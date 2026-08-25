from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection

from bp_engine.features.calculators import (
    FeatureGroup,
    book_state,
    bybit_state,
    coinbase_candles,
    official_reference,
    polymarket_prices,
    time_geometry,
)
from bp_engine.features.exclusions import raw_window_exclusion
from bp_engine.features.hashing import canonical_hash
from bp_engine.features.models import FEATURE_VERSION, FeatureTarget, MarketFeature
from bp_engine.features.repository import FeatureConflict, MarketFeatureRepository
from bp_engine.features.sources import FeatureSourceReader
from bp_engine.features.trade_flow import TradeFlow, load_trade_flow


@dataclass(frozen=True)
class FeatureGenerationStats:
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


def _target_times(target: FeatureTarget) -> tuple[datetime, datetime]:
    start = _utc(target.market_start_at, "market_start_at")
    end = _utc(target.market_end_at, "market_end_at")
    if target.horizon_seconds <= 0:
        raise ValueError("horizon_seconds must be positive")
    if end <= start:
        raise ValueError("market_end_at must be after market_start_at")
    actual_horizon = int((end - start).total_seconds())
    if actual_horizon != target.horizon_seconds:
        raise ValueError("target horizon_seconds must match market window")
    return start, end


def _assert_preservable_existing(
    existing: Mapping[str, Any], target: FeatureTarget, feature_at: datetime
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
            "conflicting static feature metadata for "
            f"condition={target.condition_id} "
            f"feature_at={feature_at.isoformat()} "
            f"version={FEATURE_VERSION}"
        )


def plan_feature_times(
    target: FeatureTarget, *, step_seconds: int = 60
) -> tuple[datetime, ...]:
    start, end = _target_times(target)
    if step_seconds <= 0:
        raise ValueError("step_seconds must be positive")
    result: list[datetime] = []
    feature_at = start + timedelta(seconds=step_seconds)
    while feature_at < end:
        result.append(feature_at)
        feature_at += timedelta(seconds=step_seconds)
    return tuple(result)


def _put_unique(target: dict[str, Any], key: str, value: Any, kind: str) -> None:
    if key in target and target[key] != value:
        raise RuntimeError(f"conflicting {kind} key: {key}")
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


def _trade_group(prefix: str, flow: TradeFlow | None) -> FeatureGroup:
    values: dict[str, Any] = {
        f"{prefix}_buy_volume": None,
        f"{prefix}_sell_volume": None,
        f"{prefix}_signed_volume": None,
        f"{prefix}_trade_count": None,
    }
    missing = {f"{prefix}_missing": flow is None}
    if flow is None:
        return FeatureGroup(values, missing, {}, ())
    values.update(
        {
            f"{prefix}_buy_volume": float(flow.buy_volume),
            f"{prefix}_sell_volume": float(flow.sell_volume),
            f"{prefix}_signed_volume": float(flow.signed_volume),
            f"{prefix}_trade_count": flow.trade_count,
        }
    )
    observations = (flow.coverage_observation, *flow.observations)
    return FeatureGroup(
        values=values,
        missing_flags=missing,
        source_cutoffs={prefix: flow.coverage_cutoff},
        observations=observations,
    )


def _raw_trade_groups(
    connection: Connection,
    target: FeatureTarget,
    feature_at: datetime,
) -> tuple[FeatureGroup, ...]:
    left = feature_at - timedelta(seconds=60)
    exclusion = raw_window_exclusion(
        left + timedelta(microseconds=1),
        feature_at + timedelta(microseconds=1),
    )
    if exclusion is not None:
        global_group = FeatureGroup(
            values={},
            missing_flags={
                "raw_trade_flow_missing": True,
                "raw_trade_flow_excluded": True,
            },
            source_cutoffs={},
            observations=(),
        )
        return (
            global_group,
            _trade_group("polymarket_trade_flow", None),
            _trade_group("coinbase_trade_flow", None),
            _trade_group("bybit_spot_trade_flow", None),
            _trade_group("bybit_linear_trade_flow", None),
        )

    flows = (
        (
            "polymarket_trade_flow",
            load_trade_flow(
                connection,
                source="polymarket",
                stream="market",
                instrument=target.condition_id,
                feature_at=feature_at,
            ),
        ),
        (
            "coinbase_trade_flow",
            load_trade_flow(
                connection,
                source="coinbase",
                stream="spot",
                instrument="BTC-USD",
                feature_at=feature_at,
            ),
        ),
        (
            "bybit_spot_trade_flow",
            load_trade_flow(
                connection,
                source="bybit",
                stream="spot",
                instrument="BTCUSDT",
                feature_at=feature_at,
            ),
        ),
        (
            "bybit_linear_trade_flow",
            load_trade_flow(
                connection,
                source="bybit",
                stream="linear",
                instrument="BTCUSDT",
                feature_at=feature_at,
            ),
        ),
    )
    global_group = FeatureGroup(
        values={},
        missing_flags={
            "raw_trade_flow_missing": any(flow is None for _, flow in flows),
            "raw_trade_flow_excluded": False,
        },
        source_cutoffs={},
        observations=(),
    )
    return (global_group, *(_trade_group(prefix, flow) for prefix, flow in flows))


def _target_descriptor(target: FeatureTarget, feature_at: datetime) -> dict[str, Any]:
    return {
        "kind": "feature_target",
        "condition_id": target.condition_id,
        "slug": target.slug,
        "horizon_seconds": target.horizon_seconds,
        "market_start_at": target.market_start_at,
        "market_end_at": target.market_end_at,
        "feature_at": feature_at,
        "feature_version": FEATURE_VERSION,
    }


def _fingerprint(descriptors: Iterable[dict[str, Any]]) -> str:
    ordered = sorted(descriptors, key=canonical_hash)
    return canonical_hash(ordered)


def _serialize_cutoffs(
    cutoffs: dict[str, datetime], feature_at: datetime
) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in sorted(cutoffs):
        cutoff = _utc(cutoffs[key], f"source_cutoffs[{key}]")
        if cutoff > feature_at:
            raise RuntimeError(f"future source cutoff for {key}")
        result[key] = cutoff.isoformat().replace("+00:00", "Z")
    return result


def build_feature(
    connection: Connection,
    target: FeatureTarget,
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

    reader = FeatureSourceReader()
    up_price = reader.latest_polymarket_price(
        connection,
        condition_id=target.condition_id,
        outcome="Up",
        feature_at=at,
    )
    down_price = reader.latest_polymarket_price(
        connection,
        condition_id=target.condition_id,
        outcome="Down",
        feature_at=at,
    )
    up_state = (
        reader.latest_state(
            connection,
            source="polymarket",
            stream="market",
            instrument=target.condition_id,
            asset_id=up_price.asset_id,
            feature_at=at,
        )
        if up_price is not None
        else None
    )
    down_state = (
        reader.latest_state(
            connection,
            source="polymarket",
            stream="market",
            instrument=target.condition_id,
            asset_id=down_price.asset_id,
            feature_at=at,
        )
        if down_price is not None
        else None
    )
    candles = reader.closed_candles(
        connection,
        source="coinbase",
        market_type="spot",
        symbol="BTC-USD",
        interval_seconds=60,
        feature_at=at,
        limit=32,
    )
    bybit_spot = reader.latest_state(
        connection,
        source="bybit",
        stream="spot",
        instrument="BTCUSDT",
        feature_at=at,
    )
    bybit_linear = reader.latest_state(
        connection,
        source="bybit",
        stream="linear",
        instrument="BTCUSDT",
        feature_at=at,
    )

    groups: Sequence[FeatureGroup] = (
        time_geometry(target, at),
        polymarket_prices(up_price, down_price, at),
        book_state("pm_up", up_state),
        book_state("pm_down", down_state),
        coinbase_candles(candles, start),
        bybit_state(bybit_spot, bybit_linear),
        official_reference(),
        *_raw_trade_groups(connection, target, at),
    )
    features, missing, cutoffs, observations = _merge_groups(groups)
    serialized_cutoffs = _serialize_cutoffs(cutoffs, at)
    descriptors = [_target_descriptor(target, at), *observations]
    input_fingerprint = _fingerprint(descriptors)
    feature_hash = canonical_hash({"features": features, "missing_flags": missing})

    feature = MarketFeature(
        condition_id=target.condition_id,
        slug=target.slug,
        horizon_seconds=target.horizon_seconds,
        market_start_at=start,
        market_end_at=end,
        feature_at=at,
        feature_offset_seconds=int((at - start).total_seconds()),
        feature_version=FEATURE_VERSION,
        features=features,
        missing_flags=missing,
        source_cutoffs=serialized_cutoffs,
        input_fingerprint=input_fingerprint,
        feature_hash=feature_hash,
        generated_at=generated,
    )
    if repository is not None:
        repository.store(connection, feature)
    return feature


def generate_features(
    connection: Connection,
    targets: Iterable[FeatureTarget],
    *,
    generated_at: datetime,
    step_seconds: int = 60,
    preserve_existing: bool = False,
) -> FeatureGenerationStats:
    generated = _utc(generated_at, "generated_at")
    repository = MarketFeatureRepository()
    target_list = list(targets)
    inserted = 0
    existing = 0
    planned_rows = 0
    missing_counts: dict[str, int] = {}

    for target in target_list:
        times = plan_feature_times(target, step_seconds=step_seconds)
        planned_rows += len(times)
        for feature_at in times:
            if preserve_existing:
                frozen = repository.find(
                    connection,
                    condition_id=target.condition_id,
                    feature_at=feature_at,
                    feature_version=FEATURE_VERSION,
                )
                if frozen is not None:
                    _assert_preservable_existing(frozen, target, feature_at)
                    existing += 1
                    continue

            feature = build_feature(
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

    return FeatureGenerationStats(
        targets_considered=len(target_list),
        planned_rows=planned_rows,
        inserted=inserted,
        existing=existing,
        missing_group_counts=dict(sorted(missing_counts.items())),
    )
