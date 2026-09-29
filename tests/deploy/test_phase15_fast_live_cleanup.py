from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLEANUP = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_fast_live_cleanup_expired_cloudshell.sh"
)


def test_fast_live_expired_cleanup_is_shell_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(CLEANUP)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_fast_live_expired_cleanup_is_fail_closed_and_session_scoped() -> None:
    text = CLEANUP.read_text(encoding="utf-8")

    for marker in (
        "I_ACCEPT_CLEAN_EXPIRED_CONTINUOUS_LIVE_SESSION",
        "runtime_authorization_not_expired",
        "expired_runtime_authorization_missing_on_both_hosts",
        'AUTH_SOURCE_HOST="recorder"',
        'AUTH_SOURCE_HOST="executor"',
        "expired_runtime_material_missing_on_both_hosts",
        "recorder_runtime_presence_check_failed",
        "executor_runtime_presence_check_failed",
        "AUTHORIZATION_SOURCE_HOST=%s",
        "manual-telegram-continuous-v1",
        "max_network_submission_attempts_per_intent",
        '[[ "$HELPER_HEAD" == "$REMOTE_MAIN" ]]',
        "working_tree_not_clean",
        "recorder_session_not_quiescent",
        "recorder_result_integrity_fault_latched",
        "RESULT_INTEGRITY_FAULT.json",
        "executor_session_not_quiescent",
        "/var/lib/bp-canary/fast-live/KILL",
        "/var/lib/bp/phase15-fast-live/telegram-prepare/current-run",
        "recorder_recovery_not_complete",
        "executor_recovery_not_complete",
        "cancellation_pending\\\":true",
        "recovery_result_publish_pending\\\":true",
        "settlement_reconciliation_required",
        "\\${receipt##*/}",
        "\\${result##*/}",
        "\\$root/results/\\$base",
        "\\$root/settlements/\\$base",
        "runtime_authorization_hash_mismatch",
        "transport_key_hash_mismatch",
        'account.get("clean_for_canary") is not True',
        "open_order_count",
        'ORDER_TOPIC="bp-phase15-fast-live-orders-$AUTH_SUFFIX"',
        'RESULT_TOPIC="bp-phase15-fast-live-results-$AUTH_SUFFIX"',
        'ORDER_SUB="bp-phase15-fast-live-orders-$AUTH_SUFFIX-jhb"',
        'RESULT_SUB="bp-phase15-fast-live-results-$AUTH_SUFFIX-us"',
        'gcloud pubsub subscriptions delete "$sub"',
        'gcloud pubsub topics delete "$topic"',
        "/etc/bp-fast-live/authorization.json",
        "/etc/bp-fast-live/PROJECT_STATE.json",
        "/etc/bp-fast-live/transport.key",
        "/etc/bp/phase15-fast-live-source.env",
        "/etc/bp-fast-live/receiver.env",
        "HISTORICAL_STATE_PRESERVED=true",
        "KILL_SWITCH_ENGAGED=true",
        "REAL_ORDER_SUBMITTED=false",
    ):
        assert marker in text

    pubsub_delete = text.index('delete_subscription_if_present "$ORDER_SUB"')
    recorder_runtime_remove = text.index(
        "sudo rm -f /etc/bp-fast-live/authorization.json"
    )
    assert pubsub_delete < recorder_runtime_remove
    assert text.index("expired_runtime_authorization_missing_on_both_hosts") < pubsub_delete
    assert text.index("expired_runtime_material_missing_on_both_hosts") < pubsub_delete

    for forbidden in (
        "rm -rf /var/lib/bp/phase15-fast-live",
        "rm -rf /var/lib/bp-canary/fast-live",
        "rm -f /var/lib/bp-canary/fast-live/KILL",
        "systemctl start bp-phase15-fast-live",
        "systemctl restart bp-phase15-fast-live",
        "systemctl enable bp-phase15-fast-live",
    ):
        assert forbidden not in text


def test_fast_live_expired_cleanup_preserves_remote_shell_expansion() -> None:
    text = CLEANUP.read_text(encoding="utf-8")
    assert "base=\\${receipt##*/};" in text
    assert "base=\\${result##*/};" in text
    assert "awk '{print \\$1}'" in text
