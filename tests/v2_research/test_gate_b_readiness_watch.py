from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_v2_gate_b_readiness_watch.py"


def _module():
    spec = importlib.util.spec_from_file_location("readiness_watch", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_readiness_watch_detects_gate_b_artifacts_by_path_without_reading_contents(
    tmp_path: Path,
) -> None:
    module = _module()
    run_dir = tmp_path / "phase14-v2-gate-b-example"
    run_dir.mkdir()
    holdout = run_dir / "holdout.json"
    holdout.write_bytes(b"this is deliberately not valid JSON")

    artifacts = module._gate_b_artifacts(tmp_path)

    assert artifacts == (holdout,)
    with pytest.raises(RuntimeError, match="gate_b_artifact_present"):
        module._require_no_gate_b_artifacts(tmp_path)


def test_readiness_watch_ignores_non_gate_b_evidence_files(tmp_path: Path) -> None:
    module = _module()
    run_dir = tmp_path / "phase14-v2-gate-b-example"
    run_dir.mkdir()
    (run_dir / "diagnostic.txt").write_text("safe", encoding="utf-8")
    (tmp_path / "other-holdout.json").write_text("safe", encoding="utf-8")

    assert module._gate_b_artifacts(tmp_path) == ()


def test_readiness_watch_compacts_feature_only_payload_without_candidate_dump() -> None:
    module = _module()
    payload = {
        "ready": False,
        "market_count": 496,
        "market_start_at": "2026-09-02T12:20:00+00:00",
        "market_end_at": "2026-09-09T20:10:00+00:00",
        "available_span_seconds": 633000.0,
        "minimum_contiguous_epoch_seconds": 64800.0,
        "required_ordinary_folds": 3,
        "analysis_start_at": None,
        "eligible_fold_count": 0,
        "final_holdout_market_count": 0,
        "analysis_start_attempt_count": 81,
        "candidate_rejections": [
            {
                "analysis_start_at": "2026-09-09T04:20:00+00:00",
                "reason": "train requires at least 24 markets; found 19",
            }
        ],
        "blocking_reason": "blocked",
        "would_plan_sha256": None,
        "labels_read": False,
        "plan_artifact_written": False,
        "selection_artifact_written": False,
        "holdout_touched": False,
        "coverage_input_sha256": "aab75574aa7faf18e65358353403e5ec1a2b89dd42424eb7b0e3329bf683b099",
        "freshness_candidates_seconds": [1, 2, 5, 10],
        "include_no_trade": True,
    }

    status = module._compact_status(payload)

    assert status["ready"] is False
    assert status["candidate_rejection_count"] == 1
    assert status["last_candidate_rejection"] == payload["candidate_rejections"][0]
    assert "candidate_rejections" not in status
    assert status["labels_read"] is False
    assert status["plan_artifact_written"] is False
    assert status["selection_artifact_written"] is False
    assert status["holdout_touched"] is False
    assert status["gate_b_artifact_count"] == 0


def test_readiness_watch_source_is_read_only_and_has_no_gate_b_execution_path() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    for required in (
        "SET TRANSACTION READ ONLY",
        "assess_gate_b_readiness",
        "automatic_promotion_boundary_changed",
        "gate_b_authorization_boundary_changed",
        "include_no_trade_boundary_changed",
        "PHASE14_V2_GATE_B_READINESS_WATCH=PASS",
        "HOLDOUT_TOUCHED=false",
        "os.replace",
    ):
        assert required in source

    for forbidden in (
        "prepare_gate_b",
        "evaluate_gate_b_holdout",
        "build_gate_b_plan",
        "metadata.create_all",
        "DELETE FROM",
        "TRUNCATE",
        "LIVE_TRADING_ENABLED=true",
    ):
        assert forbidden not in source


def test_readiness_watch_status_file_is_valid_json_and_atomic(tmp_path: Path) -> None:
    module = _module()
    path = tmp_path / "status" / "latest.json"
    payload = {"ready": False, "holdout_touched": False}

    module._write_status(path, payload)

    assert json.loads(path.read_text(encoding="utf-8")) == payload
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []
