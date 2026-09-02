from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection

from bp_engine.features.calculators import FeatureGroup, book_state, time_geometry
from bp_engine.features.hashing import canonical_hash
from bp_engine.features.models import MarketFeature
from bp_engine.features.repository import FeatureConflict, MarketFeatureRepository
from bp_engine.features.sources import FeatureSourceReader
from bp_engine.features.v2_calculators import last_trade_features
from bp_engine.features.v2_models import V2_FEATURE_VERSION, V2FeatureTarget
from bp_engine.features.v2_sources import V2FeatureSourceReader


@dataclass(frozen=True)
class V2FeatureGenerationStats:
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


def _target_times(target: V2FeatureTarget) -> tuple[datetime, datetime]:
    start = _utc(target.market_start_at, "market_start_at")
    end = _utc(target.market_end_at, "market_end_at")
    if target.horizon_seconds != 300:
        raise ValueError("Gate A V2 targets must have horizon_seconds == 300")
    if end <= start:
        raise ValueError("market_end_at must be after market_start_at")
    if int((end - start).total_seconds()) != 300:
        raise ValueError("Gate A V2 market window must be exactly 300 seconds")
    if not target.up_token_id or not target.down_token_id:
        raise ValueError("V2 targets require exact Up and Down token IDs")
    return start, end


def plan_v2_feature_times(target: V2FeatureTarget) -> tuple[datetime, ...]:
    start, _ = _target_times(target)
    return tuple(start + timedelta(seconds=offset) for offset in (60, 120, 180, 240))


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


def _target_descriptor(target: V2FeatureTarget, feature_at: datetime) -> dict[str, Any]:
    start, end = _target_times(target)
    return {
        "kind": "v2_feature_target",
        "condition_id": target.condition_id,
        "slug": target.slug,
        "horizon_seconds": target.horizon_seconds,
        "market_start_at": start,
        "market_end_at": end,
        "feature_at": feature_at,
        "feature_version": V2_FEATURE_VERSION,
        "up_token_id": target.up_token_id,
        "down_token_id": target.down_token_id,
    }


def _fingerprint(descriptors: Iterable[dict[str, Any]]) -> str:
    ordered = sorted(descriptors, key=canonical_hash)
    return canonical_hash(ordered)


def _serialize_cutoffs(cutoffs: Mapping[str, datetime], feature_at: datetime) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in sorted(cutoffs):
        cutoff = _utc(cutoffs[key], f"source_cutoffs[{key}]")
        if cutoff > feature_at:
            raise RuntimeError(f"future source cutoff for {key}")
        result[key] = cutoff.isoformat().replace("+00:00", "Z")
    return result


def _assert_preservable_existing(
    existing: Mapping[str, Any], target: V2FeatureTarget, feature_at: datetime
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
            "conflicting static V2 feature metadata for "
            f"condition={target.condition_id} feature_at={feature_at.isoformat()}"
        )


def build_v2_feature(
    connection: Connection,
    target: V2FeatureTarget,
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
    if at not in plan_v2_feature_times(target):
        raise ValueError("feature_at must be one of the fixed Gate A V2 offsets")

    trade_reader = V2FeatureSourceReader()
    state_reader = FeatureSourceReader()
    up_trade = trade_reader.latest_polymarket_last_trade(
        connection,
        condition_id=target.condition_id,
        asset_id=target.up_token_id,
        feature_at=at,
    )
    down_trade = trade_reader.latest_polymarket_last_trade(
        connection,
        condition_id=target.condition_id,
        asset_id=target.down_token_id,
        feature_at=at,
    )
    up_state = state_reader.latest_state(
        connection,
        source="polymarket",
        stream="market",
        instrument=target.condition_id,
        asset_id=target.up_token_id,
        feature_at=at,
    )
    down_state = state_reader.latest_state(
        connection,
        source="polymarket",
        stream="market",
        instrument=target.condition_id,
        asset_id=target.down_token_id,
        feature_at=at,
    )

    groups = (
        time_geometry(target, at),
        last_trade_features("pm_up", up_trade, at),
        last_trade_features("pm_down", down_trade, at),
        book_state("pm_up", up_state),
        book_state("pm_down", down_state),
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
        feature_version=V2_FEATURE_VERSION,
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


def generate_v2_features(
    connection: Connection,
    targets: Iterable[V2FeatureTarget],
    *,
    generated_at: datetime,
    preserve_existing: bool = False,
) -> V2FeatureGenerationStats:
    generated = _utc(generated_at, "generated_at")
    repository = MarketFeatureRepository()
    target_list = list(targets)
    inserted = 0
    existing = 0
    planned_rows = 0
    missing_counts: dict[str, int] = {}

    for target in target_list:
        times = plan_v2_feature_times(target)
        planned_rows += len(times)
        for feature_at in times:
            if preserve_existing:
                frozen = repository.find(
                    connection,
                    condition_id=target.condition_id,
                    feature_at=feature_at,
                    feature_version=V2_FEATURE_VERSION,
                )
                if frozen is not None:
                    _assert_preservable_existing(frozen, target, feature_at)
                    existing += 1
                    continue

            feature = build_v2_feature(
                connection,
                target,
                feature_at,
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

    return V2FeatureGenerationStats(
        targets_considered=len(target_list),
        planned_rows=planned_rows,
        inserted=inserted,
        existing=existing,
        missing_group_counts=dict(sorted(missing_counts.items())),
    )
