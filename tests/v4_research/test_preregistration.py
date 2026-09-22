from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, insert

from bp_engine.storage import schema
from bp_engine.v4_research import cli as cli_module
from bp_engine.v4_research import plan as plan_module
from bp_engine.v4_research import readiness as readiness_module
from bp_engine.v4_research.config import (
    FROZEN_V4_GATE_B_CONFIG,
    V4_PREDICTOR_NAMES,
    V4_REGIME_CONTEXT_PREDICTORS,
    V4_SHORT_CONTEXT_PREDICTORS,
)

OFFSETS = (60, 120, 180, 240)
PREFIXES = ("coinbase", "bybit_spot", "bybit_linear")


def _test_config():
    start = FROZEN_V4_GATE_B_CONFIG.epoch_start
    return replace(
        FROZEN_V4_GATE_B_CONFIG,
        epoch_end=start + timedelta(hours=12),
        train_duration=timedelta(hours=4),
        validation_duration=timedelta(hours=1),
        test_duration=timedelta(hours=1),
        step_duration=timedelta(hours=1),
        final_holdout_duration=timedelta(hours=2),
        min_train_markets=40,
        min_validation_markets=10,
        min_test_markets=10,
        min_final_holdout_markets=20,
        ordinary_fold_count=5,
        min_known_regime_markets=10,
        min_validation_trades_per_fold=2,
        required_non_negative_validation_folds=4,
        min_reporting_slice_markets=5,
    )


def _z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _regime_values(index: int) -> dict[str, float]:
    regime = ("bull", "bear", "sideways_mixed")[index % 3]
    values = {
        "regime_bull": 0.0,
        "regime_bear": 0.0,
        "regime_sideways_mixed": 0.0,
    }
    values[f"regime_{regime}"] = 1.0
    direction = 1.0 if regime == "bull" else -1.0 if regime == "bear" else 0.0
    values.update(
        {
            "regime_5m_direction": direction,
            "regime_15m_direction": direction,
            "regime_60m_direction": direction,
            "regime_5m_venue_agreement": 1.0,
            "regime_15m_venue_agreement": 1.0,
            "regime_60m_venue_agreement": 1.0,
            "regime_trend_score": direction,
        }
    )
    return values


def _feature_rows(
    *,
    config=None,
    market_count: int = 144,
    start: datetime | None = None,
    condition_prefix: str = "condition",
    future_cutoff: bool = False,
    polymarket_predictor: bool = False,
    single_regime: str | None = None,
) -> list[dict[str, object]]:
    config = config or _test_config()
    epoch_start = start or config.epoch_start
    rows: list[dict[str, object]] = []
    for market_index in range(market_count):
        market_start = epoch_start + timedelta(minutes=5 * market_index)
        condition_id = f"{condition_prefix}-{market_index:04d}"
        for offset_index, offset in enumerate(OFFSETS):
            feature_at = market_start + timedelta(seconds=offset)
            features: dict[str, object] = {
                "seconds_elapsed": offset,
                "seconds_remaining": 300 - offset,
                "fraction_elapsed": offset / 300,
            }
            for prefix in PREFIXES:
                features[f"{prefix}_return_from_market_start"] = 0.001
                features[f"{prefix}_return_30s"] = 0.001
                features[f"{prefix}_return_60s"] = 0.001
                features[f"{prefix}_return_120s"] = (
                    None if offset == 60 else 0.001
                )
                features[f"{prefix}_return_5m"] = 0.002
                features[f"{prefix}_return_15m"] = 0.003
                features[f"{prefix}_return_60m"] = 0.004
            features.update(
                {
                    "coinbase_bybit_spot_return_spread": 0.0,
                    "coinbase_bybit_spot_direction_agree": 1.0,
                    "spot_linear_direction_agree": 1.0,
                    "bybit_linear_vs_spot_basis": 0.0,
                    "bybit_linear_funding_rate": 0.0001,
                    "bybit_linear_open_interest": 1000.0,
                }
            )
            if single_regime is None:
                features.update(_regime_values(market_index))
            else:
                regime = {
                    "regime_bull": 0.0,
                    "regime_bear": 0.0,
                    "regime_sideways_mixed": 0.0,
                    "regime_5m_direction": 0.0,
                    "regime_15m_direction": 0.0,
                    "regime_60m_direction": 0.0,
                    "regime_5m_venue_agreement": 1.0,
                    "regime_15m_venue_agreement": 1.0,
                    "regime_60m_venue_agreement": 1.0,
                    "regime_trend_score": 0.0,
                }
                regime[f"regime_{single_regime}"] = 1.0
                features.update(regime)
            if polymarket_predictor and market_index == 0 and offset == 60:
                features["pm_forbidden_predictor"] = 0.5

            missing_flags: dict[str, bool] = {}
            source_cutoffs: dict[str, str] = {}
            for prefix in PREFIXES:
                missing_flags[f"{prefix}_current_missing"] = False
                missing_flags[f"{prefix}_current_stale"] = False
                source_cutoffs[f"{prefix}_current_state"] = _z(
                    feature_at - timedelta(seconds=1)
                )
                for horizon, delta in (
                    ("5m", timedelta(minutes=5)),
                    ("15m", timedelta(minutes=15)),
                    ("60m", timedelta(minutes=60)),
                ):
                    missing_flags[
                        f"{prefix}_regime_trailing_{horizon}_missing"
                    ] = False
                    missing_flags[
                        f"{prefix}_regime_trailing_{horizon}_stale"
                    ] = False
                    source_cutoffs[
                        f"{prefix}_regime_trailing_{horizon}_state"
                    ] = _z(feature_at - delta)
            if future_cutoff and market_index == 0 and offset == 60:
                source_cutoffs["diagnostic_future"] = _z(
                    feature_at + timedelta(seconds=1)
                )

            seed = market_index * 10 + offset_index + 1
            rows.append(
                {
                    "condition_id": condition_id,
                    "slug": f"btc-updown-5m-{condition_id}",
                    "horizon_seconds": 300,
                    "market_start_at": market_start,
                    "market_end_at": market_start + timedelta(minutes=5),
                    "feature_at": feature_at,
                    "feature_offset_seconds": offset,
                    "feature_version": config.feature_version,
                    "features": features,
                    "missing_flags": missing_flags,
                    "source_cutoffs": source_cutoffs,
                    "input_fingerprint": f"{seed:064x}",
                    "feature_hash": f"{seed + 100000:064x}",
                    "generated_at": feature_at + timedelta(seconds=1),
                }
            )
    return rows


def _engine(rows: list[dict[str, object]]):
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
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


def test_frozen_v4_gate_b_config_is_future_only_and_complete() -> None:
    config = FROZEN_V4_GATE_B_CONFIG
    assert config.research_plan_version == "v4-gate-b-preregister-v1"
    assert config.dataset_version == "supervised-core-v4-regime-aware-v1"
    assert config.feature_version == "core-v4-regime-aware"
    assert config.label_version == "official-outcome-v1"
    assert config.epoch_start == datetime(2026, 9, 23, 0, 0, tzinfo=UTC)
    assert config.epoch_end == datetime(2026, 9, 30, 0, 0, tzinfo=UTC)
    assert config.epoch_end - config.epoch_start == timedelta(days=7)
    assert config.train_duration == timedelta(hours=48)
    assert config.validation_duration == timedelta(hours=12)
    assert config.test_duration == timedelta(hours=12)
    assert config.step_duration == timedelta(hours=12)
    assert config.final_holdout_duration == timedelta(hours=24)
    assert config.ordinary_fold_count == 7
    assert config.min_train_markets == 480
    assert config.min_validation_markets == 120
    assert config.min_test_markets == 120
    assert config.min_final_holdout_markets == 240
    assert config.known_regimes == ("bull", "bear", "sideways_mixed")
    assert config.min_known_regime_markets == 120


def test_frozen_v4_search_contract_covers_all_remediation_objectives() -> None:
    config = FROZEN_V4_GATE_B_CONFIG
    assert config.predictor_names == V4_PREDICTOR_NAMES
    assert config.short_context_predictor_names == V4_SHORT_CONTEXT_PREDICTORS
    assert set(V4_REGIME_CONTEXT_PREDICTORS).issubset(config.predictor_names)
    assert config.forecast_candidates == (
        "training_prior",
        "single_feature_btc_logistic",
        "short_context_v4_logistic",
        "full_v4_logistic",
        "full_v4_xgboost",
    )
    assert config.calibration_candidates == ("identity", "platt")
    assert config.offset_candidates_seconds == (60, 120, 180, 240)
    assert config.min_edge_grid == (
        0.0,
        0.01,
        0.02,
        0.03,
        0.05,
        0.075,
        0.1,
        0.15,
    )
    assert config.no_trade_candidate is True
    assert config.min_validation_trades_per_fold == 16
    assert config.required_non_negative_validation_folds == 6
    assert config.require_positive_aggregate_validation_pnl is True
    assert config.side_specific_policy_allowed is False
    assert config.regime_specific_policy_allowed is False
    assert config.coverage_frontier_required is True
    assert config.drawdown_reporting_required is True
    assert config.losing_streak_reporting_required is True
    assert config.profit_factor_reporting_required is True
    assert config.execution_availability_report_required is True
    assert config.reporting_slices == (
        "overall",
        "bull",
        "bear",
        "sideways_mixed",
        "unknown",
        "up",
        "down",
        "regime_by_side",
    )


def test_v4_readiness_is_outcome_blind_and_requires_epoch_completion() -> None:
    config = _test_config()
    engine = _engine(_feature_rows(config=config))
    with engine.connect() as connection:
        before = readiness_module.assess_v4_gate_b_readiness(
            connection,
            as_of=config.epoch_end - timedelta(seconds=1),
            config=config,
        )
        after = readiness_module.assess_v4_gate_b_readiness(
            connection,
            as_of=config.epoch_end,
            config=config,
        )
    assert before["ready"] is False
    assert "epoch_incomplete" in before["blocking_reasons"]
    assert after["ready"] is True
    assert after["labels_read"] is False
    assert after["training_performed"] is False
    assert after["policy_selected"] is False
    assert after["regime_market_counts"] == {
        "bull": 48,
        "bear": 48,
        "sideways_mixed": 48,
    }


def test_v4_readiness_fails_closed_on_leakage_or_missing_regime_diversity() -> None:
    config = _test_config()
    bad_rows = _feature_rows(
        config=config,
        future_cutoff=True,
        polymarket_predictor=True,
        single_regime="bull",
    )
    engine = _engine(bad_rows)
    with engine.connect() as connection:
        report = readiness_module.assess_v4_gate_b_readiness(
            connection,
            as_of=config.epoch_end,
            config=config,
        )
    assert report["ready"] is False
    assert "future_cutoff_violations" in report["blocking_reasons"]
    assert "polymarket_predictor_keys_present" in report["blocking_reasons"]
    assert "bear_market_count_below_minimum" in report["blocking_reasons"]
    assert "sideways_mixed_market_count_below_minimum" in report["blocking_reasons"]


def test_v4_feature_only_plan_is_deterministic_and_future_structural() -> None:
    config = _test_config()
    historical = _feature_rows(
        config=config,
        market_count=1,
        start=config.epoch_start - timedelta(minutes=5),
        condition_prefix="historical",
        future_cutoff=True,
    )
    engine = _engine([*_feature_rows(config=config), *historical])
    with engine.connect() as connection:
        first = plan_module.build_v4_gate_b_plan(
            connection,
            as_of=config.epoch_end,
            config=config,
        )
        second = plan_module.build_v4_gate_b_plan(
            connection,
            as_of=config.epoch_end,
            config=config,
        )
    assert first == second
    assert first["research_plan_version"] == "v4-gate-b-preregister-v1"
    assert first["market_count"] == 144
    assert len(first["folds"]) == 5
    assert first["labels_read"] is False
    assert first["training_performed"] is False
    assert first["policy_selected"] is False
    assert first["final_holdout_evaluated"] is False
    assert "historical-0000" not in _partition_ids(first)

    for index, fold in enumerate(first["folds"]):
        fold_start = config.epoch_start + index * config.step_duration
        assert fold["train"]["start"] == fold_start.isoformat()
        assert len(fold["train"]["condition_ids"]) == 47
        assert len(fold["validation"]["condition_ids"]) == 11
        assert len(fold["test"]["condition_ids"]) == 12
        assert len(fold["embargo_condition_ids"]) == 2
        assert len(fold["membership_sha256"]) == 64
    assert len(first["final"]["train_condition_ids"]) == 47
    assert len(first["final"]["validation_condition_ids"]) == 11
    assert len(first["final"]["holdout_condition_ids"]) == 24
    for field in (
        "readiness_input_sha256",
        "config_sha256",
        "feature_manifest_sha256",
        "plan_sha256",
    ):
        assert len(first[field]) == 64
        int(first[field], 16)


def test_v4_plan_hash_binds_the_full_preregistered_search_contract() -> None:
    config = _test_config()
    engine = _engine(_feature_rows(config=config))
    changed = replace(config, fee_rate=0.08)
    with engine.connect() as connection:
        frozen = plan_module.build_v4_gate_b_plan(
            connection,
            as_of=config.epoch_end,
            config=config,
        )
        modified = plan_module.build_v4_gate_b_plan(
            connection,
            as_of=config.epoch_end,
            config=changed,
        )
    assert frozen["readiness_input_sha256"] != modified["readiness_input_sha256"]
    assert frozen["config_sha256"] != modified["config_sha256"]
    assert frozen["plan_sha256"] != modified["plan_sha256"]


def test_v4_research_sources_do_not_read_outcomes_or_fit_models() -> None:
    for module in (readiness_module, plan_module):
        source = inspect.getsource(module).lower()
        for forbidden in (
            "market_labels",
            "official_outcome",
            "live_prediction_evaluations",
            "paper_settlements",
            "realized_pnl",
            "train_logistic",
            "train_xgboost",
            "joblib",
        ):
            assert forbidden not in source


def test_v4_cli_exposes_only_readiness_and_plan_and_plan_is_no_clobber(
    tmp_path,
) -> None:
    parser = cli_module.build_parser()
    readiness = parser.parse_args(
        ["readiness", "--as-of", "2026-09-30T00:00:00Z"]
    )
    plan = parser.parse_args(
        [
            "plan",
            "--as-of",
            "2026-09-30T00:00:00Z",
            "--output",
            str(tmp_path / "plan.json"),
        ]
    )
    assert readiness.command == "readiness"
    assert plan.command == "plan"

    destination = tmp_path / "artifact.json"
    cli_module._write_exclusive(str(destination), {"safe": True})
    with pytest.raises(FileExistsError):
        cli_module._write_exclusive(str(destination), {"safe": False})
