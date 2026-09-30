from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_fast_live_zero_fill_fix_restart_cloudshell.sh"
)


def test_zero_fill_fix_restart_helper_is_shell_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_zero_fill_fix_restart_helper_is_exact_and_fail_closed() -> None:
    text = HELPER.read_text(encoding="utf-8")

    for marker in (
        "I_ACCEPT_DEPLOY_FAST_LIVE_ZERO_FILL_FIX_AND_RESTART_SESSION",
        "phase15-v3-fast-live-auto-continuous-12h-a6525318-20260930",
        "afbf078a1be8bb29bb26ad7b99b2a35f10501473",
        "2026-09-30T11:58:23.648915+00:00",
        "ed1f55975958f23baee65f0c4a334b34009dda78",
        "working_tree_not_clean",
        "checkout_is_not_current_main",
        "zero_fill_fix_blob_mismatch",
        "runtime_already_expired",
        "sudo systemctl stop bp-phase15-fast-live-source.service",
        "zero-fill-fix-restart",
        "sudo systemctl stop bp-phase15-fast-live-receiver.service",
        "/var/lib/bp-canary/fast-live/KILL",
        "PHASE15_ACCEPT_FAST_LIVE_ZERO_ACTIVITY_RESTART",
        "phase15_v3_fast_live_cleanup_expired_cloudshell.sh",
        "phase15_v3_fast_live_build_release.py",
        "phase15_v3_fast_live_stage_cloudshell.sh",
        "phase15_v3_fast_live_preflight_cloudshell.sh",
        "I_ACCEPT_CONTINUOUS_TELEGRAM_APPROVED_LIVE_SESSION",
        'BP_FAST_LIVE_RUNTIME_EXPIRES_AT="$RUNTIME_EXPIRES"',
        "phase15_v3_fast_live_activate_cloudshell.sh",
        "phase15_v3_fast_live_status_cloudshell.sh",
        "RUNTIME_EXPIRY_EXTENDED=false",
        "REAL_ORDER_SUBMITTED_BY_RESTART=false",
    ):
        assert marker in text

    stop_source = text.index(
        "sudo systemctl stop bp-phase15-fast-live-source.service"
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

    assert stop_source < cleanup < stage < preflight < activation

    for forbidden in (
        "rm -rf /var/lib/bp/phase15-fast-live",
        "rm -rf /var/lib/bp-canary/fast-live",
        "BP_FAST_LIVE_RUNTIME_EXPIRES_AT=2026-09-30T12:38:13",
        "systemctl enable bp-phase15-fast-live",
    ):
        assert forbidden not in text
