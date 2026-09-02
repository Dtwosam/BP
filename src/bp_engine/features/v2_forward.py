from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, select

from bp_engine.features.v2_coverage import build_v2_coverage_report
from bp_engine.features.v2_models import V2_FEATURE_VERSION, V2FeatureTarget
from bp_engine.features.v2_service import generate_v2_features
from bp_engine.storage.schema import market_features, polymarket_markets

V2_FORWARD_EPOCH = datetime(2026, 9, 2, 12, 18, 2, tzinfo=UTC)
V2_FORWARD_END_GRACE_SECONDS = 15
_EXPECTED_OFFSETS = frozenset({60, 120, 180, 240})


@dataclass(frozen=True)
class V2ForwardCycleStats:
    cycle_at: datetime
    eligible_targets: int
    inserted: int
    existing: int
    planned_rows: int
    coverage_row_count: int
    coverage_market_count: int
    future_cutoff_violation_count: int
    policy_selected: bool
    automatic_promotion: bool


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def discover_pending_v2_targets(
    connection: Connection,
    *,
    cycle_at: datetime,
    epoch: datetime = V2_FORWARD_EPOCH,
    end_grace_seconds: int = V2_FORWARD_END_GRACE_SECONDS,
) -> tuple[V2FeatureTarget, ...]:
    cycle = _utc(cycle_at, "cycle_at")
    forward_epoch = _utc(epoch, "epoch")
    if end_grace_seconds < 0:
        raise ValueError("end_grace_seconds must be non-negative")
    completed_cutoff = cycle - timedelta(seconds=end_grace_seconds)

    market_rows = connection.execute(
        select(
            polymarket_markets.c.condition_id,
            polymarket_markets.c.slug,
            polymarket_markets.c.horizon_seconds,
            polymarket_markets.c.start_at,
            polymarket_markets.c.end_at,
            polymarket_markets.c.up_token_id,
            polymarket_markets.c.down_token_id,
        )
        .where(
            polymarket_markets.c.horizon_seconds == 300,
            polymarket_markets.c.start_at >= forward_epoch,
            polymarket_markets.c.end_at <= completed_cutoff,
        )
        .order_by(polymarket_markets.c.start_at, polymarket_markets.c.condition_id)
    ).mappings().all()
    if not market_rows:
        return ()

    condition_ids = tuple(str(row["condition_id"]) for row in market_rows)
    feature_rows = connection.execute(
        select(
            market_features.c.condition_id,
            market_features.c.feature_offset_seconds,
        ).where(
            market_features.c.feature_version == V2_FEATURE_VERSION,
            market_features.c.condition_id.in_(condition_ids),
        )
    ).mappings().all()

    offsets_by_condition: dict[str, set[int]] = {
        condition_id: set() for condition_id in condition_ids
    }
    for row in feature_rows:
        condition_id = str(row["condition_id"])
        offset = int(row["feature_offset_seconds"])
        if offset not in _EXPECTED_OFFSETS:
            raise RuntimeError(
                "unexpected V2 forward feature offset "
                f"condition={condition_id} offset={offset}"
            )
        offsets_by_condition[condition_id].add(offset)

    pending: list[V2FeatureTarget] = []
    for row in market_rows:
        condition_id = str(row["condition_id"])
        if offsets_by_condition[condition_id] == _EXPECTED_OFFSETS:
            continue
        pending.append(
            V2FeatureTarget(
                condition_id=condition_id,
                slug=str(row["slug"]),
                horizon_seconds=int(row["horizon_seconds"]),
                market_start_at=_stored_utc(row["start_at"]),
                market_end_at=_stored_utc(row["end_at"]),
                up_token_id=str(row["up_token_id"]),
                down_token_id=str(row["down_token_id"]),
            )
        )
    return tuple(pending)


def run_v2_forward_cycle(
    connection: Connection,
    *,
    cycle_at: datetime,
    epoch: datetime = V2_FORWARD_EPOCH,
    end_grace_seconds: int = V2_FORWARD_END_GRACE_SECONDS,
) -> V2ForwardCycleStats:
    cycle = _utc(cycle_at, "cycle_at")
    targets = discover_pending_v2_targets(
        connection,
        cycle_at=cycle,
        epoch=epoch,
        end_grace_seconds=end_grace_seconds,
    )
    generation = generate_v2_features(
        connection,
        targets,
        generated_at=cycle,
        preserve_existing=True,
    )
    coverage = build_v2_coverage_report(connection)

    future_cutoffs = int(coverage["future_cutoff_violation_count"])
    policy_selected = coverage["policy_selected"]
    automatic_promotion = coverage["automatic_promotion"]
    if future_cutoffs != 0 or policy_selected is not False or automatic_promotion is not False:
        raise RuntimeError("V2 forward coverage invariant violation")

    return V2ForwardCycleStats(
        cycle_at=cycle,
        eligible_targets=len(targets),
        inserted=generation.inserted,
        existing=generation.existing,
        planned_rows=generation.planned_rows,
        coverage_row_count=int(coverage["row_count"]),
        coverage_market_count=int(coverage["market_count"]),
        future_cutoff_violation_count=future_cutoffs,
        policy_selected=False,
        automatic_promotion=False,
    )
