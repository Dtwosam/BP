from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_fast_live_preflight_cloudshell.sh"
)


def test_fast_live_preflight_is_shell_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(PREFLIGHT)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_fast_live_preflight_is_read_only_and_checks_full_session_readiness() -> None:
    text = PREFLIGHT.read_text(encoding="utf-8")

    for marker in (
        "verify_source_authorization",
        "requires_telegram_approval=True",
        "continuous_session=True",
        '[[ "$HEAD" == "$REMOTE_MAIN" ]]',
        '[[ "$AUTH_MAIN" =~ ^[0-9a-f]{40}$ ]]',
        'merge-base --is-ancestor "$AUTH_MAIN" "$HEAD"',
        "working_tree_not_clean",
        "/opt/bp-fast-live/releases/$AUTH_MAIN",
        "/opt/bp-phase15-telegram-approval/releases/$AUTH_MAIN",
        "/etc/bp/telegram-approval.env",
        "/etc/bp/telegram-approval-handoff.env",
        "/var/lib/bp/phase15-fast-live/telegram-prepare/current-run",
        "/var/lib/bp/phase15-fast-live/RESULT_INTEGRITY_FAULT.json",
        "recorder_prior_live_recovery_pending",
        "\\${receipt##*/}",
        "\\${result##*/}",
        "\\$root/results/\\$base",
        "\\$root/settlements/\\$base",
        "cancellation_pending\\\":true",
        "recovery_result_publish_pending\\\":true",
        "/var/lib/bp-canary/fast-live/KILL",
        '\\\"action\\\":\\\"health\\\"',
        'geo.get("country") != "ZA"',
        'account.get("clean_for_canary") is not True',
        "open_orders != 0",
        'collateral < Decimal("5")',
        "PHASE15_FAST_LIVE_PREFLIGHT=PASS",
        "CONTROL_MAIN=%s",
        "MUTATIONS_PERFORMED=false",
        "PUBSUB_RESOURCES_MUTATED=false",
        "RUNTIME_AUTHORIZATION_CREATED=false",
        "SERVICES_STARTED=false",
        "KILL_SWITCH_REMOVED=false",
        "REAL_ORDER_SUBMITTED=false",
    ):
        assert marker in text

    for forbidden in (
        "gcloud pubsub topics create",
        "gcloud pubsub subscriptions create",
        "add-iam-policy-binding",
        "gcloud compute scp",
        "systemctl start bp-phase15-fast-live",
        "systemctl restart bp-phase15-canary-telegram-approval",
        "rm -f /var/lib/bp-canary/fast-live/KILL",
        "install -o ",
        "os.urandom",
    ):
        assert forbidden not in text
