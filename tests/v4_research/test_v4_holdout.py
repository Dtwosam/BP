from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import joblib
import pytest

from bp_engine.features.hashing import canonical_hash
from bp_engine.modeling.models import DatasetSnapshot, SupervisedRow
from bp_engine.v4_research import cli as cli_module
from bp_engine.v4_research import holdout as holdout_module
from bp_engine.v4_research import service as service_module
from bp_engine.v4_research.config import (
    FROZEN_V4_GATE_B_CONFIG,
    v4_gate_b_config_payload,
)
from bp_engine.v4_research.policy import V4ExecutionBook


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
        "research_plan_version": FROZEN_V4_GATE_B_CONFIG.research_plan_version,
        "dataset_version": FROZEN_V4_GATE_B_CONFIG.dataset_version,
        "feature_version": FROZEN_V4_GATE_B_CONFIG.feature_version,
        "label_version": FROZEN_V4_GATE_B_CONFIG.label_version,
        "horizon_seconds": FROZEN_V4_GATE_B_CONFIG.horizon_seconds,
        "feature_offsets_seconds": list(
            FROZEN_V4_GATE_B_CONFIG.feature_offsets_seconds
        ),
        "epoch_start": FROZEN_V4_GATE_B_CONFIG.epoch_start.isoformat(),
        "epoch_end": FROZEN_V4_GATE_B_CONFIG.epoch_end.isoformat(),
        "market_count": 15,
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


def _selection(
    plan: dict[str, object],
    *,
    artifact_sha: str,
    size_bytes: int = 123,
) -> dict[str, object]:
    calibration_fit = {
        "method": "identity",
        "intercept": None,
        "coefficient": None,
    }
    payload: dict[str, object] = {
        "research_plan_version": FROZEN_V4_GATE_B_CONFIG.research_plan_version,
        "stage": "ordinary_selection_frozen",
        "dataset_version": FROZEN_V4_GATE_B_CONFIG.dataset_version,
        "feature_version": FROZEN_V4_GATE_B_CONFIG.feature_version,
        "label_version": FROZEN_V4_GATE_B_CONFIG.label_version,
        "authorized_plan_sha256": plan["plan_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "readiness_input_sha256": plan["readiness_input_sha256"],
        "feature_manifest_sha256": plan["feature_manifest_sha256"],
        "dataset_sha256_non_holdout": "d" * 64,
        "config": service_module._json_safe_config(FROZEN_V4_GATE_B_CONFIG),
        "forecast_candidates": [],
        "selected_forecast": {
            "candidate": "full_v4_xgboost",
            "offset_seconds": 240,
            "calibration_method": "identity",
            "aggregate_validation": {},
        },
        "edge_selection": {
            "policy": "trade_threshold",
            "min_edge": 0.05,
        },
        "folds": [{"index": index} for index in range(5)],
        "final": {
            "membership_sha256": plan["final"]["membership_sha256"],
            "train_condition_ids": ["final-train"],
            "validation_condition_ids": ["final-validation"],
            "holdout_condition_ids": ["final-holdout"],
            "selected_forecast": {
                "candidate": "full_v4_xgboost",
                "offset_seconds": 240,
                "calibration_method": "identity",
                "calibration_fit": calibration_fit,
                "model_config": {},
            },
            "frozen_edge_policy": {
                "policy": "trade_threshold",
                "min_edge": 0.05,
            },
            "validation_forecast": {},
            "validation_economics": {},
        },
        "labels_read_non_holdout": True,
        "holdout_labels_read": False,
        "holdout_evaluated": False,
        "training_performed": True,
        "policy_selected": True,
        "automatic_promotion": False,
        "activation_performed": False,
        "model_artifact": {
            "candidate": "full_v4_xgboost",
            "family": "xgboost",
            "file_name": "model.joblib",
            "size_bytes": size_bytes,
            "sha256": artifact_sha,
            "library_version": "test",
        },
    }
    payload["selection_sha256"] = canonical_hash(payload)
    return payload


def _bundle(plan: dict[str, object]) -> dict[str, object]:
    return {
        "candidate": "full_v4_xgboost",
        "family": "xgboost",
        "offset_seconds": 240,
        "predictor_names": ("coinbase_return_from_market_start",),
        "imputer": object(),
        "estimator": object(),
        "config": {},
        "research_plan_version": FROZEN_V4_GATE_B_CONFIG.research_plan_version,
        "plan_sha256": plan["plan_sha256"],
        "dataset_sha256_non_holdout": "d" * 64,
        "calibration_fit": {
            "method": "identity",
            "intercept": None,
            "coefficient": None,
        },
        "calibration_method": "identity",
        "selected_min_edge": 0.05,
        "edge_policy": "trade_threshold",
        "fee_rate": FROZEN_V4_GATE_B_CONFIG.fee_rate,
        "slippage_buffer": FROZEN_V4_GATE_B_CONFIG.slippage_buffer,
        "max_selected_book_age_seconds": (
            FROZEN_V4_GATE_B_CONFIG.max_selected_book_age_seconds
        ),
    }


def _row() -> SupervisedRow:
    start = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
    return SupervisedRow(
        condition_id="final-holdout",
        slug="btc-updown-5m-final-holdout",
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=start.replace(minute=5),
        feature_at=start.replace(minute=4),
        feature_offset_seconds=240,
        predictors={
            "coinbase_return_from_market_start": 0.01,
            "regime_bull": 1.0,
            "regime_bear": 0.0,
            "regime_sideways_mixed": 0.0,
        },
        target=1,
        feature_hash="1" * 64,
        input_fingerprint="2" * 64,
    )


def test_holdout_inputs_are_bound_to_frozen_v4_selection_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    selection = _selection(plan, artifact_sha="c" * 64)
    bundle = _bundle(plan)

    monkeypatch.setattr(
        service_module,
        "AUTHORIZED_V4_GATE_B_V2_PLAN_SHA256",
        plan["plan_sha256"],
    )
    monkeypatch.setattr(
        holdout_module,
        "AUTHORIZED_V4_GATE_B_V2_SELECTION_SHA256",
        selection["selection_sha256"],
    )
    monkeypatch.setattr(
        holdout_module,
        "AUTHORIZED_V4_GATE_B_V2_MODEL_SHA256",
        "c" * 64,
    )
    monkeypatch.setattr(
        holdout_module,
        "AUTHORIZED_V4_GATE_B_V2_MODEL_SIZE_BYTES",
        123,
    )

    holdout_module.verify_v4_holdout_inputs(
        plan=plan,
        selection=selection,
        model_bundle=bundle,
        model_sha256="c" * 64,
        model_file_name="model.joblib",
        model_size_bytes=123,
    )

    tampered = dict(selection)
    tampered["holdout_labels_read"] = True
    tampered.pop("selection_sha256")
    tampered["selection_sha256"] = canonical_hash(tampered)
    monkeypatch.setattr(
        holdout_module,
        "AUTHORIZED_V4_GATE_B_V2_SELECTION_SHA256",
        tampered["selection_sha256"],
    )
    with pytest.raises(
        holdout_module.V4HoldoutIntegrityError,
        match="already records final-holdout label access",
    ):
        holdout_module.verify_v4_holdout_inputs(
            plan=plan,
            selection=tampered,
            model_bundle=bundle,
            model_sha256="c" * 64,
            model_file_name="model.joblib",
            model_size_bytes=123,
        )


def test_v4_holdout_evaluation_reports_metrics_without_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    selection = _selection(plan, artifact_sha="c" * 64)
    bundle = _bundle(plan)
    row = _row()
    dataset = DatasetSnapshot(
        dataset_version=FROZEN_V4_GATE_B_CONFIG.dataset_version,
        feature_version=FROZEN_V4_GATE_B_CONFIG.feature_version,
        label_version=FROZEN_V4_GATE_B_CONFIG.label_version,
        horizon_seconds=300,
        start=FROZEN_V4_GATE_B_CONFIG.epoch_start,
        end=FROZEN_V4_GATE_B_CONFIG.epoch_end,
        rows=(row,),
        predictor_names=tuple(row.predictors),
        dataset_sha256="e" * 64,
    )

    monkeypatch.setattr(
        service_module,
        "AUTHORIZED_V4_GATE_B_V2_PLAN_SHA256",
        plan["plan_sha256"],
    )
    monkeypatch.setattr(
        holdout_module,
        "AUTHORIZED_V4_GATE_B_V2_SELECTION_SHA256",
        selection["selection_sha256"],
    )
    monkeypatch.setattr(
        holdout_module,
        "AUTHORIZED_V4_GATE_B_V2_MODEL_SHA256",
        "c" * 64,
    )
    monkeypatch.setattr(
        holdout_module,
        "AUTHORIZED_V4_GATE_B_V2_MODEL_SIZE_BYTES",
        123,
    )
    monkeypatch.setattr(
        holdout_module,
        "_load_holdout_dataset",
        lambda connection, plan, config: dataset,
    )
    monkeypatch.setattr(
        holdout_module,
        "_raw_model_probabilities",
        lambda rows, model_bundle: (0.80,),
    )
    monkeypatch.setattr(
        holdout_module,
        "_calibrated_probabilities",
        lambda raw, model_bundle: raw,
    )
    monkeypatch.setattr(
        holdout_module,
        "_execution_books",
        lambda connection, rows, max_age_seconds: {
            "final-holdout": V4ExecutionBook(
                up_best_bid=0.39,
                up_best_ask=0.40,
                up_fresh=True,
                down_best_bid=0.59,
                down_best_ask=0.60,
                down_fresh=True,
            )
        },
    )

    payload = holdout_module.evaluate_v4_gate_b_holdout(
        object(),
        plan=plan,
        selection=selection,
        model_bundle=bundle,
        model_sha256="c" * 64,
        model_file_name="model.joblib",
        model_size_bytes=123,
    )

    assert payload["stage"] == "final_holdout_evaluated"
    assert payload["holdout_evaluation"]["forecast"]["metrics"]["market_count"] == 1
    assert payload["holdout_evaluation"]["economics"]["overall"]["trade_count"] == 1
    assert payload["holdout_labels_read"] is True
    assert payload["holdout_evaluated_once"] is True
    assert payload["model_refit_performed"] is False
    assert payload["threshold_tuning_performed"] is False
    assert payload["policy_reselection_performed"] is False
    assert payload["automatic_promotion"] is False
    assert payload["paper_activation_authorized"] is False
    assert payload["paper_activation_performed"] is False
    assert payload["live_trading_enabled"] is False
    assert payload["real_money_usd"] == 0


def test_model_loader_verifies_v4_artifact_bytes_before_use(tmp_path: Path) -> None:
    model_path = tmp_path / "model.joblib"
    joblib.dump({"candidate": "full_v4_xgboost"}, model_path)
    payload = model_path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    selection = {
        "model_artifact": {
            "file_name": "model.joblib",
            "size_bytes": len(payload),
            "sha256": digest,
        }
    }

    value, actual_digest, actual_size = cli_module._load_model_verified(
        str(model_path),
        selection,
    )
    assert value == {"candidate": "full_v4_xgboost"}
    assert actual_digest == digest
    assert actual_size == len(payload)

    changed = dict(selection)
    changed["model_artifact"] = dict(selection["model_artifact"])
    changed["model_artifact"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        cli_module._load_model_verified(str(model_path), changed)


def test_v4_holdout_cli_has_no_tuning_knobs_and_no_clobber_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = cli_module.build_parser()
    evidence = tmp_path / "evidence"
    evidence.mkdir()

    args = parser.parse_args(
        [
            "evaluate-holdout",
            "--plan",
            str(evidence / "plan.json"),
            "--selection",
            str(evidence / "selection.json"),
            "--model",
            str(evidence / "model.joblib"),
            "--output",
            str(evidence / "holdout.json"),
        ]
    )
    assert args.command == "evaluate-holdout"

    help_text = parser.format_help().lower()
    for forbidden in (
        "--min-edge",
        "--offset-seconds",
        "--candidate",
        "--calibration",
        "--fee-rate",
        "--slippage",
    ):
        assert forbidden not in help_text

    class _Engine:
        def dispose(self) -> None:
            pass

    monkeypatch.setattr(
        cli_module,
        "_settings",
        lambda args: type("Settings", (), {"database_url": "sqlite://"})(),
    )
    monkeypatch.setattr(cli_module, "create_engine", lambda url: _Engine())

    bad = parser.parse_args(
        [
            "evaluate-holdout",
            "--plan",
            str(evidence / "plan.json"),
            "--selection",
            str(evidence / "selection.json"),
            "--model",
            str(evidence / "model.joblib"),
            "--output",
            str(evidence / "holdout-copy.json"),
        ]
    )
    with pytest.raises(ValueError, match="must be named holdout.json"):
        cli_module._run(bad)


def test_v4_holdout_source_preserves_evaluation_only_boundary() -> None:
    source = Path(holdout_module.__file__).read_text(encoding="utf-8").lower()
    assert '"automatic_promotion": false' in source
    assert '"paper_activation_authorized": false' in source
    assert '"paper_activation_performed": false' in source
    assert '"live_trading_enabled": false' in source
    assert '"real_money_usd": 0' in source
    assert "model_refit_performed" in source
    assert "threshold_tuning_performed" in source
