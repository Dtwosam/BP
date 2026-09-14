from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, insert

from bp_engine.storage import schema
from bp_engine.v3_research.config import FROZEN_V3_GATE_B_CONFIG

try:
    from bp_engine.v3_research import plan as plan_module
except ImportError:
    plan_module = None

PREFIXES = ("coinbase", "bybit_spot", "bybit_linear")
RETURN_FIELDS = tuple(
    f"{prefix}_{suffix}"
    for prefix in PREFIXES
    for suffix in (
        "return_from_market_start",
        "return_30s",
        "return_60s",
        "return_120s",
    )
)
OFFSETS = (60, 120, 180, 240)


def _z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _feature_rows(
    market_count: int = 864,
    *,
    start: datetime | None = None,
    condition_prefix: str = "condition",
    seed_offset: int = 0,
    future_cutoff: bool = False,
) -> list[dict[str, object]]:
    epoch_start = start or FROZEN_V3_GATE_B_CONFIG.epoch_start
    rows: list[dict[str, object]] = []
    for market_index in range(market_count):
        market_start = epoch_start + timedelta(minutes=5 * market_index)
        condition_id = f"{condition_prefix}-{market_index:03d}"
        for offset_index, offset in enumerate(OFFSETS):
            feature_at = market_start + timedelta(seconds=offset)
            features: dict[str, object] = {
                "seconds_elapsed": offset,
                "seconds_remaining": 300 - offset,
                "fraction_elapsed": offset / 300,
                "horizon_seconds": 300,
            }
            for field in RETURN_FIELDS:
                structural = field.endswith("_return_120s") and offset == 60
                features[field] = None if structural else 0.001
            source_cutoffs = {
                f"{prefix}_current_state": _z(feature_at - timedelta(seconds=1))
                for prefix in PREFIXES
            }
            if future_cutoff:
                source_cutoffs["diagnostic_future"] = _z(
                    feature_at + timedelta(seconds=1)
                )
            missing_flags = {
                key: value
                for prefix in PREFIXES
                for key, value in (
                    (f"{prefix}_current_missing", False),
                    (f"{prefix}_current_stale", False),
                )
            }
            fingerprint_seed = seed_offset + market_index * 10 + offset_index + 1
            rows.append(
                {
                    "condition_id": condition_id,
                    "slug": f"btc-updown-5m-{condition_id}",
                    "horizon_seconds": 300,
                    "market_start_at": market_start,
                    "market_end_at": market_start + timedelta(minutes=5),
                    "feature_at": feature_at,
                    "feature_offset_seconds": offset,
                    "feature_version": "core-v3-btc-native",
                    "features": features,
                    "missing_flags": missing_flags,
                    "source_cutoffs": source_cutoffs,
                    "input_fingerprint": f"{fingerprint_seed:064x}",
                    "feature_hash": f"{fingerprint_seed + 10000:064x}",
                    "generated_at": feature_at + timedelta(seconds=1),
                }
            )
    return rows


def _engine_with_features(
    market_count: int = 864,
    *,
    extra_rows: list[dict[str, object]] | None = None,
    future_cutoff: bool = False,
):
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    rows = _feature_rows(market_count, future_cutoff=future_cutoff)
    rows.extend(extra_rows or [])
    with engine.begin() as connection:
        connection.execute(insert(schema.market_features), rows)
    return engine


def _partition_ids(plan: dict[str, object]) -> set[str]:
    values: set[str] = set()
    for fold in plan["folds"]:
        for name in ("train", "validation", "test"):
            values.update(fold[name]["condition_ids"])
    for name in (
        "train_condition_ids",
        "validation_condition_ids",
        "holdout_condition_ids",
    ):
        values.update(plan["final"][name])
    return values


def test_v3_gate_b_plan_is_fixed_five_fold_deterministic_and_feature_only() -> None:
    assert plan_module is not None
    config = FROZEN_V3_GATE_B_CONFIG
    engine = _engine_with_features()

    with engine.connect() as connection:
        first = plan_module.build_v3_gate_b_plan(
            connection,
            as_of=config.epoch_end,
        )
        second = plan_module.build_v3_gate_b_plan(
            connection,
            as_of=config.epoch_end,
        )

    assert first == second
    assert first["research_plan_version"] == "v3-gate-b-preregister-v2"
    assert first["feature_version"] == "core-v3-btc-native"
    assert first["market_count"] == 864
    assert len(first["folds"]) == 5
    assert first["labels_read"] is False
    assert first["training_performed"] is False
    assert first["final_holdout_evaluated"] is False

    for index, fold in enumerate(first["folds"]):
        fold_start = config.epoch_start + index * config.step_duration
        assert fold["index"] == index
        assert fold["train"]["start"] == _iso(fold_start)
        assert fold["train"]["end"] == _iso(fold_start + config.train_duration)
        assert len(fold["train"]["condition_ids"]) == 287
        assert len(fold["validation"]["condition_ids"]) == 71
        assert len(fold["test"]["condition_ids"]) == 72
        assert fold["purged_condition_ids"] == []
        assert len(fold["embargo_condition_ids"]) == 2
        assert len(fold["membership_sha256"]) == 64

    holdout_start = config.epoch_end - config.final_holdout_duration
    assert first["final"]["holdout"]["start"] == _iso(holdout_start)
    assert first["final"]["holdout"]["end"] == _iso(config.epoch_end)
    assert len(first["final"]["train_condition_ids"]) == 287
    assert len(first["final"]["validation_condition_ids"]) == 71
    assert len(first["final"]["holdout_condition_ids"]) == 144
    assert first["final"]["holdout_condition_ids"][-1] == "condition-863"

    for field in ("config_sha256", "feature_manifest_sha256", "plan_sha256"):
        assert len(first[field]) == 64
        int(first[field], 16)


def test_v3_gate_b_plan_ignores_all_pre_epoch_markets_structurally() -> None:
    assert plan_module is not None
    config = FROZEN_V3_GATE_B_CONFIG
    historical_rows = _feature_rows(
        1,
        start=config.epoch_start - timedelta(minutes=5),
        condition_prefix="historical-diagnosis",
        seed_offset=900_000,
        future_cutoff=True,
    )
    baseline_engine = _engine_with_features()
    contaminated_engine = _engine_with_features(extra_rows=historical_rows)

    with baseline_engine.connect() as connection:
        baseline = plan_module.build_v3_gate_b_plan(
            connection,
            as_of=config.epoch_end,
        )
    with contaminated_engine.connect() as connection:
        contaminated = plan_module.build_v3_gate_b_plan(
            connection,
            as_of=config.epoch_end,
        )

    assert contaminated == baseline
    assert "historical-diagnosis-000" not in _partition_ids(contaminated)
    for forbidden in (
        "excluded_condition_ids",
        "diagnosis_exclusion_sha256",
        "consumed_v2_final_holdout_exclusion_sha256",
    ):
        assert forbidden not in contaminated


def test_v3_gate_b_plan_hash_binds_future_search_contract() -> None:
    assert plan_module is not None
    config = FROZEN_V3_GATE_B_CONFIG
    engine = _engine_with_features()
    changed = replace(config, fee_rate=0.08)

    with engine.connect() as connection:
        frozen = plan_module.build_v3_gate_b_plan(
            connection,
            as_of=config.epoch_end,
            config=config,
        )
        modified = plan_module.build_v3_gate_b_plan(
            connection,
            as_of=config.epoch_end,
            config=changed,
        )

    assert frozen["readiness_input_sha256"] != modified["readiness_input_sha256"]
    assert frozen["plan_sha256"] != modified["plan_sha256"]


def test_v3_gate_b_plan_fails_closed_before_planning_when_readiness_is_not_met() -> None:
    assert plan_module is not None
    engine = _engine_with_features(future_cutoff=True)

    with engine.connect() as connection:
        with pytest.raises(plan_module.V3PlanIntegrityError, match="readiness"):
            plan_module.build_v3_gate_b_plan(
                connection,
                as_of=FROZEN_V3_GATE_B_CONFIG.epoch_end,
            )


def test_v3_gate_b_plan_enforces_v3_local_final_holdout_minimum() -> None:
    assert plan_module is not None
    config = FROZEN_V3_GATE_B_CONFIG
    engine = _engine_with_features(market_count=839)

    with engine.connect() as connection:
        with pytest.raises(
            plan_module.V3PlanIntegrityError,
            match="final holdout requires at least 120 markets; found 119",
        ):
            plan_module.build_v3_gate_b_plan(
                connection,
                as_of=config.epoch_end,
            )


def test_v3_gate_b_plan_source_is_outcome_and_training_isolated() -> None:
    assert plan_module is not None
    source = inspect.getsource(plan_module).lower()
    for forbidden in (
        "market_labels",
        "official_outcome",
        "live_prediction_evaluations",
        "paper_settlements",
        "realized_pnl",
        "adaptive_train",
        "xgboost",
        "logisticregression",
    ):
        assert forbidden not in source
