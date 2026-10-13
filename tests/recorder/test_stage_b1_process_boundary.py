"""Offline POSIX process boundary tests; only hardcoded synthetic children run."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.rehearse_stage_b1_process_boundary import supervise_synthetic

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")


@pytest.mark.asyncio
async def test_cooperative_child_reaped_and_bounded_aggregate_valid() -> None:
    result = await supervise_synthetic("cooperative")
    assert result["scope"] == "offline_fixed_fake_child_only"
    assert result["scenario"] == "cooperative"
    assert result["termination"] == "exited"
    assert result["fixed_synthetic_aggregate_valid"] is True
    assert result["process_leader_reaped"] is True
    assert result["known_fixture_group_pipe_eof"] is True
    assert result["all_descendants_terminated_proven"] is False
    assert result["operator_launcher_available"] is False
    assert result["production_execution_authorized"] is False
    assert result["production_host_access"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    ["hang", "ignore_term"],
)
async def test_unresponsive_synthetic_children_are_stopped_and_reaped(
    scenario: str,
) -> None:
    result = await supervise_synthetic(
        scenario,
        wall_seconds=0.7,
        term_grace_seconds=0.15,
        kill_grace_seconds=1,
    )
    assert result["termination"] in {"sigterm", "sigkill"}
    assert result["process_leader_reaped"]
    assert result["known_fixture_group_pipe_eof"]
    assert not result["fixed_synthetic_aggregate_valid"]
    assert not result["all_descendants_terminated_proven"]


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["crash", "poisoned", "oversized"])
async def test_child_exception_and_malformed_report_rejected_without_leak(
    scenario: str,
) -> None:
    result = await supervise_synthetic(scenario)
    assert result["termination"] == "exited"
    assert result["process_leader_reaped"]
    assert result["known_fixture_group_pipe_eof"]
    assert result["fixed_synthetic_aggregate_valid"] is False
    assert "SECRET_ERROR_DETAILS" not in json.dumps(result)
    assert "SECRET_VALUE" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected_termination"),
    [
        ("leader_exits_descendant", "descendant_sigterm"),
        ("leader_exits_descendant_ignores_term", "descendant_sigkill"),
    ],
)
async def test_successful_leader_with_live_descendant_is_never_accepted(
    scenario: str, expected_termination: str,
) -> None:
    result = await supervise_synthetic(
        scenario, wall_seconds=0.7, term_grace_seconds=0.15,
        kill_grace_seconds=1,
    )
    assert result["termination"] == expected_termination
    assert result["process_leader_reaped"] is True
    assert result["known_fixture_group_pipe_eof"] is True
    assert result["fixed_synthetic_aggregate_valid"] is False
    assert result["all_descendants_terminated_proven"] is False
    assert result["operator_launcher_available"] is False
    assert result["production_execution_authorized"] is False
    assert "pid" not in json.dumps(result).lower()


@pytest.mark.asyncio
async def test_unlisted_commands_and_invalid_limits_fail_before_spawn() -> None:
    with pytest.raises(ValueError, match="only named synthetic"):
        await supervise_synthetic("python -m bp_engine.recorder")
    for invalid in (0, -1, float("inf"), float("nan"), True, 5):
        with pytest.raises(ValueError, match="timeout"):
            await supervise_synthetic("cooperative", wall_seconds=invalid)
    with pytest.raises(ValueError, match="timeout"):
        await supervise_synthetic("cooperative", kill_grace_seconds=3)


def test_cli_prints_only_fixed_summary_not_child_report_or_command() -> None:
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "rehearse_stage_b1_process_boundary.py"
    )
    run = subprocess.run(
        [sys.executable, str(script), "--scenario", "cooperative"],
        capture_output=True, text=True, timeout=8, check=True,
    )
    result = json.loads(run.stdout)
    assert result["fixed_synthetic_aggregate_valid"]
    assert set(result) == {
        "report", "scope", "scenario", "termination", "process_leader_reaped",
        "fixed_synthetic_aggregate_valid", "all_descendants_terminated_proven",
        "known_fixture_group_pipe_eof",
        "production_execution_authorized", "production_host_access",
        "operator_launcher_available", "limitations",
    }
    assert "wire_messages_by_topic_class" not in run.stdout
    assert not run.stderr
