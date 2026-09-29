from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACTIVATE = ROOT / "scripts" / "deploy" / "phase15_v3_fast_live_activate_cloudshell.sh"
SOURCE = ROOT / "scripts" / "run_phase15_v3_fast_live_source.py"
RECEIVER = ROOT / "scripts" / "run_phase15_v3_fast_live_receiver.py"


def test_fast_live_activation_helper_is_shell_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(ACTIVATE)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_fast_live_activation_requires_continuous_manual_session_authorization() -> None:
    text = ACTIVATE.read_text(encoding="utf-8")
    for marker in (
        "I_ACCEPT_CONTINUOUS_TELEGRAM_APPROVED_LIVE_SESSION",
        "verify_source_authorization",
        "verify_runtime_authorization",
        "manual-telegram-continuous-v1",
        "max_network_submission_attempts_per_intent",
        "BP_FAST_LIVE_TELEGRAM_APPROVAL_REQUIRED=yes",
        "BP_FAST_LIVE_CONTINUOUS_SESSION=yes",
        '[[ "$HEAD" == "$REMOTE_MAIN" ]]',
        "working_tree_not_clean",
    ):
        assert marker in text
    assert "PROJECT_STATE.json" in text
    assert "jq" not in text
    assert "update_ref" not in text
    assert "git commit" not in text


def test_fast_live_activation_uses_fresh_per_authorization_transport() -> None:
    text = ACTIVATE.read_text(encoding="utf-8")
    for marker in (
        'AUTH_SUFFIX="$(python3 - "$AUTH_ID"',
        'ORDER_TOPIC="bp-phase15-fast-live-orders-$AUTH_SUFFIX"',
        'ORDER_SUB="bp-phase15-fast-live-orders-$AUTH_SUFFIX-jhb"',
        'RESULT_TOPIC="bp-phase15-fast-live-results-$AUTH_SUFFIX"',
        'RESULT_SUB="bp-phase15-fast-live-results-$AUTH_SUFFIX-us"',
        'TRANSPORT_KEY="$TMP_DIR/transport.key"',
        "os.urandom(32)",
        "transport_key_hash_mismatch",
        "/etc/bp-fast-live/transport.key",
        'roles/pubsub.publisher',
        'roles/pubsub.subscriber',
    ):
        assert marker in text
    assert text.count("gcloud pubsub topics add-iam-policy-binding") == 2
    assert text.count("gcloud pubsub subscriptions add-iam-policy-binding") == 2
    assert "/etc/bp-telegram-transport/transport.key" not in text


def test_fast_live_activation_starts_receiver_armed_then_releases_and_starts_source() -> None:
    text = ACTIVATE.read_text(encoding="utf-8")
    telegram_start = text.index(
        "sudo systemctl restart bp-phase15-canary-telegram-approval.service"
    )
    receiver_start = text.index(
        "sudo systemctl start bp-phase15-fast-live-receiver.service"
    )
    kill_release = text.index(
        "sudo rm -f /var/lib/bp-canary/fast-live/KILL"
    )
    source_start = text.index(
        "sudo systemctl start bp-phase15-fast-live-source.service"
    )
    assert telegram_start < receiver_start < kill_release < source_start

    for marker in (
        "safe_stop()",
        "fast-live-activation-safe-stop",
        "sudo systemctl stop bp-phase15-fast-live-receiver.service",
        "sudo systemctl stop bp-phase15-fast-live-source.service",
        "sudo test ! -e /var/lib/bp-canary/fast-live/attempt.json",
        "sudo test ! -e /var/lib/bp-canary/fast-live/result.json",
        "clean_for_canary",
        'geo.get("country") != "ZA"',
        "open_orders != 0",
        'collateral < Decimal("5")',
        "/opt/bp-phase15-telegram-approval/releases/$HEAD",
        "/etc/bp/telegram-approval.env",
        "/etc/bp/telegram-approval-handoff.env",
        "telegram_approval_listener_start_failed",
        "TELEGRAM_APPROVAL_ACTIVE=true",
        "TELEGRAM_APPROVAL_RELEASE_MAIN=%s",
    ):
        assert marker in text

    assert "systemctl enable bp-phase15-fast-live" not in text
    assert "systemctl enable --now bp-phase15-fast-live" not in text


def test_fast_live_services_run_until_session_expiry_or_operator_stop() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    receiver = RECEIVER.read_text(encoding="utf-8")

    assert "runtime_expires_at" in source
    assert "fast_live_authorization_expired" in source
    assert "if observed >= runtime_expires_at:" in source

    assert "runtime_expires_at" in receiver
    assert "terminal_event = threading.Event()" in receiver
    assert 'result.get("network_submission_attempt_consumed") is True' in receiver
    assert "not continuous_session" in receiver
    assert "terminal_event.set()" in receiver
    assert "if _utc_now() >= runtime_expires_at:" in receiver
    assert "future.cancel()" in receiver
    assert "result_reconciliation_deadline" in source
    assert "fast_live_result_reconciliation_grace" in source
    assert "fast_live_result_reconciliation_timeout" in source

    assert "if not continuous_session:" in source
    assert 'status in {"skipped", "blocked"}' in source
    assert "waiting_for_result = False" in source
