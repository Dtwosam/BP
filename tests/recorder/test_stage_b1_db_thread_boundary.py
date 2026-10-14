"""Only in-memory fake engine/repository; never contact a database."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.rehearse_stage_b1_db_thread_boundary import (
    rehearse_fake_thread_boundary,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected_outcome"),
    [
        ("late_success", "fake_committed"),
        ("late_failure", "fake_rolled_back"),
    ],
)
async def test_real_asyncio_to_thread_worker_can_finish_after_await_cancel(
    scenario: str, expected_outcome: str,
) -> None:
    result = await rehearse_fake_thread_boundary(scenario)
    assert result["scope"] == "fake_in_memory_transaction_actual_asyncio_to_thread_boundary"
    assert result["awaiting_task_cancelled"] is True
    assert result["fake_worker_pending_after_task_cancellation"] is True
    assert result["fake_worker_finished_after_release"] is True
    assert result["fake_transaction_outcome_after_release"] == expected_outcome
    assert result["writer_batches_cancelled_unresolved"] == 1
    assert result["writer_batches_in_flight_conservative"] == 1
    assert result["writer_batches_returned"] == 0
    assert result["writer_attempts_marked_committed"] == 0
    assert result["actual_database_commit_or_rollback_proven"] is False
    assert result["production_db_threads_terminated_proven"] is False
    assert result["systemd_restore_proven"] is False
    assert result["operator_launcher_available"] is False
    assert result["production_execution_authorized"] is False
    assert "secret" not in json.dumps(result)


@pytest.mark.asyncio
async def test_arbitrary_scenario_rejected_before_fake_thread_start() -> None:
    with pytest.raises(ValueError, match="only fixed fake"):
        await rehearse_fake_thread_boundary("production_database")


def test_cli_emits_only_fixed_sanitized_fake_accounting() -> None:
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts" / "rehearse_stage_b1_db_thread_boundary.py"
    )
    run = subprocess.run(
        [sys.executable, str(script), "--scenario", "late_success"],
        text=True, capture_output=True, timeout=9, check=True,
    )
    obj = json.loads(run.stdout)
    assert obj["fake_transaction_outcome_after_release"] == "fake_committed"
    assert obj["production_execution_authorized"] is False
    assert obj["production_db_threads_terminated_proven"] is False
    assert not run.stderr
    assert set(obj) == {
        "report", "scope", "scenario", "awaiting_task_cancelled",
        "fake_worker_pending_after_task_cancellation",
        "fake_worker_finished_after_release",
        "fake_transaction_outcome_after_release",
        "writer_batches_cancelled_unresolved",
        "writer_batches_in_flight_conservative",
        "writer_batches_returned",
        "writer_attempts_marked_committed",
        "actual_database_commit_or_rollback_proven",
        "production_db_threads_terminated_proven",
        "systemd_restore_proven",
        "operator_launcher_available",
        "production_execution_authorized",
    }
    assert len(run.stdout.encode("utf-8")) < 2048
