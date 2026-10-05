from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bp_engine.features.hashing import canonical_hash
from bp_engine.modeling.models import SupervisedRow
from bp_engine.v4_research import cli as cli_module
from bp_engine.v4_research import policy as policy_module
from bp_engine.v4_research import service as service_module
from bp_engine.v4_research.config import (
    FROZEN_V4_GATE_B_CONFIG,
    v4_gate_b_config_payload,
)


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
        "research_plan_version": "v4-gate-b-preregister-v2",
        "dataset_version": "supervised-core-v4-regime-aware-v1",
        "feature_version": "core-v4-regime-aware",
        "label_version": "official-outcome-v1",
        "horizon_seconds": 300,
        "feature_offsets_seconds": [60, 120, 180, 240],
        "epoch_start": "2026-09-24T00:00:00+00:00",
        "epoch_end": "2026-09-30T00:00:00+00:00",
        "market_count": 1662,
        "readiness_input_sha256": "a" * 64,
        "config_sha256": canonical_hash(
            v4_gate_b_config_payload(FROZEN_V4_GATE_B_CONFIG)
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
        "policy_selected": False,
        "final_holdout_evaluated": False,
    }
    payload["plan_sha256"] = canonical_hash(payload)
    return payload


def _row(
    condition_id: str,
    *,
    target: int,
    regime: str = "bull",
) -> SupervisedRow:
    start = datetime(2026, 9, 24, 0, 0, tzinfo=UTC)
    predictors = {
        name: 0.01 for name in FROZEN_V4_GATE_B_CONFIG.predictor_names
    }
    predictors["regime_bull"] = 1.0 if regime == "bull" else 0.0
    predictors["regime_bear"] = 1.0 if regime == "bear" else 0.0
    predictors["regime_sideways_mixed"] = (
        1.0 if regime == "sideways_mixed" else 0.0
    )
    return SupervisedRow(
        condition_id=condition_id,
        slug=f"btc-updown-5m-{condition_id}",
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=start.replace(minute=5),
        feature_at=start.replace(minute=1),
        feature_offset_seconds=60,
        predictors=predictors,
        target=target,
        feature_hash="a" * 64,
        input_fingerprint="b" * 64,
    )


def test_prepare_plan_is_bound_to_explicit_authorization_and_excludes_holdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    monkeypatch.setattr(
        service_module,
        "AUTHORIZED_V4_GATE_B_V2_PLAN_SHA256",
        plan["plan_sha256"],
    )
    service_module.verify_v4_prepare_plan(plan)
    ids = set(service_module.non_holdout_condition_ids(plan))

    assert "final-holdout" not in ids
    assert "final-train" in ids
    assert "final-validation" in ids
    assert {f"test-{index}" for index in range(5)} <= ids

    changed = dict(plan)
    changed["market_count"] = 999
    with pytest.raises(service_module.V4PrepareIntegrityError, match="plan_sha256"):
        service_module.verify_v4_prepare_plan(changed)


def test_v4_model_families_keep_short_and_full_context_distinct() -> None:
    row_predictors = {
        name: 0.1 for name in FROZEN_V4_GATE_B_CONFIG.predictor_names
    }
    row_predictors.update(
        {
            "missing__coinbase_market_start_missing": 0.0,
            "missing__coinbase_market_start_stale": 0.0,
            "missing__coinbase_current_missing": 0.0,
            "missing__coinbase_current_stale": 0.0,
            "missing__coinbase_regime_trailing_60m_missing": 0.0,
            "missing__regime_trend_score_missing": 0.0,
            "pm_up_best_ask": 0.55,
        }
    )

    single = service_module.model_predictor_names(
        row_predictors,
        "single_feature_btc_logistic",
    )
    short = service_module.model_predictor_names(
        row_predictors,
        "short_context_v4_logistic",
    )
    full = service_module.model_predictor_names(
        row_predictors,
        "full_v4_logistic",
    )

    assert single == (
        "coinbase_return_from_market_start",
        "missing__coinbase_market_start_missing",
        "missing__coinbase_market_start_stale",
        "missing__coinbase_current_missing",
        "missing__coinbase_current_stale",
    )
    assert set(FROZEN_V4_GATE_B_CONFIG.short_context_predictor_names) <= set(short)
    assert "coinbase_return_60m" not in short
    assert "missing__coinbase_regime_trailing_60m_missing" not in short
    assert set(FROZEN_V4_GATE_B_CONFIG.predictor_names) <= set(full)
    assert "missing__coinbase_regime_trailing_60m_missing" in full
    assert all("pm_" not in name and "polymarket" not in name.lower() for name in full)


def test_probability_slices_keep_sparse_regime_and_side_evidence_visible() -> None:
    rows = (
        _row("bull-up", target=1, regime="bull"),
        _row("bear-down", target=0, regime="bear"),
        _row("mixed-up", target=1, regime="sideways_mixed"),
    )
    report = service_module.probability_slices(rows, (0.8, 0.2, 0.7))

    assert report["overall"]["market_count"] == 3
    assert report["overall"]["metrics"]["accuracy"] == 1.0
    assert report["bull"]["status"] == "insufficient_slice_evidence"
    assert report["up"]["market_count"] == 2
    assert report["regime_by_side"]["bear_down"]["market_count"] == 1


def test_v4_economic_report_includes_risk_and_execution_fields() -> None:
    rows = (
        _row("win", target=1),
        _row("loss", target=0),
    )
    books = {
        condition_id: policy_module.V4ExecutionBook(
            up_best_bid=0.39,
            up_best_ask=0.40,
            up_fresh=True,
            down_best_bid=0.59,
            down_best_ask=0.60,
            down_fresh=True,
        )
        for condition_id in ("win", "loss")
    }
    report = policy_module.evaluate_economics_v4(
        rows,
        {"win": 0.80, "loss": 0.80},
        books,
        fee_rate=0.07,
        slippage_buffer=0.01,
        min_edge=0.0,
    )

    assert report["trade_count"] == 2
    assert report["wins"] == 1
    assert report["losses"] == 1
    assert report["profit_factor"] is not None
    assert report["max_drawdown"] > 0
    assert report["max_losing_streak"] == 1
    assert report["execution_availability"]["executable_markets"] == 2


def test_prepare_loader_is_condition_id_scoped_and_has_no_holdout_query_path() -> None:
    source = inspect.getsource(service_module._load_non_holdout_dataset).lower()
    assert "condition_ids=requested" in source
    assert "prepare loaded a final-holdout label" in source
    assert "holdout" in source


def test_v4_cli_exposes_prepare_and_separate_holdout_command(
    tmp_path: Path,
) -> None:
    parser = cli_module.build_parser()
    prepare = parser.parse_args(
        [
            "prepare",
            "--plan",
            str(tmp_path / "plan.json"),
            "--output",
            str(tmp_path / "selection.json"),
            "--model-output",
            str(tmp_path / "model.joblib"),
        ]
    )
    assert prepare.command == "prepare"

    holdout = parser.parse_args(
        [
            "evaluate-holdout",
            "--plan",
            str(tmp_path / "plan.json"),
            "--selection",
            str(tmp_path / "selection.json"),
            "--model",
            str(tmp_path / "model.joblib"),
            "--output",
            str(tmp_path / "holdout.json"),
        ]
    )
    assert holdout.command == "evaluate-holdout"

    parser_source = inspect.getsource(cli_module.build_parser)
    assert "evaluate-holdout" in parser_source
