from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection

from bp_engine.features.calculators import FeatureGroup, time_geometry
from bp_engine.features.hashing import canonical_hash
from bp_engine.features.models import MarketFeature
from bp_engine.features.repository import FeatureConflict, MarketFeatureRepository
from bp_engine.features.v3_calculators import (
    BTCAnchorSet,
    btc_cross_venue_group,
    btc_return_group,
)
from bp_engine.features.v3_models import (
    V3_FEATURE_VERSION,
    V3_OFFSETS_SECONDS,
    V3FeatureTarget,
)
from bp_engine.features.v3_sources import V3FeatureSourceReader


@dataclass(frozen=True)
class V3FeatureGenerationStats:
    targets_considered: int
    planned_rows: int
    inserted: int
    existing: int
    missing_group_counts: dict[str, int]


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _target_times(target: V3FeatureTarget) -> tuple[datetime, datetime]:
    start = _utc(target.market_start_at, "market_start_at")
    end = _utc(target.market_end_at, "market_end_at")
    if target.horizon_seconds != 300:
        raise ValueError("Gate A V3 targets must have horizon_seconds == 300")
    if end <= start:
        raise ValueError("market_end_at must be after market_start_at")
    if int((end - start).total_seconds()) != 300:
        raise ValueError("Gate A V3 market window must be exactly 300 seconds")
    return start, end


def plan_v3_feature_times(target: V3FeatureTarget) -> tuple[datetime, ...]:
    start, _ = _target_times(target)
    return tuple(
        start + timedelta(seconds=offset) for offset in V3_OFFSETS_SECONDS
    )


def _put_unique(target: dict[str, Any], key: str, value: Any, kind: str) -> None:
    if key in target:
        raise RuntimeError(f"duplicate {kind} key: {key}")
    target[key] = value


def _merge_groups(
    groups: Iterable[FeatureGroup],
) -> tuple[dict[str, Any], dict[str, bool], dict[str, datetime], list[dict[str, Any]]]:
    features: dict[str, Any] = {}
    missing: dict[str, bool] = {}
    cutoffs: dict[str, datetime] = {}
    observations: list[dict[str, Any]] = []
    for group in groups:
        for key, value in group.values.items():
            _put_unique(features, key, value, "feature")
        for key, value in group.missing_flags.items():
            _put_unique(missing, key, bool(value), "missing flag")
        for key, value in group.source_cutoffs.items():
            _put_unique(cutoffs, key, value, "source cutoff")
        observations.extend(group.observations)
    return features, missing, cutoffs, observations


def _target_descriptor(target: V3FeatureTarget, feature_at: datetime) -> dict[str, Any]:
    start, end = _target_times(target)
    return {
        "kind": "v3_feature_target",
        "condition_id": target.condition_id,
        "slug": target.slug,
        "horizon_seconds": target.horizon_seconds,
        "market_start_at": start,
        "market_end_at": end,
        "feature_at": feature_at,
        "feature_version": V3_FEATURE_VERSION,
    }


def _fingerprint(descriptors: Iterable[dict[str, Any]]) -> str:
    ordered = sorted(descriptors, key=canonical_hash)
    return canonical_hash(ordered)


def _serialize_cutoffs(
    cutoffs: Mapping[str, datetime], feature_at: datetime
) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in sorted(cutoffs):
        cutoff = _utc(cutoffs[key], f"source_cutoffs[{key}]")
        if cutoff > feature_at:
            raise RuntimeError(f"future source cutoff for {key}")
        result[key] = cutoff.isoformat().replace("+00:00", "Z")
    return result


def _assert_preservable_existing(
    existing: Mapping[str, Any], target: V3FeatureTarget, feature_at: datetime
) -> None:
    start, end = _target_times(target)
    expected = (
        target.slug,
        target.horizon_seconds,
        start,
        end,
        int((feature_at - start).total_seconds()),
    )
    actual = (
        str(existing["slug"]),
        int(existing["horizon_seconds"]),
        _stored_utc(existing["market_start_at"]),
        _stored_utc(existing["market_end_at"]),
        int(existing["feature_offset_seconds"]),
    )
    if actual != expected:
        raise FeatureConflict(
            "conflicting static V3 feature metadata for "
            f"condition={target.condition_id} feature_at={feature_at.isoformat()}"
        )


def _read_anchor(
    reader: V3FeatureSourceReader,
    connection: Connection,
    *,
    source: str,
    stream: str,
    instrument: str,
    requested_at: datetime,
    market_start_at: datetime,
):
    if requested_at < market_start_at:
        return None
    return reader.latest_btc_state(
        connection,
        source=source,
        stream=stream,
        instrument=instrument,
        as_of=requested_at,
    )


def _anchor_set(
    reader: V3FeatureSourceReader,
    connection: Connection,
    *,
    source: str,
    stream: str,
    instrument: str,
    market_start_at: datetime,
    feature_at: datetime,
) -> BTCAnchorSet:
    return BTCAnchorSet(
        market_start=_read_anchor(
            reader,
            connection,
            source=source,
            stream=stream,
            instrument=instrument,
            requested_at=market_start_at,
            market_start_at=market_start_at,
        ),
        trailing_120s=_read_anchor(
            reader,
            connection,
            source=source,
            stream=stream,
            instrument=instrument,
            requested_at=feature_at - timedelta(seconds=120),
            market_start_at=market_start_at,
        ),
        trailing_60s=_read_anchor(
            reader,
            connection,
            source=source,
            stream=stream,
            instrument=instrument,
            requested_at=feature_at - timedelta(seconds=60),
            market_start_at=market_start_at,
        ),
        trailing_30s=_read_anchor(
            reader,
            connection,
            source=source,
            stream=stream,
            instrument=instrument,
            requested_at=feature_at - timedelta(seconds=30),
            market_start_at=market_start_at,
        ),
        current=_read_anchor(
            reader,
            connection,
            source=source,
            stream=stream,
            instrument=instrument,
            requested_at=feature_at,
            market_start_at=market_start_at,
        ),
    )


def build_v3_feature(
    connection: Connection,
    target: V3FeatureTarget,
    feature_at: datetime,
    *,
    generated_at: datetime,
    repository: MarketFeatureRepository | None = None,
) -> MarketFeature:
    start, end = _target_times(target)
    at = _utc(feature_at, "feature_at")
    generated = _utc(generated_at, "generated_at")
    if not start < at < end:
        raise ValueError("feature_at must be strictly inside market window")
    if at not in plan_v3_feature_times(target):
        raise ValueError("feature_at must be one of the fixed Gate A V3 offsets")

    reader = V3FeatureSourceReader()
    coinbase = _anchor_set(
        reader,
        connection,
        source="coinbase",
        stream="spot",
        instrument="BTC-USD",
        market_start_at=start,
        feature_at=at,
    )
    bybit_spot = _anchor_set(
        reader,
        connection,
        source="bybit",
        stream="spot",
        instrument="BTCUSDT",
        market_start_at=start,
        feature_at=at,
    )
    bybit_linear = _anchor_set(
        reader,
        connection,
        source="bybit",
        stream="linear",
        instrument="BTCUSDT",
        market_start_at=start,
        feature_at=at,
    )

    groups = (
        time_geometry(target, at),
        btc_return_group("coinbase", coinbase),
        btc_return_group("bybit_spot", bybit_spot),
        btc_return_group("bybit_linear", bybit_linear),
        btc_cross_venue_group(coinbase, bybit_spot, bybit_linear),
    )
    features, missing, cutoffs, observations = _merge_groups(groups)
    serialized_cutoffs = _serialize_cutoffs(cutoffs, at)
    descriptors = [_target_descriptor(target, at), *observations]
    feature = MarketFeature(
        condition_id=target.condition_id,
        slug=target.slug,
        horizon_seconds=target.horizon_seconds,
        market_start_at=start,
        market_end_at=end,
        feature_at=at,
        feature_offset_seconds=int((at - start).total_seconds()),
        feature_version=V3_FEATURE_VERSION,
        features=features,
        missing_flags=missing,
        source_cutoffs=serialized_cutoffs,
        input_fingerprint=_fingerprint(descriptors),
        feature_hash=canonical_hash({"features": features, "missing_flags": missing}),
        generated_at=generated,
    )
    if repository is not None:
        repository.store(connection, feature)
    return feature


def generate_v3_features(
    connection: Connection,
    targets: Iterable[V3FeatureTarget],
    *,
    generated_at: datetime,
    preserve_existing: bool = False,
) -> V3FeatureGenerationStats:
    generated = _utc(generated_at, "generated_at")
    repository = MarketFeatureRepository()
    target_list = list(targets)
    inserted = 0
    existing = 0
    planned_rows = 0
    missing_counts: dict[str, int] = {}

    for target in target_list:
        times = plan_v3_feature_times(target)
        planned_rows += len(times)
        for at in times:
            if preserve_existing:
                frozen = repository.find(
                    connection,
                    condition_id=target.condition_id,
                    feature_at=at,
                    feature_version=V3_FEATURE_VERSION,
                )
                if frozen is not None:
                    _assert_preservable_existing(frozen, target, at)
                    existing += 1
                    continue

            feature = build_v3_feature(
                connection,
                target,
                at,
                generated_at=generated,
            )
            result = repository.store(connection, feature)
            if result.created:
                inserted += 1
            else:
                existing += 1
            for key, value in feature.missing_flags.items():
                if value:
                    missing_counts[key] = missing_counts.get(key, 0) + 1

    return V3FeatureGenerationStats(
        targets_considered=len(target_list),
        planned_rows=planned_rows,
        inserted=inserted,
        existing=existing,
        missing_group_counts=dict(sorted(missing_counts.items())),
    )
