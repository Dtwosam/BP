from __future__ import annotations

import hashlib
from pathlib import Path

import joblib
import pytest

from bp_engine.features.hashing import canonical_hash
from bp_engine.v3_research import cli as cli_module
from bp_engine.v3_research import holdout as holdout_module
from bp_engine.v3_research import plan as plan_module
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
        "research_plan_version": FROZEN_V3_GATE_B_CONFIG.research_plan_version,
        "dataset_version": FROZEN_V3_GATE_B_CONFIG.dataset_version,
        "feature_version": FROZEN_V3_GATE_B_CONFIG.feature_version,
        "horizon_seconds": FROZEN_V3_GATE_B_CONFIG.horizon_seconds,
        "feature_offsets_seconds": list(
            FROZEN_V3_GATE_B_CONFIG.feature_offsets_seconds
        ),
        "epoch_start": FROZEN_V3_GATE_B_CONFIG.epoch_start.isoformat(),
        "epoch_end": FROZEN_V3_GATE_B_CONFIG.epoch_end.isoformat(),
        "market_count": 15,
        "readiness_input_sha256": "a" * 64,
        "config_sha256": canonical_hash(
            plan_module._config_payload(FROZEN_V3_GATE_B_CONFIG)
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


def _bundle(plan: dict[str, object]) -> dict[str, object]:
    return {
        "candidate": "training_prior",
        "family": "prior",
        "offset_seconds": 240,
        "probability": 0.5,
        "predictor_names": (),
        "config": {"weighted_market_prior": True},
        "calibration_fit": {
            "method": "identity",
            "intercept": None,
            "coefficient": None,
        },
        "research_plan_version": FROZEN_V3_GATE_B_CONFIG.research_plan_version,
        "plan_sha256": plan["plan_sha256"],
        "dataset_sha256_non_holdout": "d" * 64,
        "selected_min_edge": 0.075,
        "edge_policy": "trade_threshold",
        "fee_rate": FROZEN_V3_GATE_B_CONFIG.fee_rate,
        "slippage_buffer": FROZEN_V3_GATE_B_CONFIG.slippage_buffer,
        "max_selected_book_age_seconds": (
            FROZEN_V3_GATE_B_CONFIG.max_selected_book_age_seconds
        ),
    }


def _selection(
    plan: dict[str, object],
    *,
    artifact_sha: str,
    file_name: str = "model.joblib",
    size_bytes: int = 123,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "research_plan_version": FROZEN_V3_GATE_B_CONFIG.research_plan_version,
        "stage": "ordinary_selection_frozen",
        "dataset_version": FROZEN_V3_GATE_B_CONFIG.dataset_version,
        "feature_version": FROZEN_V3_GATE_B_CONFIG.feature_version,
        "label_version": FROZEN_V3_GATE_B_CONFIG.label_version,
        "plan_sha256": plan["plan_sha256"],
        "readiness_input_sha256": plan["readiness_input_sha256"],
        "feature_manifest_sha256": plan["feature_manifest_sha256"],
        "dataset_sha256_non_holdout": "d" * 64,
        "config": service_module._json_safe_config(FROZEN_V3_GATE_B_CONFIG),
        "folds": [{"index": index} for index in range(5)],
        "ordinary_validation_economics_passed": True,
        "final": {
            "membership_sha256": plan["final"]["membership_sha256"],
            "train_condition_ids": ["final-train"],
            "validation_condition_ids": ["final-validation"],
            "holdout_condition_ids": ["final-holdout"],
            "selected_forecast": {
                "candidate": "training_prior",
                "offset_seconds": 240,
                "calibration": {
                    "method": "identity",
                    "fit": {
                        "method": "identity",
                        "intercept": None,
                        "coefficient": None,
                    },
                },
            },
            "edge_selection": {
                "policy": "trade_threshold",
                "min_edge": 0.075,
            },
        },
        "labels_read_non_holdout": True,
        "holdout_labels_read": False,
        "holdout_evaluated": False,
        "training_performed": True,
        "automatic_promotion": False,
        "model_artifact": {
            "candidate": "training_prior",
            "family": "prior",
            "file_name": file_name,
            "size_bytes": size_bytes,
            "sha256": artifact_sha,
            "library_version": "test",
        },
    }
    payload["selection_sha256"] = canonical_hash(payload)
    return payload


def test_holdout_inputs_are_hash_bound_to_frozen_selection_and_model() -> None:
    plan = _plan()
    bundle = _bundle(plan)
    selection = _selection(plan, artifact_sha="c" * 64)

    holdout_module.verify_v3_holdout_inputs(
        plan=plan,
        selection=selection,
        model_bundle=bundle,
        model_sha256="c" * 64,
        model_file_name="model.joblib",
        model_size_bytes=123,
    )

    with pytest.raises(
        holdout_module.V3HoldoutIntegrityError,
        match="model artifact SHA-256 mismatch",
    ):
        holdout_module.verify_v3_holdout_inputs(
            plan=plan,
            selection=selection,
            model_bundle=bundle,
            model_sha256="e" * 64,
            model_file_name="model.joblib",
            model_size_bytes=123,
        )

    tampered = dict(selection)
    tampered["holdout_labels_read"] = True
    tampered.pop("selection_sha256")
    tampered["selection_sha256"] = canonical_hash(tampered)
    with pytest.raises(
        holdout_module.V3HoldoutIntegrityError,
        match="already records holdout label access",
    ):
        holdout_module.verify_v3_holdout_inputs(
            plan=plan,
            selection=tampered,
            model_bundle=bundle,
            model_sha256="c" * 64,
            model_file_name="model.joblib",
            model_size_bytes=123,
        )


def test_model_loader_verifies_bytes_before_use(tmp_path: Path) -> None:
    model_path = tmp_path / "model.joblib"
    joblib.dump({"candidate": "training_prior"}, model_path)
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
    assert value == {"candidate": "training_prior"}
    assert actual_digest == digest
    assert actual_size == len(payload)

    changed = dict(selection)
    changed["model_artifact"] = dict(selection["model_artifact"])
    changed["model_artifact"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        cli_module._load_model_verified(str(model_path), changed)


def test_holdout_cli_has_no_tuning_knobs_and_requires_canonical_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = cli_module.build_parser()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    plan_path = evidence / "plan.json"
    selection_path = evidence / "selection.json"
    model_path = evidence / "model.joblib"
    output_path = evidence / "holdout.json"

    args = parser.parse_args(
        [
            "evaluate-holdout",
            "--plan",
            str(plan_path),
            "--selection",
            str(selection_path),
            "--model",
            str(model_path),
            "--output",
            str(output_path),
        ]
    )
    assert args.command == "evaluate-holdout"

    help_text = parser.format_help().lower()
    for forbidden in (
        "min-edge",
        "offset-seconds",
        "candidate",
        "calibration",
        "fee-rate",
        "slippage",
    ):
        assert forbidden not in help_text

    monkeypatch.setattr(
        cli_module,
        "_settings",
        lambda args: type("Settings", (), {"database_url": "sqlite://"})(),
    )
    monkeypatch.setattr(cli_module, "create_engine", lambda url: object())

    bad = parser.parse_args(
        [
            "evaluate-holdout",
            "--plan",
            str(plan_path),
            "--selection",
            str(selection_path),
            "--model",
            str(model_path),
            "--output",
            str(evidence / "holdout-copy.json"),
        ]
    )
    with pytest.raises(ValueError, match="must be named holdout.json"):
        cli_module._run(bad)


def test_holdout_source_preserves_no_activation_boundary() -> None:
    source = Path(holdout_module.__file__).read_text(encoding="utf-8").lower()
    assert '"automatic_promotion": false' in source
    assert '"activation_performed": false' in source
    assert "model_refit_performed" in source
