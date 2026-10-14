"""Regression tests for strictly local, pinned source-only B1 feasibility."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import review_stage_b1_operator_feasibility as feasibility


def _copies(tmp_path: Path) -> Path:
    for relative in feasibility.FILE_GIT_BLOBS:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(feasibility.ROOT / relative, target)
    return tmp_path


def test_current_repo_source_assumptions_match_reviewed_git_blobs() -> None:
    result = feasibility.review_source_feasibility()
    assert result["status"] == "PINNED_SOURCE_REVIEW_ONLY_NO_GO"
    assert all(result["checks"].values())
    assert result["file_count"] == 5
    assert result["missing_or_mismatched_checks"] == []
    assert result["checks"]["normal_recorder_entrypoint_no_b1_injection"]
    assert result["checks"]["repo_unit_restarts_recorder_always"]
    assert result["checks"]["repo_unit_has_60_second_stop_grace"]
    assert result["checks"]["repo_unit_lacks_approved_independent_b1_watchdog"]
    assert result["checks"]["database_sink_uses_asyncio_to_thread"]
    assert result["checks"]["normal_recorder_builder_creates_db_and_schema"]
    assert result["checks"]["one_shot_remains_separate_dormant_library"]
    for denied in (
        "on_host_identity_verified",
        "real_systemd_restoration_verified",
        "db_thread_quiescence_verified",
        "safe_existing_recorder_restart_candidate_available",
        "safe_parallel_full_recorder_candidate_available",
        "production_execution_authorized",
        "proposed_operator_implementation_approved",
    ):
        assert result[denied] is False


@pytest.mark.parametrize("relative", tuple(feasibility.FILE_GIT_BLOBS))
def test_any_pinned_source_edit_refuses_feasibility_claim(
    tmp_path: Path, relative: str,
) -> None:
    root = _copies(tmp_path)
    target = root / relative
    target.write_bytes(target.read_bytes() + b"\nUNTRUSTED_HOST_SECRET_CANARY\n")
    result = feasibility.review_source_feasibility(root=root)
    assert result["status"] == "PINNED_SOURCE_DRIFT_OR_MISMATCH_NO_GO"
    assert not any(result["checks"].values())
    assert set(result["missing_or_mismatched_checks"]) == set(feasibility.CHECK_NAMES)
    assert "UNTRUSTED_HOST_SECRET_CANARY" not in json.dumps(result)


def test_missing_source_and_symlink_fail_closed(tmp_path: Path) -> None:
    root = _copies(tmp_path)
    target = root / "deploy/systemd/bp-recorder.service"
    target.unlink()
    assert feasibility.review_source_feasibility(root=root)["status"] == (
        "PINNED_SOURCE_DRIFT_OR_MISMATCH_NO_GO"
    )
    target.symlink_to(feasibility.ROOT / "deploy/systemd/bp-recorder.service")
    assert feasibility.review_source_feasibility(root=root)["checks"][
        "exact_five_source_blobs_verified"
    ] is False


def test_overlarge_source_and_nonutf8_are_inconclusive(tmp_path: Path) -> None:
    root = _copies(tmp_path)
    target = root / "src/bp_engine/recorder/__main__.py"
    target.write_bytes(b"z" * (feasibility.MAX_FILE_BYTES + 1))
    assert feasibility.review_source_feasibility(root=root)["status"] == (
        "PINNED_SOURCE_DRIFT_OR_MISMATCH_NO_GO"
    )
    target.write_bytes(b"\xff\xfe")
    assert feasibility.review_source_feasibility(root=root)["status"] == (
        "PINNED_SOURCE_DRIFT_OR_MISMATCH_NO_GO"
    )


def test_operator_feasibility_cli_is_always_no_go_with_no_host_details() -> None:
    script = feasibility.ROOT / "scripts/review_stage_b1_operator_feasibility.py"
    run = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, check=False, timeout=10,
    )
    assert run.returncode == 2
    obj = json.loads(run.stdout)
    assert obj["status"] == "PINNED_SOURCE_REVIEW_ONLY_NO_GO"
    assert obj["production_execution_authorized"] is False
    assert obj["safe_existing_recorder_restart_candidate_available"] is False
    assert obj["safe_parallel_full_recorder_candidate_available"] is False
    assert not run.stderr
    assert len(run.stdout.encode("utf-8")) < 2500
    assert "/opt/bp" not in run.stdout
    assert "/etc/bp" not in run.stdout
