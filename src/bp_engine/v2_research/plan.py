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


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _partition(name: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": name,
        "condition_ids": [record["condition_id"] for record in records],
        "market_start_at": (
            _utc(records[0]["market_start_at"]).isoformat() if records else None
        ),
        "market_end_at": (
            _utc(records[-1]["market_end_at"]).isoformat() if records else None
        ),
    }


def _feature_timeline(connection: Connection) -> list[dict[str, Any]]:
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
        timeline.append(
            {
                "condition_id": condition_id,
                "slug": str(first["slug"]),
                "horizon_seconds": int(first["horizon_seconds"]),
                "market_start_at": _utc(first["market_start_at"]),
                "market_end_at": _utc(first["market_end_at"]),
            }
        )
    timeline.sort(key=lambda item: (item["market_start_at"], item["condition_id"]))
    return timeline


def build_gate_b_plan(
    connection: Connection,
    config: GateBPlanConfig | None = None,
    research_config: GateBResearchConfig | None = None,
) -> dict[str, Any]:
    config = config or GateBPlanConfig()
    research_config = research_config or GateBResearchConfig()
    timeline = _feature_timeline(connection)
    total = len(timeline)
    e = config.embargo_markets
    holdout_start = total - config.final_holdout_markets
    if holdout_start <= 0:
        raise GateBPlanIntegrityError("final holdout consumes all markets")

    final_validation_end = holdout_start - e
    final_validation_start = final_validation_end - config.validation_markets
    final_train_end = final_validation_start - e
    if final_train_end < config.min_initial_train_markets:
        raise GateBPlanIntegrityError("not enough markets for final train/validation/holdout")

    folds: list[dict[str, Any]] = []
    test_start = (
        config.min_initial_train_markets
        + e
        + config.validation_markets
        + e
    )
    index = 0
    seen_test: set[str] = set()
    while test_start + config.test_markets <= holdout_start:
        validation_end = test_start - e
        validation_start = validation_end - config.validation_markets
        train_end = validation_start - e
        train_records = timeline[:train_end]
        validation_records = timeline[validation_start:validation_end]
        test_records = timeline[test_start : test_start + config.test_markets]
        if len(train_records) < config.min_initial_train_markets:
            raise GateBPlanIntegrityError("fold train partition below minimum")
        test_ids = {record["condition_id"] for record in test_records}
        if seen_test.intersection(test_ids):
            raise GateBPlanIntegrityError("ordinary test market reused")
        seen_test.update(test_ids)
        embargo_records = (
            timeline[train_end : train_end + e]
            + timeline[validation_end : validation_end + e]
        )
        membership = {
            "index": index,
            "train": _partition("train", train_records),
            "validation": _partition("validation", validation_records),
            "test": _partition("test", test_records),
            "embargo_condition_ids": [
                record["condition_id"] for record in embargo_records
            ],
        }
        membership["membership_sha256"] = canonical_hash(membership)
        folds.append(membership)
        index += 1
        test_start += config.test_markets

    if len(folds) < 3:
        raise GateBPlanIntegrityError(
            f"Gate B plan requires at least 3 ordinary folds; found {len(folds)}"
        )

    final_train_records = timeline[:final_train_end]
    final_validation_records = timeline[final_validation_start:final_validation_end]
    final_holdout_records = timeline[holdout_start:]
    final_embargo = (
        timeline[final_train_end : final_train_end + e]
        + timeline[final_validation_end : final_validation_end + e]
    )
    holdout_ids = {record["condition_id"] for record in final_holdout_records}
    if seen_test.intersection(holdout_ids):
        raise GateBPlanIntegrityError("final holdout overlaps ordinary test markets")

    final = {
        "train": _partition("final_train", final_train_records),
        "validation": _partition("final_validation", final_validation_records),
        "holdout": _partition("final_holdout", final_holdout_records),
        "train_condition_ids": [record["condition_id"] for record in final_train_records],
        "validation_condition_ids": [
            record["condition_id"] for record in final_validation_records
        ],
        "holdout_condition_ids": [
            record["condition_id"] for record in final_holdout_records
        ],
        "embargo_condition_ids": [record["condition_id"] for record in final_embargo],
    }
    final["membership_sha256"] = canonical_hash(final)

    config_payload = {
        "min_initial_train_markets": config.min_initial_train_markets,
        "validation_markets": config.validation_markets,
        "test_markets": config.test_markets,
        "final_holdout_markets": config.final_holdout_markets,
        "embargo_markets": config.embargo_markets,
    }
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
        "market_count": total,
        "market_start_at": _utc(timeline[0]["market_start_at"]).isoformat(),
        "market_end_at": _utc(timeline[-1]["market_end_at"]).isoformat(),
        "coverage_input_sha256": FROZEN_COVERAGE_INPUT_SHA256,
        "freshness_candidates_seconds": list(FROZEN_FRESHNESS_CANDIDATES_SECONDS),
        "include_no_trade": FROZEN_INCLUDE_NO_TRADE,
        "labels_read": False,
        "config": config_payload,
        "research_config": research_payload,
        "research_config_sha256": canonical_hash(research_payload),
        "folds": folds,
        "final": final,
    }
    payload["plan_sha256"] = canonical_hash(payload)
    return payload
