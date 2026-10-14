"""Offline Stage B1 restore-readback contract: fixed in-memory fixtures only."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.rehearse_stage_b1_fake_service_restoration import (
    SCENARIOS,
    review_fake_restoration,
)

_FAKE_OK = "FAKE_READBACK_MATCH_UNVERIFIED_NO_GO"
_FAKE_FAIL = "FAKE_RESTORE_MISMATCH_NO_GO"


def test_known_fixture_restoration_matches_but_never_authorizes() -> None:
    report = review_fake_restoration("restored")
    assert report["status"] == _FAKE_OK
    assert all(report["checks"].values())
    assert report["failed_checks"] == []
    assert report["fake_readback_after_restore_attempt"] is True
    for field in (
        "actual_process_tree_termination_proven",
        "actual_db_thread_quiescence_proven",
        "actual_systemd_restoration_proven",
        "actual_host_identity_attested",
        "operator_launcher_available",
        "production_execution_authorized",
        "live_trading_enabled",
    ):
        assert report[field] is False


@pytest.mark.parametrize(
    ("scenario", "failed_check"),
    [
        ("ack_only", "service_active"),
        ("wrong_unit", "service_unit_matches_baseline"),
        ("wrong_code", "recorder_source_matches_baseline"),
        ("extra_restart", "expected_single_restart"),
        ("unhealthy", "service_healthy"),
        ("db_worker_pending", "no_known_fake_db_workers"),
        ("descendant_pending", "no_known_fake_descendants"),
        ("stale_readback", "fresh_separate_readback"),
        ("no_ack", "restore_ack_received"),
        ("restore_error", "restore_ack_received"),
        ("observer_unavailable", "fresh_separate_readback"),
    ],
)
def test_fake_restore_adversarial_scenarios_fail_closed(
    scenario: str, failed_check: str
) -> None:
    result = review_fake_restoration(scenario)
    assert result["status"] == _FAKE_FAIL
    assert not result["checks"][failed_check]
    assert failed_check in result["failed_checks"]
    assert result["production_execution_authorized"] is False
    assert result["actual_systemd_restoration_proven"] is False
    assert result["actual_db_thread_quiescence_proven"] is False


def test_reported_restore_success_does_not_override_independent_fake_readback() -> None:
    bad = review_fake_restoration("ack_only")
    assert bad["checks"]["restore_ack_received"] is True
    assert bad["status"] == _FAKE_FAIL
    stale = review_fake_restoration("stale_readback")
    assert stale["checks"]["service_healthy"] is True
    assert stale["checks"]["service_unit_matches_baseline"] is True
    assert stale["checks"]["fresh_separate_readback"] is False
    assert stale["status"] == _FAKE_FAIL


def test_every_fixed_scenario_has_strict_sanitized_schema() -> None:
    keys: set[str] | None = None
    checks: set[str] | None = None
    for scenario in SCENARIOS:
        result = review_fake_restoration(scenario)
        if keys is None:
            keys = set(result)
            checks = set(result["checks"])
        assert set(result) == keys
        assert set(result["checks"]) == checks
        assert len(json.dumps(result)) < 2048
        assert not any(
            secret in json.dumps(result)
            for secret in ("password", "private_key", "systemctl", "sudo", "PID=")
        )
        assert result["production_execution_authorized"] is False


def test_unlisted_scenario_rejected_without_fake_state_creation() -> None:
    with pytest.raises(ValueError, match="only fixed synthetic"):
        review_fake_restoration("real_host")


def test_cli_only_emits_sanitized_fixed_fixture_result() -> None:
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts" / "rehearse_stage_b1_fake_service_restoration.py"
    )
    run = subprocess.run(
        [sys.executable, str(script), "--scenario", "stale_readback"],
        capture_output=True, text=True, timeout=8, check=True,
    )
    obj = json.loads(run.stdout)
    assert obj["status"] == _FAKE_FAIL
    assert obj["checks"]["fresh_separate_readback"] is False
    assert obj["production_execution_authorized"] is False
    assert not run.stderr
