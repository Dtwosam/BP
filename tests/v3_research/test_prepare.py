from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from bp_engine.features.hashing import canonical_hash
from bp_engine.v3_research import cli as cli_module
from bp_engine.v3_research import service as service_module
from bp_engine.v3_research.config import FROZEN_V3_GATE_B_CONFIG


def _plan() -> dict[str, object]:
    folds = []
    for index in range(5):
        folds.append(
            {
                "index": index,
                "train": {"condition_ids": [f"train-{index}"]},
                "validation": {"condition_ids": [f"validation-{index}"]},
                "test": {"condition_ids": [f"test-{index}"]},
                "membership_sha256": f"{index + 1:064x}",
            }
        )
    payload: dict[str, object] = {
        "research_plan_version": "v3-gate-b-preregister-v2",
        "dataset_version": "supervised-core-v3-btc-native-v1",
        "feature_version": "core-v3-btc-native",
        "horizon_seconds": 300,
        "feature_offsets_seconds": [60, 120, 180, 240],
        "epoch_start": "2026-09-16T13:45:00+00:00",
        "epoch_end": "2026-09-19T13:45:00+00:00",
        "market_count": 15,
        "readiness_input_sha256": "a" * 64,
        "config_sha256": canonical_hash(
            service_module.v3_gate_b_config_payload(FROZEN_V3_GATE_B_CONFIG)
        ),
        "feature_manifest_sha256": "b" * 64,
        "folds": folds,
        "final": {
            "membership_sha256": "f" * 64,
            "train_condition_ids": ["final-train"],
            "validation_condition_ids": ["final-validation"],
            "holdout_condition_ids": ["final-holdout"],
        },
        "labels_read": False,
        "training_performed": False,
        "final_holdout_evaluated": False,
    }
    payload["plan_sha256"] = canonical_hash(payload)
    return payload


def test_prepare_plan_verification_and_non_holdout_scope() -> None:
    plan = _plan()
    service_module.verify_v3_prepare_plan(plan)
    ids = set(service_module.non_holdout_condition_ids(plan))

    assert "final-holdout" not in ids
    assert "final-train" in ids
    assert "final-validation" in ids
    assert {f"test-{index}" for index in range(5)} <= ids

    changed = dict(plan)
    changed["market_count"] = 999
    with pytest.raises(service_module.V3PrepareIntegrityError, match="plan_sha256"):
        service_module.verify_v3_prepare_plan(changed)


def test_v3_model_predictors_are_btc_only_and_missingness_explicit() -> None:
    row_predictors = {
        name: 0.1 for name in FROZEN_V3_GATE_B_CONFIG.predictor_names
    }
    row_predictors.update(
        {
            "missing__coinbase_market_start_missing": 0.0,
            "missing__coinbase_market_start_stale": 0.0,
            "missing__coinbase_current_missing": 0.0,
            "missing__coinbase_current_stale": 0.0,
            "missing__coinbase_trailing_30s_missing": 0.0,
            "missing__coinbase_trailing_60s_stale": 0.0,
            "missing__bybit_spot_current_missing": 0.0,
            "missing__bybit_linear_current_missing": 0.0,
            "horizon_seconds": 300.0,
            "pm_up_best_ask": 0.55,
        }
    )

    single = service_module.model_predictor_names(
        row_predictors, "single_feature_btc_logistic"
    )
    full = service_module.model_predictor_names(row_predictors, "full_v3_logistic")

    assert single == (
        "coinbase_return_from_market_start",
        "missing__coinbase_market_start_missing",
        "missing__coinbase_market_start_stale",
        "missing__coinbase_current_missing",
        "missing__coinbase_current_stale",
    )
    assert set(FROZEN_V3_GATE_B_CONFIG.predictor_names) <= set(full)
    assert all("pm_" not in name and "polymarket" not in name.lower() for name in full)
    assert "horizon_seconds" not in full


def test_xgboost_cannot_replace_logistic_without_both_metric_improvements() -> None:
    logistic = {"log_loss": 0.60, "brier_score": 0.20}
    assert service_module.xgboost_replacement_eligible(
        logistic, {"log_loss": 0.59, "brier_score": 0.19}
    )
    assert not service_module.xgboost_replacement_eligible(
        logistic, {"log_loss": 0.59, "brier_score": 0.21}
    )
    assert not service_module.xgboost_replacement_eligible(
        logistic, {"log_loss": 0.61, "brier_score": 0.19}
    )


def test_validation_economic_gate_matches_frozen_five_fold_rule() -> None:
    passing = [
        {"trade_count": 8, "realized_pnl_after_assumed_costs": value}
        for value in (1.0, 0.0, 2.0, 0.0, -0.5)
    ]
    failing_nonnegative = [
        {"trade_count": 8, "realized_pnl_after_assumed_costs": value}
        for value in (1.0, 1.0, 1.0, -0.1, -0.1)
    ]
    failing_trades = [dict(item) for item in passing]
    failing_trades[0]["trade_count"] = 7

    assert service_module.validation_economics_pass(passing) is True
    assert service_module.validation_economics_pass(failing_nonnegative) is False
    assert service_module.validation_economics_pass(failing_trades) is False


def test_prepare_cli_is_read_only_no_clobber_and_has_no_holdout_command(tmp_path: Path) -> None:
    parser = cli_module.build_parser()
    output = tmp_path / "selection.json"
    artifact = tmp_path / "model.joblib"
    args = parser.parse_args(
        [
            "prepare",
            "--plan",
            str(tmp_path / "plan.json"),
            "--output",
            str(output),
            "--model-output",
            str(artifact),
        ]
    )
    assert args.command == "prepare"

    help_text = parser.format_help().lower()
    assert "prepare" in help_text
    assert "evaluate-holdout" not in help_text

    cli_module._write_exclusive(str(output), {"ok": True})
    with pytest.raises(FileExistsError):
        cli_module._write_exclusive(str(output), {"ok": False})


def test_v3_prepare_source_has_no_database_write_or_holdout_evaluator() -> None:
    source = "\n".join(
        (
            inspect.getsource(service_module),
            inspect.getsource(cli_module),
        )
    ).lower()
    for forbidden in (
        "evaluate_gate_b_holdout",
        "evaluate-holdout",
        "modeltrainingrunrepository",
        "backtestrunrepository",
        "calibrationedgerunrepository",
        "metadata.create_all",
        ".store(",
        "automatic_promotion=true",
        "live_trading_enabled=true",
    ):
        assert forbidden not in source
