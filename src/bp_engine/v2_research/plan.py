from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, select

from bp_engine.features.hashing import canonical_hash
from bp_engine.storage.schema import market_features
from bp_engine.v2_research.config import (
    EXPECTED_OFFSETS_SECONDS,
    FROZEN_COVERAGE_INPUT_SHA256,
    FROZEN_FRESHNESS_CANDIDATES_SECONDS,
    FROZEN_INCLUDE_NO_TRADE,
    V2_FEATURE_VERSION,
    V2_GATE_B_VERSION,
)
from bp_engine.v2_research.models import GateBPlanConfig, GateBResearchConfig


class GateBPlanIntegrityError(RuntimeError):
    """Raised when unlabeled V2 feature evidence cannot form a safe Gate B plan."""


REQUIRED_ORDINARY_FOLDS = 3


def _minimum_contiguous_epoch_seconds(config: GateBPlanConfig) -> float:
    return (
        config.train_duration
        + config.validation_duration
        + config.test_duration
        + ((REQUIRED_ORDINARY_FOLDS - 1) * config.step_duration)
        + config.final_holdout_duration
    ).total_seconds()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _partition(
    name: str,
    start: datetime,
    end: datetime,
    records: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    return {
        "name": name,
        "start": _utc(start).isoformat(),
        "end": _utc(end).isoformat(),
        "condition_ids": [record["condition_id"] for record in records],
    }


def _feature_timeline(connection: Connection) -> tuple[dict[str, Any], ...]:
    records = connection.execute(
        select(
            market_features.c.condition_id,
            market_features.c.slug,
            market_features.c.horizon_seconds,
            market_features.c.market_start_at,
            market_features.c.market_end_at,
            market_features.c.feature_offset_seconds,
        )
        .where(
            market_features.c.feature_version == V2_FEATURE_VERSION,
            market_features.c.horizon_seconds == 300,
        )
        .order_by(
            market_features.c.market_start_at,
            market_features.c.condition_id,
            market_features.c.feature_offset_seconds,
        )
    ).mappings().all()
    if not records:
        raise GateBPlanIntegrityError("no V2 feature rows available")

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record["condition_id"])].append(dict(record))

    timeline: list[dict[str, Any]] = []
    for condition_id, rows in grouped.items():
        first = rows[0]
        offsets = tuple(int(row["feature_offset_seconds"]) for row in rows)
        if offsets != EXPECTED_OFFSETS_SECONDS:
            raise GateBPlanIntegrityError(
                f"{condition_id} must have exactly offsets {EXPECTED_OFFSETS_SECONDS}"
            )
        expected = (
            first["slug"],
            int(first["horizon_seconds"]),
            _utc(first["market_start_at"]),
            _utc(first["market_end_at"]),
        )
        for row in rows[1:]:
            observed = (
                row["slug"],
                int(row["horizon_seconds"]),
                _utc(row["market_start_at"]),
                _utc(row["market_end_at"]),
            )
            if observed != expected:
                raise GateBPlanIntegrityError(
                    f"static market metadata mismatch for {condition_id}"
                )
        market_start = _utc(first["market_start_at"])
        market_end = _utc(first["market_end_at"])
        if market_end <= market_start:
            raise GateBPlanIntegrityError(
                f"market_end_at must follow market_start_at for {condition_id}"
            )
        timeline.append(
            {
                "condition_id": condition_id,
                "slug": str(first["slug"]),
                "horizon_seconds": int(first["horizon_seconds"]),
                "market_start_at": market_start,
                "market_end_at": market_end,
            }
        )
    return tuple(
        sorted(timeline, key=lambda item: (item["market_start_at"], item["condition_id"]))
    )


def _planning_timeline(
    connection: Connection,
    planning_epoch_start: datetime | None,
) -> tuple[tuple[dict[str, Any], ...], datetime | None]:
    boundary: datetime | None = None
    if planning_epoch_start is not None:
        if (
            planning_epoch_start.tzinfo is None
            or planning_epoch_start.utcoffset() is None
        ):
            raise ValueError("planning_epoch_start must be timezone-aware")
        boundary = planning_epoch_start.astimezone(UTC)

    timeline = _feature_timeline(connection)
    if boundary is None:
        return timeline, None

    filtered = tuple(
        market for market in timeline if market["market_start_at"] >= boundary
    )
    if not filtered:
        raise GateBPlanIntegrityError(
            "no V2 feature rows available at or after planning_epoch_start"
        )
    return filtered, boundary


def _contained(
    timeline: tuple[dict[str, Any], ...],
    start: datetime,
    end: datetime,
) -> tuple[dict[str, Any], ...]:
    return tuple(
        market
        for market in timeline
        if market["market_start_at"] >= start and market["market_end_at"] <= end
    )


def _crosses_boundary(market: dict[str, Any], boundary: datetime) -> bool:
    return market["market_start_at"] < boundary < market["market_end_at"]


def _purged_at_boundaries(
    timeline: tuple[dict[str, Any], ...],
    boundaries: tuple[datetime, ...],
) -> tuple[str, ...]:
    return tuple(
        market["condition_id"]
        for market in timeline
        if any(_crosses_boundary(market, boundary) for boundary in boundaries)
    )


def _embargo_earlier_partition(
    records: tuple[dict[str, Any], ...], count: int
) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
    if count == 0:
        return records, ()
    if len(records) <= count:
        return (), tuple(record["condition_id"] for record in records)
    removed = records[-count:]
    return records[:-count], tuple(record["condition_id"] for record in removed)


def _require_count(name: str, records: tuple[dict[str, Any], ...], minimum: int) -> None:
    if len(records) < minimum:
        raise GateBPlanIntegrityError(
            f"{name} requires at least {minimum} markets; found {len(records)}"
        )


def _config_payload(config: GateBPlanConfig) -> dict[str, float | int]:
    return {
        "train_duration_seconds": config.train_duration.total_seconds(),
        "validation_duration_seconds": config.validation_duration.total_seconds(),
        "test_duration_seconds": config.test_duration.total_seconds(),
        "step_duration_seconds": config.step_duration.total_seconds(),
        "final_holdout_duration_seconds": config.final_holdout_duration.total_seconds(),
        "embargo_markets": config.embargo_markets,
        "min_train_markets": config.min_train_markets,
        "min_validation_markets": config.min_validation_markets,
        "min_test_markets": config.min_test_markets,
    }


def _build_gate_b_plan_from_start(
    timeline: tuple[dict[str, Any], ...],
    *,
    analysis_start: datetime,
    dataset_end: datetime,
    holdout_start: datetime,
    config: GateBPlanConfig,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    folds: list[dict[str, Any]] = []
    seen_test: set[str] = set()
    fold_start = analysis_start
    index = 0
    while True:
        train_start = fold_start
        train_end = train_start + config.train_duration
        validation_start = train_end
        validation_end = validation_start + config.validation_duration
        test_start = validation_end
        test_end = test_start + config.test_duration
        if test_end > holdout_start:
            break

        train_records = _contained(timeline, train_start, train_end)
        validation_records = _contained(timeline, validation_start, validation_end)
        test_records = _contained(timeline, test_start, test_end)
        purged = _purged_at_boundaries(
            timeline,
            (train_start, train_end, validation_end, test_end),
        )
        train_records, train_embargo = _embargo_earlier_partition(
            train_records, config.embargo_markets
        )
        validation_records, validation_embargo = _embargo_earlier_partition(
            validation_records, config.embargo_markets
        )
        _require_count("train", train_records, config.min_train_markets)
        _require_count(
            "validation", validation_records, config.min_validation_markets
        )
        _require_count("test", test_records, config.min_test_markets)

        test_ids = {record["condition_id"] for record in test_records}
        overlap = seen_test.intersection(test_ids)
        if overlap:
            raise GateBPlanIntegrityError(
                f"ordinary test market reused: {sorted(overlap)[0]}"
            )
        seen_test.update(test_ids)
        membership = {
            "index": index,
            "train": _partition("train", train_start, train_end, train_records),
            "validation": _partition(
                "validation",
                validation_start,
                validation_end,
                validation_records,
            ),
            "test": _partition("test", test_start, test_end, test_records),
            "purged_condition_ids": list(purged),
            "embargo_condition_ids": list(train_embargo + validation_embargo),
        }
        membership["membership_sha256"] = canonical_hash(membership)
        folds.append(membership)
        index += 1
        fold_start = fold_start + config.step_duration

    if len(folds) < REQUIRED_ORDINARY_FOLDS:
        raise GateBPlanIntegrityError(
            "Gate B plan requires at least "
            f"{REQUIRED_ORDINARY_FOLDS} eligible folds; found {len(folds)}"
        )

    final_holdout_start = holdout_start
    final_validation_end = final_holdout_start
    final_validation_start = final_validation_end - config.validation_duration
    final_train_end = final_validation_start
    final_train_start = final_train_end - config.train_duration
    if final_train_start < analysis_start:
        raise GateBPlanIntegrityError(
            "final train window begins before candidate analysis start"
        )

    final_train_records = _contained(timeline, final_train_start, final_train_end)
    final_validation_records = _contained(
        timeline, final_validation_start, final_validation_end
    )
    final_holdout_records = _contained(timeline, final_holdout_start, dataset_end)
    final_purged = _purged_at_boundaries(
        timeline,
        (
            final_train_start,
            final_train_end,
            final_validation_end,
            dataset_end,
        ),
    )
    final_train_records, final_train_embargo = _embargo_earlier_partition(
        final_train_records, config.embargo_markets
    )
    final_validation_records, final_validation_embargo = _embargo_earlier_partition(
        final_validation_records, config.embargo_markets
    )
    _require_count("final train", final_train_records, config.min_train_markets)
    _require_count(
        "final validation",
        final_validation_records,
        config.min_validation_markets,
    )
    _require_count("final holdout", final_holdout_records, config.min_test_markets)

    holdout_ids = {record["condition_id"] for record in final_holdout_records}
    overlap = seen_test.intersection(holdout_ids)
    if overlap:
        raise GateBPlanIntegrityError(
            f"final holdout overlaps ordinary test markets: {sorted(overlap)[0]}"
        )

    final = {
        "train": _partition(
            "final_train", final_train_start, final_train_end, final_train_records
        ),
        "validation": _partition(
            "final_validation",
            final_validation_start,
            final_validation_end,
            final_validation_records,
        ),
        "holdout": _partition(
            "final_holdout",
            final_holdout_start,
            dataset_end,
            final_holdout_records,
        ),
        "train_condition_ids": [
            record["condition_id"] for record in final_train_records
        ],
        "validation_condition_ids": [
            record["condition_id"] for record in final_validation_records
        ],
        "holdout_condition_ids": [
            record["condition_id"] for record in final_holdout_records
        ],
        "purged_condition_ids": list(final_purged),
        "embargo_condition_ids": list(
            final_train_embargo + final_validation_embargo
        ),
    }
    final["membership_sha256"] = canonical_hash(final)
    return folds, final


def assess_gate_b_readiness(
    connection: Connection,
    config: GateBPlanConfig | None = None,
    research_config: GateBResearchConfig | None = None,
    *,
    planning_epoch_start: datetime | None = None,
) -> dict[str, Any]:
    """Return feature-only readiness without writing or freezing Gate B artifacts."""
    config = config or GateBPlanConfig()
    research_config = research_config or GateBResearchConfig()
    timeline, planning_boundary = _planning_timeline(connection, planning_epoch_start)
    dataset_start = timeline[0]["market_start_at"]
    dataset_end = timeline[-1]["market_end_at"]
    holdout_start = dataset_end - config.final_holdout_duration
    minimum_seconds = _minimum_contiguous_epoch_seconds(config)

    base: dict[str, Any] = {
        "feature_version": V2_FEATURE_VERSION,
        "horizon_seconds": 300,
        "market_count": len(timeline),
        "market_start_at": dataset_start.isoformat(),
        "market_end_at": dataset_end.isoformat(),
        "planning_epoch_start_at": (
            planning_boundary.isoformat() if planning_boundary is not None else None
        ),
        "available_span_seconds": (dataset_end - dataset_start).total_seconds(),
        "minimum_contiguous_epoch_seconds": minimum_seconds,
        "required_ordinary_folds": REQUIRED_ORDINARY_FOLDS,
        "labels_read": False,
        "plan_artifact_written": False,
        "selection_artifact_written": False,
        "holdout_touched": False,
        "coverage_input_sha256": FROZEN_COVERAGE_INPUT_SHA256,
        "freshness_candidates_seconds": list(FROZEN_FRESHNESS_CANDIDATES_SECONDS),
        "include_no_trade": FROZEN_INCLUDE_NO_TRADE,
        "config": _config_payload(config),
        "research_config": {
            "fee_rate": research_config.fee_rate,
            "slippage_buffer": research_config.slippage_buffer,
            "min_edge_grid": list(research_config.min_edge_grid),
            "min_validation_trades": research_config.min_validation_trades,
            "min_train_eligible_markets": research_config.min_train_eligible_markets,
            "min_validation_eligible_markets": (
                research_config.min_validation_eligible_markets
            ),
        },
    }

    if holdout_start <= dataset_start:
        return {
            **base,
            "ready": False,
            "analysis_start_at": None,
            "eligible_fold_count": 0,
            "final_holdout_market_count": 0,
            "analysis_start_attempt_count": 0,
            "candidate_rejections": [],
            "blocking_reason": "final holdout leaves no Gate B history",
            "would_plan_sha256": None,
        }

    minimum_span = (
        config.train_duration
        + config.validation_duration
        + config.test_duration
    )
    candidate_start = dataset_start
    attempt_count = 0
    rejections: list[dict[str, str]] = []

    while candidate_start + minimum_span <= holdout_start:
        attempt_count += 1
        try:
            folds, final = _build_gate_b_plan_from_start(
                timeline,
                analysis_start=candidate_start,
                dataset_end=dataset_end,
                holdout_start=holdout_start,
                config=config,
            )
        except GateBPlanIntegrityError as exc:
            rejections.append(
                {
                    "analysis_start_at": candidate_start.isoformat(),
                    "reason": str(exc),
                }
            )
            candidate_start = candidate_start + config.step_duration
            continue

        plan = build_gate_b_plan(
            connection,
            config,
            research_config,
            planning_epoch_start=planning_boundary,
        )
        return {
            **base,
            "ready": True,
            "analysis_start_at": candidate_start.isoformat(),
            "eligible_fold_count": len(folds),
            "final_holdout_market_count": len(final["holdout_condition_ids"]),
            "analysis_start_attempt_count": attempt_count,
            "candidate_rejections": rejections,
            "blocking_reason": None,
            "would_plan_sha256": plan["plan_sha256"],
        }

    last_reason = (
        rejections[-1]["reason"]
        if rejections
        else "no candidate analysis start was evaluable"
    )
    return {
        **base,
        "ready": False,
        "analysis_start_at": None,
        "eligible_fold_count": 0,
        "final_holdout_market_count": 0,
        "analysis_start_attempt_count": attempt_count,
        "candidate_rejections": rejections,
        "blocking_reason": (
            "no contiguous Gate B epoch satisfies the frozen walk-forward "
            f"minimums; last rejection: {last_reason}"
        ),
        "would_plan_sha256": None,
    }


def build_gate_b_plan(
    connection: Connection,
    config: GateBPlanConfig | None = None,
    research_config: GateBResearchConfig | None = None,
    *,
    planning_epoch_start: datetime | None = None,
) -> dict[str, Any]:
    config = config or GateBPlanConfig()
    research_config = research_config or GateBResearchConfig()
    timeline, planning_boundary = _planning_timeline(connection, planning_epoch_start)
    dataset_start = timeline[0]["market_start_at"]
    dataset_end = timeline[-1]["market_end_at"]
    holdout_start = dataset_end - config.final_holdout_duration
    if holdout_start <= dataset_start:
        raise GateBPlanIntegrityError("final holdout leaves no Gate B history")

    minimum_span = (
        config.train_duration
        + config.validation_duration
        + config.test_duration
    )
    candidate_start = dataset_start
    attempt_count = 0
    rejected_starts: list[dict[str, str]] = []
    folds: list[dict[str, Any]] | None = None
    final: dict[str, Any] | None = None

    while candidate_start + minimum_span <= holdout_start:
        attempt_count += 1
        try:
            folds, final = _build_gate_b_plan_from_start(
                timeline,
                analysis_start=candidate_start,
                dataset_end=dataset_end,
                holdout_start=holdout_start,
                config=config,
            )
            break
        except GateBPlanIntegrityError as exc:
            rejected_starts.append(
                {
                    "analysis_start_at": candidate_start.isoformat(),
                    "reason": str(exc),
                }
            )
            candidate_start = candidate_start + config.step_duration

    if folds is None or final is None:
        last_reason = (
            rejected_starts[-1]["reason"]
            if rejected_starts
            else "no candidate analysis start was evaluable"
        )
        raise GateBPlanIntegrityError(
            "no contiguous Gate B epoch satisfies the frozen walk-forward "
            f"minimums; last rejection: {last_reason}"
        )

    excluded_prefix = [
        record["condition_id"]
        for record in timeline
        if record["market_start_at"] < candidate_start
    ]

    research_payload = {
        "fee_rate": research_config.fee_rate,
        "slippage_buffer": research_config.slippage_buffer,
        "min_edge_grid": list(research_config.min_edge_grid),
        "min_validation_trades": research_config.min_validation_trades,
        "min_train_eligible_markets": research_config.min_train_eligible_markets,
        "min_validation_eligible_markets": (
            research_config.min_validation_eligible_markets
        ),
    }
    payload: dict[str, Any] = {
        "gate_b_version": V2_GATE_B_VERSION,
        "feature_version": V2_FEATURE_VERSION,
        "horizon_seconds": 300,
        "market_count": len(timeline),
        "market_start_at": dataset_start.isoformat(),
        "market_end_at": dataset_end.isoformat(),
        "planning_epoch_start_at": (
            planning_boundary.isoformat() if planning_boundary is not None else None
        ),
        "analysis_start_at": candidate_start.isoformat(),
        "analysis_start_attempt_count": attempt_count,
        "excluded_prefix_condition_ids": excluded_prefix,
        "analysis_start_rejections": rejected_starts,
        "coverage_input_sha256": FROZEN_COVERAGE_INPUT_SHA256,
        "freshness_candidates_seconds": list(FROZEN_FRESHNESS_CANDIDATES_SECONDS),
        "include_no_trade": FROZEN_INCLUDE_NO_TRADE,
        "labels_read": False,
        "config": _config_payload(config),
        "research_config": research_payload,
        "research_config_sha256": canonical_hash(research_payload),
        "folds": folds,
        "final": final,
    }
    payload["plan_sha256"] = canonical_hash(payload)
    return payload
