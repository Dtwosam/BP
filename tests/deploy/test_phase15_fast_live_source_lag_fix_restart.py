from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_fast_live_source_lag_fix_restart_cloudshell.sh"
)


def test_source_lag_fix_restart_helper_is_shell_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_source_lag_fix_restart_helper_is_exact_and_fail_closed() -> None:
    text = HELPER.read_text(encoding="utf-8")

    for marker in (
        "I_ACCEPT_STAGE_AND_ACTIVATE_SOURCE_LAG_FIX",
        "9824a0b16f64b5018739a33634dc5e4dea673be8",
        "857e28c5ba47d56697d5936dd844b54db18dc0b8e3c60513358ffc2232ca7de4",
        "phase15-v3-fast-live-auto-continuous-v2-12h-2302945a-20261001T124442Z",
        "2302945a0fd6fe7f04654a6c7915767bdde5ef7d",
        "2026-10-02T00:44:42+00:00",
        "phase15-v3-fast-live-auto-continuous-v2-12h-9824a0b1-20261001T201044Z",
        "2026-10-02T08:10:44+00:00",
        '"source_of_truth_version"] == "0.14.186"',
        '"auto-telegram-continuous-v2"',
        '"max_consecutive_losses"] == 0',
        '"min_edge"] == 0.075',
        "working_tree_not_clean",
        "checkout_is_not_current_main",
        "release_archive_sha_mismatch",
        "release_archive_manifest_invalid",
        "old_runtime_authorization_binding_invalid",
        "sudo systemctl stop bp-phase15-fast-live-source.service",
        "source-lag-fix-restart",
        "sudo systemctl stop bp-phase15-fast-live-receiver.service",
        "/var/lib/bp-canary/fast-live/KILL",
        "I_ACCEPT_ROTATE_ZERO_ATTEMPT_SOURCE_LAG_SESSION",
        "phase15_v3_fast_live_cleanup_expired_cloudshell.sh",
        "phase15_v3_fast_live_stage_cloudshell.sh",
        "phase15_v3_fast_live_preflight_cloudshell.sh",
        "I_ACCEPT_CONTINUOUS_TELEGRAM_APPROVED_LIVE_SESSION",
        "phase15_v3_fast_live_activate_cloudshell.sh",
        "phase15_v3_fast_live_status_cloudshell.sh",
        "OLD_SESSION_NETWORK_ATTEMPTS=0",
        "MANUAL_ORDER_SUBMISSION_PERFORMED=false",
        "DIRECT_ORDER_COMMAND_EXECUTED=false",
    ):
        assert marker in text

    stop_source = text.index(
        "sudo systemctl stop bp-phase15-fast-live-source.service"
    )
    stop_receiver = text.index(
        "sudo systemctl stop bp-phase15-fast-live-receiver.service"
    )
    cleanup = text.index(
        'bash "$ROOT/scripts/deploy/phase15_v3_fast_live_cleanup_expired_cloudshell.sh"'
    )
    stage = text.index(
        'bash "$ROOT/scripts/deploy/phase15_v3_fast_live_stage_cloudshell.sh"'
    )
    preflight = text.index(
        'bash "$ROOT/scripts/deploy/phase15_v3_fast_live_preflight_cloudshell.sh"'
    )
    activation = text.index(
        'bash "$ROOT/scripts/deploy/phase15_v3_fast_live_activate_cloudshell.sh"'
    )

    assert stop_source < stop_receiver < cleanup < stage < preflight < activation

    assert "<<'PY' ||" not in text
    assert "if ! PYTHONPATH=\"$ROOT/src\" python3 -" in text
    assert 'if ! python3 - "$OLD_RUNTIME"' in text

    for forbidden in (
        "post_order(",
        "create_market_order",
        "systemctl enable bp-phase15-fast-live",
        "rm -rf /var/lib/bp/phase15-fast-live",
        "rm -rf /var/lib/bp-canary/fast-live",
    ):
        assert forbidden not in text
