from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = (
    ROOT / "scripts" / "deploy" / "phase14_v4_bounded_forward_rollout_cloudshell.sh"
)
HOST = ROOT / "scripts" / "deploy" / "phase14_v4_bounded_forward_rollout_host.sh"


def test_rollout_scripts_parse_without_running() -> None:
    for path in (CONTROLLER, HOST):
        result = subprocess.run(
            ["bash", "-n", str(path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{path}: {result.stderr}"


def test_rollout_defaults_to_non_mutating_local_preflight() -> None:
    source = CONTROLLER.read_text(encoding="utf-8")
    assert 'PREFLIGHT_ONLY="${PHASE14_V4_BOUNDED_ROLLOUT_PREFLIGHT_ONLY:-true}"' in source
    assert '[[ "$APPROVAL" == "$EXPECTED_APPROVAL" ]] || fail "approval_mismatch"' in source
    assert "EXPECTED_APPROVAL=" in source
    assert "EXPECTED_DEPLOYED=a352c66ec0110925727bc40de767ee4ba981f965" in source
    assert "EXPECTED_OLD_CODE_SHA256=e8882286a4fb92969d0e51ab75b81908deae1000fe26806a9491e75c18e4f846" in source
    assert "git archive --format=tar" in source
    assert "git checkout" not in source
    assert "git reset" not in source

    preflight = source.split('if [[ "$PREFLIGHT_ONLY" == true ]]; then', 1)[1].split(
        "\nfi", 1
    )[0]
    for marker in (
        "PHASE14_V4_BOUNDED_ROLLOUT_PREFLIGHT=PASS",
        "PRODUCTION_HOST_CONTACTED=false",
        "PRODUCTION_MUTATION=false",
        "DATABASE_WRITES=false",
        "ROLLOUT_AUTHORIZED=false",
        "exit 0",
    ):
        assert marker in preflight
    assert "gcloud compute ssh" not in preflight
    assert "gcloud compute scp" not in preflight


def test_root_half_stops_only_v4_timer_and_waits_for_idle_service() -> None:
    source = HOST.read_text(encoding="utf-8")
    for required in (
        "approval_invalid",
        "candidate_archive_sha256_changed",
        "host_script_sha256_changed",
        "old_runtime_code_changed",
        "concurrent_rollout",
        'systemctl stop "$TIMER"',
        'systemctl start "$TIMER"',
        "old_oneshot_did_not_quiesce",
        "V4_OLD_ONESHOT_QUIESCED=true",
        "atomic_switch",
        "ROLLBACK_ARMED=1",
        "PHASE14_V4_BOUNDED_ROLLBACK=PASS",
        "PHASE14_V4_BOUNDED_ROLLOUT=PASS",
        "ROLL_OUT_SAFETY_AND_DB_BASELINE=PASS",
    ):
        assert required in source

    # Stop the timer and confirm no active one-shot before switching symlinks
    # or running the isolated DB-writing validation cycle.
    assert source.index('systemctl stop "$TIMER"') < source.index(
        "V4_OLD_ONESHOT_QUIESCED=true"
    ) < source.index('atomic_switch "$VERSION_DIR"')
    assert source.index('atomic_switch "$VERSION_DIR"') < source.index(
        "timeout --kill-after=5s 110s"
    )

    for forbidden in (
        "systemctl stop bp-recorder",
        "systemctl restart bp-recorder",
        "systemctl stop bp-postgres",
        "systemctl restart bp-postgres",
        "systemctl start bp-v4-forward-coverage.service",
        "git -c \"$REPO\" checkout",
        "git -c \"$REPO\" reset",
        "drop table",
        "delete from market_features",
        "truncate market_features",
        "live_trading_enabled=true",
        "automatic_promotion=true",
    ):
        assert forbidden not in source.lower()


def test_bounded_cycle_must_commit_and_preserve_frozen_system() -> None:
    source = HOST.read_text(encoding="utf-8")
    for required in (
        "V4_FORWARD_MARKETS_PER_CYCLE = 1",
        "V4_FORWARD_STAGE=",
        "SELECT count(*) FROM market_features",
        'cycle["eligible_targets"]==1',
        'cycle["planned_rows"]==4',
        '1 <= cycle["inserted"] <= 4',
        'after - before == cycle["inserted"]',
        'cycle["remaining_pending_targets"] >= 0',
        "future_cutoff_violation_count",
        "polymarket_predictor_key_count",
        "regime_invariant_violation_count",
        "policy_selected",
        "training_run",
        "automatic_promotion",
        "recorder_checkout_changed",
        "core_service_pid_changed",
        "postgres_container_changed",
        "v4_service_unit_file_changed",
        "v4_timer_unit_file_changed",
        "EVIDENCE_PATH=",
    ):
        assert required in source

    assert "default_transaction_read_only=on" in source
    assert "assert s.recorder_priority_batch_size == 20" in source
    assert 'assert c.execute(text("SHOW shared_buffers")).scalar_one() == "128MB"' in source
    assert "PRODUCTION_MUTATION=true" in source


def test_host_without_explicit_args_does_not_run_rollout() -> None:
    result = subprocess.run(
        ["bash", str(HOST)], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 2
    assert "HOST_GATE=FAIL:arguments" in result.stderr
