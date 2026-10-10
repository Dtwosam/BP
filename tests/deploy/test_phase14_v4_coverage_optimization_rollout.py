"""Non-mutating safety contracts for the successor V4 coverage rollout."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
READINESS = ROOT / "scripts/deploy/phase14_v4_coverage_optimization_readiness_cloudshell.sh"
CONTROLLER = ROOT / "scripts/deploy/phase14_v4_coverage_optimization_rollout_cloudshell.sh"
HOST = ROOT / "scripts/deploy/phase14_v4_coverage_optimization_rollout_host.sh"


def test_all_successor_scripts_parse_without_execution() -> None:
    for script in (READINESS, CONTROLLER, HOST):
        done = subprocess.run(
            ["bash", "-n", str(script)], cwd=ROOT,
            capture_output=True, text=True, check=False,
        )
        assert done.returncode == 0, (script, done.stderr)


def test_readiness_pins_actual_bounded_runtime_and_fails_on_drift() -> None:
    source = READINESS.read_text()
    assert "OLD_RUNTIME_COMMIT=e7a21462374a1c19beae878c98ca319f13fb2d69" in source
    assert "EXPECTED_DEPLOYED=a352c66ec0110925727bc40de767ee4ba981f965" in source
    assert "EXPECTED_OLD_TARGET=/var/lib/bp/runtime/v4-forward-$OLD_RUNTIME_COMMIT" in source
    assert 'git show "$OLD_RUNTIME_COMMIT:src/bp_engine/features/v4_forward.py"' in source
    assert 'TARGET="$(sudo -n readlink -f "$LINK")"' in source
    assert '"$TARGET" == "$EXPECTED_OLD_TARGET"' in source
    assert "OLD_V4_RUNTIME_HASH_MISMATCH" in source
    assert "candidate_not_optimized" in source
    assert "SCHEDULED_COMMITS=" in source
    assert "COVERAGE_STAGE_TIMEOUTS=" in source
    assert "POST_COMMIT_EXIT_TIMEOUTS=" in source
    assert "UNEXPECTED_FAILURE_EVENTS=" in source
    assert "OLD_V4_RUNTIME_HEALTH=DEGRADED_COVERAGE_AND_EXIT_TIMEOUTS" in source
    assert "RECENT_30M_COMMITS=" in source
    assert "READINESS_SCOPE=OPTIMIZED_V4_COVERAGE_REMEDIATION_ONLY" in source
    assert "V4_TIMER_NOT_ACTIVE" in source
    assert "V4_TIMER_NOT_ENABLED" in source
    assert "PHASE14_V4_COVERAGE_OPTIMIZATION_READINESS=PASS" in source


def test_readiness_makes_only_read_only_host_and_db_calls() -> None:
    source = READINESS.read_text().lower()
    for forbidden in (
        "systemctl start ", "systemctl stop ", "systemctl restart ",
        "systemctl enable ", "systemctl disable ", "systemctl reset-failed ",
        "git checkout", "git reset", "git switch", "git pull", "git fetch",
        "docker exec", "docker compose", "create table", "alter table",
        "insert into", "update market_features", "delete from",
        "truncate ", "gcloud compute scp", "rollback_armed=1",
        "preflight_only=false",
    ):
        assert forbidden not in source, forbidden
    for necessary in (
        "default_transaction_read_only=on",
        "statement_timeout=2000",
        "RECORDER_AND_DB_SAFETY=PASS",
        "AUTOMATIC_PROMOTION=DISABLED",
        "PRODUCTION_MUTATION=false",
        "DEPLOYMENT_EXECUTED=false",
        "ROLLOUT_AUTHORIZED=false",
    ):
        assert necessary.lower() in source


def test_new_controller_is_local_only_by_default_and_newly_approved() -> None:
    source = CONTROLLER.read_text()
    assert 'PREFLIGHT_ONLY="${PHASE14_V4_COVERAGE_ROLLOUT_PREFLIGHT_ONLY:-true}"' in source
    assert "PHASE14_V4_COVERAGE_ROLLOUT_PREFLIGHT=PASS" in source
    assert '[[ "$APPROVAL" == "$EXPECTED_APPROVAL" ]] || fail "approval_mismatch"' in source
    assert "I_APPROVE_PHASE14_V4_COVERAGE_OPTIMIZATION:" in source
    assert "EXPECTED_OLD_TARGET=/var/lib/bp/runtime/v4-forward-$OLD_RUNTIME_COMMIT" in source
    assert "optimized_summary_not_used" in source
    assert "git archive --format=tar" in source
    assert "git show" in source
    assert "sha256sum" in source
    preflight = source.split('if [[ "$PREFLIGHT_ONLY" == true ]]; then', 1)[1].split(
        "\nfi", 1
    )[0]
    for label in ("PRODUCTION_HOST_CONTACTED=false", "PRODUCTION_MUTATION=false",
                  "DATABASE_WRITES=false", "ROLLOUT_AUTHORIZED=false", "exit 0"):
        assert label in preflight
    assert "gcloud compute ssh" not in preflight
    assert "gcloud compute scp" not in preflight


def test_host_safety_and_rollback_bound_to_existing_runtime() -> None:
    source = HOST.read_text()
    for required in (
        "v4-forward-e7a21462374a1c19beae878c98ca319f13fb2d69",
        "I_APPROVE_PHASE14_V4_COVERAGE_OPTIMIZATION",
        "optimized_summary_not_used", "optimized_summary_missing",
        "ROLLBACK_ARMED=1", "old_oneshot_did_not_quiesce",
        "V4_OLD_ONESHOT_QUIESCED=true",
        'systemctl stop "$TIMER"', 'systemctl start "$TIMER"',
        "PHASE14_V4_COVERAGE_ROLLBACK=PASS",
        "INCOMPLETE_OPERATOR_ACTION_REQUIRED",
        "default_transaction_read_only=on",
        'assert s.recorder_priority_batch_size == 20',
        'assert c.execute(text("SHOW shared_buffers")).scalar_one() == "128MB"',
        'cycle["eligible_targets"] in (0, 1)',
        'cycle["planned_rows"] == 0',
        'cycle["planned_rows"] == 4',
        'assert after == before',
        'after - before == cycle["inserted"]',
        "V4_ZERO_BACKLOG_NOOP_VALIDATED=true",
        "future_cutoff_violation_count",
        "polymarket_predictor_key_count", "regime_invariant_violation_count",
        "automatic_promotion", "core_service_pid_changed",
        "postgres_container_changed",
        "PHASE14_V4_COVERAGE_ROLLOUT=PASS",
    ):
        assert required in source, required
    assert source.index('systemctl stop "$TIMER"', source.index("# Serialize separate")) < (
        source.index('atomic_switch "$VERSION_DIR"')
    )
    assert source.index('atomic_switch "$VERSION_DIR"') < source.index(
        "timeout --kill-after=5s 110s"
    )
    for forbidden in (
        "systemctl stop bp-recorder", "systemctl restart bp-recorder",
        "systemctl restart bp-postgres", "drop table",
        "delete from market_features", "truncate market_features",
        "live_trading_enabled=true", "automatic_promotion=true",
    ):
        assert forbidden not in source.lower()


def test_scripts_fail_closed_before_host_contact_without_approval() -> None:
    no_head = subprocess.run(
        ["bash", str(CONTROLLER)], cwd=ROOT, env={"PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, check=False,
    )
    assert no_head.returncode != 0
    assert "exact_head_required" in no_head.stderr

    no_args = subprocess.run(
        ["bash", str(HOST)], cwd=ROOT,
        capture_output=True, text=True, check=False,
    )
    assert no_args.returncode == 2
    assert "HOST_GATE=FAIL:arguments" in no_args.stderr


def _classify(journal: str) -> subprocess.CompletedProcess[str]:
    """Exercise exactly the embedded AWK classifier, with no remote contact."""
    source = READINESS.read_text()
    section = source.split(
        "echo '=== V4 SCHEDULED CYCLE HEALTH (SINCE BOUNDED ROLLOUT) ==='", 1
    )[1].split("echo '=== RECENT SUCCESSFUL COMMITS (30 MINUTES) ==='", 1)[0]
    match = re.search(r"\nawk '\n(.*?)\n'\s*$", section, re.DOTALL)
    assert match is not None
    return subprocess.run(
        ["awk", match.group(1)], input=journal,
        capture_output=True, text=True, check=False,
    )


def _cycle(pending: int = 20, timeout: bool = False) -> str:
    stages = [
        "V4_FORWARD_STAGE=discovery_start",
        f"V4_FORWARD_STAGE=generation_start selected=1 remaining={pending}",
        "V4_FORWARD_STAGE=generation_complete inserted=4",
        "V4_FORWARD_STAGE=coverage_start",
    ]
    if timeout:
        stages += [
            "bp-v4-forward-coverage.service: start operation timed out. Terminating.",
            "bp-v4-forward-coverage.service: Failed with result 'timeout'.",
        ]
    else:
        stages += [
            "V4_FORWARD_STAGE=coverage_complete",
            "V4_FORWARD_STAGE=committed",
        ]
    return "\n".join(stages) + "\n"


def test_readiness_classifies_known_coverage_timeout_without_waiving_it() -> None:
    result = _classify(_cycle(392) + _cycle(34, timeout=True) + _cycle(27))
    assert result.returncode == 0, result.stdout + result.stderr
    for item in (
        "SCHEDULED_COMMITS=2",
        "COVERAGE_STAGE_TIMEOUTS=1",
        "UNEXPECTED_FAILURE_EVENTS=0",
        "FIRST_PENDING_OBSERVED=392",
        "LAST_PENDING_OBSERVED=27",
        "OLD_V4_RUNTIME_HEALTH=DEGRADED_COVERAGE_AND_EXIT_TIMEOUTS",
        "V4_COVERAGE_TIMEOUT_CLASSIFICATION=PASS",
    ):
        assert item in result.stdout


@pytest.mark.parametrize(
    "unexpected",
    [
        # A timeout during generation is not the confirmed coverage failure.
        "\n".join([
            "V4_FORWARD_STAGE=discovery_start",
            "V4_FORWARD_STAGE=generation_start selected=1 remaining=19",
            "bp-v4-forward-coverage.service: start operation timed out. Terminating.",
            "bp-v4-forward-coverage.service: Failed with result 'timeout'.",
        ]) + "\n",
        # A timeout after coverage_complete cannot be classified as expected.
        "\n".join([
            "V4_FORWARD_STAGE=discovery_start",
            "V4_FORWARD_STAGE=generation_start selected=1 remaining=19",
            "V4_FORWARD_STAGE=generation_complete inserted=4",
            "V4_FORWARD_STAGE=coverage_start",
            "V4_FORWARD_STAGE=coverage_complete",
            "bp-v4-forward-coverage.service: start operation timed out. Terminating.",
            "bp-v4-forward-coverage.service: Failed with result 'timeout'.",
        ]) + "\n",
        # Orphan/incorrectly paired failure results block readiness.
        "bp-v4-forward-coverage.service: Failed with result 'timeout'.\n",
        "bp-v4-forward-coverage.service: Failed with result 'exit-code'.\n",
        "Traceback (most recent call last):\n",
        "V4_FORWARD_STAGE=coverage_complete\n",
        # A systemd timeout without the matching failed-result line also blocks.
        "\n".join([
            "V4_FORWARD_STAGE=discovery_start",
            "V4_FORWARD_STAGE=generation_start selected=1 remaining=19",
            "V4_FORWARD_STAGE=generation_complete inserted=4",
            "V4_FORWARD_STAGE=coverage_start",
            "bp-v4-forward-coverage.service: start operation timed out. Terminating.",
        ]) + "\n",
    ],
)
def test_readiness_blocks_unexpected_failures(unexpected: str) -> None:
    result = _classify(_cycle(392) + unexpected)
    assert result.returncode != 0
    assert "V4_COVERAGE_TIMEOUT_CLASSIFICATION=FAIL" in result.stdout


def test_readiness_blocks_backlog_regression_and_all_timeout_history() -> None:
    assert _classify(_cycle(20) + _cycle(21)).returncode != 0
    assert _classify(_cycle(19, timeout=True)).returncode != 0


def test_readiness_allows_inflight_unfinished_cycle_but_not_failure() -> None:
    inflight = "\n".join([
        "V4_FORWARD_STAGE=discovery_start",
        "V4_FORWARD_STAGE=generation_start selected=1 remaining=19",
        "V4_FORWARD_STAGE=generation_complete inserted=4",
        "V4_FORWARD_STAGE=coverage_start",
    ]) + "\n"
    result = _classify(_cycle(20) + inflight)
    assert result.returncode == 0
    assert "OLD_V4_RUNTIME_HEALTH=NO_OBSERVED_FAILURES" in result.stdout


def _postcommit_timeout(elapsed: float = 116.4, pending: int = 19) -> str:
    """Model the four observed completed-but-timed-out old V4 cycles."""
    return "\n".join([
        "V4_FORWARD_STAGE=discovery_start",
        f"V4_FORWARD_STAGE=generation_start selected=1 remaining={pending}",
        "V4_FORWARD_STAGE=generation_complete inserted=4",
        "V4_FORWARD_STAGE=coverage_start",
        f"V4_FORWARD_STAGE=coverage_complete elapsed_seconds={elapsed - 0.04}",
        f"V4_FORWARD_STAGE=committed elapsed_seconds={elapsed}",
        "bp-v4-forward-coverage.service: start operation timed out. Terminating.",
        "bp-v4-forward-coverage.service: Failed with result 'timeout'.",
    ]) + "\n"


def test_readiness_classifies_narrow_postcommit_exit_timeout_as_degraded() -> None:
    result = _classify(
        _cycle(392) + _cycle(34, timeout=True)
        + _postcommit_timeout(116.4, 21) + _cycle(0)
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SCHEDULED_COMMITS=3" in result.stdout
    assert "COVERAGE_STAGE_TIMEOUTS=1" in result.stdout
    assert "POST_COMMIT_EXIT_TIMEOUTS=1" in result.stdout
    assert "UNEXPECTED_FAILURE_EVENTS=0" in result.stdout
    assert "OLD_V4_RUNTIME_HEALTH=DEGRADED_COVERAGE_AND_EXIT_TIMEOUTS" in result.stdout


@pytest.mark.parametrize(
    "invalid",
    [
        # Earlier-than-observed post-commit exit hangs must not be waived.
        _postcommit_timeout(55.0),
        _postcommit_timeout(109.9),
        _postcommit_timeout(120.0),
        # Complete logs still require exactly one systemd timeout and result pair.
        _postcommit_timeout().replace(
            "bp-v4-forward-coverage.service: Failed with result 'timeout'.\n", ""
        ),
        _postcommit_timeout().replace(
            "bp-v4-forward-coverage.service: start operation timed out. Terminating.\n", ""
        ),
        _postcommit_timeout().replace("elapsed_seconds=116.4", "elapsed_unknown"),
        _postcommit_timeout().replace(
            "bp-v4-forward-coverage.service: Failed with result 'timeout'.",
            "bp-v4-forward-coverage.service: Failed with result 'exit-code'.",
        ),
        # A post-coverage but pre-commit timeout is not a post-commit timeout.
        "\n".join([
            "V4_FORWARD_STAGE=discovery_start",
            "V4_FORWARD_STAGE=generation_start selected=1 remaining=19",
            "V4_FORWARD_STAGE=generation_complete inserted=4",
            "V4_FORWARD_STAGE=coverage_start",
            "V4_FORWARD_STAGE=coverage_complete",
            "bp-v4-forward-coverage.service: start operation timed out. Terminating.",
            "bp-v4-forward-coverage.service: Failed with result 'timeout'.",
        ]) + "\n",
    ],
)
def test_readiness_rejects_unexplained_postcommit_failure(invalid: str) -> None:
    result = _classify(_cycle(392) + invalid)
    assert result.returncode != 0
    assert "V4_COVERAGE_TIMEOUT_CLASSIFICATION=FAIL" in result.stdout


def _host_validation(
    tmp_path: Path,
    *,
    eligible: int,
    planned: int,
    inserted: int,
    existing: int,
    remaining: int,
    before: int,
    after: int,
    future_cutoff: int = 0,
) -> subprocess.CompletedProcess[str]:
    """Execute only the host's JSON acceptance snippet with isolated fixtures."""
    source = HOST.read_text()
    marker = (
        '/opt/bp/.venv/bin/python - "$BEFORE" "$AFTER" '
        '"$EVIDENCE/cycle.json" <<\'PY\''
    )
    snippet = source.split(marker, 1)[1].split("\nPY", 1)[0]
    cycle = {
        "epoch": "2026-09-20T12:40:53+00:00",
        "eligible_targets": eligible,
        "planned_rows": planned,
        "inserted": inserted,
        "existing": existing,
        "remaining_pending_targets": remaining,
        "future_cutoff_violation_count": future_cutoff,
        "polymarket_predictor_key_count": 0,
        "regime_invariant_violation_count": 0,
        "policy_selected": False,
        "training_run": False,
        "automatic_promotion": False,
    }
    payload = tmp_path / "cycle.json"
    payload.write_text(json.dumps(cycle))
    return subprocess.run(
        [sys.executable, "-c", snippet, str(before), str(after), str(payload)],
        capture_output=True, text=True, check=False,
    )


def test_zero_backlog_validation_requires_strict_noop(tmp_path: Path) -> None:
    result = _host_validation(
        tmp_path, eligible=0, planned=0, inserted=0,
        existing=0, remaining=0, before=20000, after=20000,
    )
    assert result.returncode == 0, result.stderr
    assert "V4_ZERO_BACKLOG_NOOP_VALIDATED=true" in result.stdout

    # The existing one-market/four-row validation contract is retained.
    generated = _host_validation(
        tmp_path, eligible=1, planned=4, inserted=4,
        existing=0, remaining=4, before=20000, after=20004,
    )
    assert generated.returncode == 0, generated.stderr

    for changes in (
        {"after": 20001}, {"planned": 4}, {"inserted": 1},
        {"existing": 1}, {"remaining": 1}, {"eligible": 2},
        {"future_cutoff": 1},
    ):
        args = dict(
            eligible=0, planned=0, inserted=0, existing=0,
            remaining=0, before=20000, after=20000,
        )
        args.update(changes)
        invalid = _host_validation(tmp_path, **args)
        assert invalid.returncode != 0, changes
