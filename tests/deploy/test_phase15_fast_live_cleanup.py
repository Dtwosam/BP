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
        "I_ACCEPT_CLEAN_EXPIRED_ZERO_ATTEMPT_FAST_LIVE_SESSION",
        "I_ACCEPT_CLEAN_EXPIRED_CONTINUOUS_LIVE_SESSION",
        "I_ACCEPT_ABORT_ZERO_ACTIVITY_FAST_LIVE_SESSION_AFTER_VALIDATION_DEFECT",
        "I_ACCEPT_DEPLOY_FAST_LIVE_ZERO_FILL_FIX_AND_RESTART_SESSION",
        "I_ACCEPT_ROTATE_ZERO_ATTEMPT_SOURCE_LAG_SESSION",
        "I_ACCEPT_TRANSITION_STOPPED_FAST_LIVE_SESSION_TO_AUTHORIZED_V2",
        'CLEANUP_MODE="expired_zero_attempt"',
        'CLEANUP_MODE="expired"',
        'CLEANUP_MODE="zero_activity_abort"',
        'CLEANUP_MODE="zero_activity_restart"',
        'CLEANUP_MODE="source_lag_restart"',
        'CLEANUP_MODE="operator_transition"',
        "expired_zero_attempt_runtime_not_expired",
        "expired_zero_attempt_authorization_id_mismatch",
        "expired_zero_attempt_release_main_mismatch",
        "expired_zero_attempt_authorization_mode_mismatch",
        "expired_zero_attempt_runtime_expiry_mismatch",
        "runtime_authorization_not_expired",
        "current_session_requires_expired_zero_attempt_mode",
        "zero_activity_abort_runtime_already_expired",
        "phase15-v3-fast-live-auto-continuous-5d305254b06ef0cbce33065e",
        "bbb20f8f5f3b533ecad3c0798c944c61f31bcdfe",
        "expired_runtime_authorization_missing_on_both_hosts",
        'AUTH_SOURCE_HOST="recorder"',
        'AUTH_SOURCE_HOST="executor"',
        "expired_runtime_material_missing_on_both_hosts",
        "recorder_runtime_presence_check_failed",
        "executor_runtime_presence_check_failed",
        "AUTHORIZATION_SOURCE_HOST=%s",
        "manual-telegram-continuous-v1",
        "auto-telegram-continuous-v1",
        "manual-telegram-continuous-v2",
        "auto-telegram-continuous-v2",
        "max_network_submission_attempts_per_intent",
        "zero_activity_abort_recorder_activity_present",
        "zero_activity_abort_executor_activity_present",
        "source_lag_restart_attempt_scan_failed",
        "source_lag_restart_publication_scan_failed",
        "SOURCE_LAG_SESSION_ATTEMPT_MARKER_COUNT",
        "SOURCE_LAG_SESSION_EXECUTION_RESULT_COUNT",
        "SOURCE_LAG_SESSION_PUBLICATION_COUNT",
        "expired_zero_attempt_executor_scan_failed",
        "expired_zero_attempt_publication_scan_failed",
        "expired_zero_attempt_network_activity_present",
        "ZERO_NETWORK_ATTEMPT_VERIFIED=true",
        "SESSION_PUBLICATION_COUNT",
        "SESSION_NETWORK_SUBMISSION_ATTEMPT_COUNT",
        "SESSION_EXECUTION_RESULT_COUNT",
        "SESSION_REAL_ORDER_SUBMITTED=false",
        "CLEANUP_COMPLETED=true",
        "EXECUTOR_GEO_COUNTRY=ZA",
        "EXECUTOR_GEO_BLOCKED=false",
        "EXECUTOR_OPEN_ORDER_COUNT=0",
        "EXECUTOR_ACCOUNT_CLEAN=true",
        "RECORDER_RUNTIME_AUTHORIZATION_PRESENT=false",
        "EXECUTOR_RUNTIME_AUTHORIZATION_PRESENT=false",
        "RECORDER_TRANSPORT_KEY_PRESENT=false",
        "EXECUTOR_TRANSPORT_KEY_PRESENT=false",
        "RECORDER_SOURCE_ACTIVE=false",
        "EXECUTOR_RECEIVER_ACTIVE=false",
        "LIVE_PUBLICATIONS",
        "LIVE_ATTEMPTS",
        "APPROVAL_CLAIMS",
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


def test_fast_live_cleanup_zero_activity_abort_is_exactly_scoped() -> None:
    text = CLEANUP.read_text(encoding="utf-8")

    assert (
        '[[ "$AUTH_ID" == '
        '"phase15-v3-fast-live-auto-continuous-5d305254b06ef0cbce33065e" ]]'
        in text
    )
    assert '[[ "$RELEASE_MAIN" == "bbb20f8f5f3b533ecad3c0798c944c61f31bcdfe" ]]' in text
    assert '[[ "$AUTH_MODE" == "auto-telegram-continuous-v1" ]]' in text
    assert (
        '[[ "$LIVE_PUBLICATIONS" == "0" && "$LIVE_RESULTS" == "0" && '
        '"$LIVE_SETTLEMENTS" == "0" ]]'
        in text
    )
    assert '[[ "$LIVE_ATTEMPTS" == "0" && "$EXEC_RESULTS" == "0" &&' in text
    assert 'ZERO_ACTIVITY_VERIFIED=true' in text


def test_fast_live_cleanup_runtime_rm_commands_are_single_shell_commands() -> None:
    text = CLEANUP.read_text(encoding="utf-8")

    assert (
        'sudo rm -f /etc/bp-fast-live/authorization.json '
        '/etc/bp-fast-live/PROJECT_STATE.json '
        '/etc/bp-fast-live/transport.key '
        '/etc/bp/phase15-fast-live-source.env'
        in text
    )
    assert (
        'sudo rm -f /etc/bp-fast-live/authorization.json '
        '/etc/bp-fast-live/PROJECT_STATE.json '
        '/etc/bp-fast-live/transport.key '
        '/etc/bp-fast-live/receiver.env;'
        in text
    )
    assert (
        'sudo rm -f /etc/bp-fast-live/authorization.json\n'
        '                    /etc/bp-fast-live/PROJECT_STATE.json'
        not in text
    )



def test_fast_live_cleanup_zero_activity_restart_is_exactly_scoped() -> None:
    text = CLEANUP.read_text(encoding="utf-8")

    assert (
        '[[ "$AUTH_ID" == '
        '"phase15-v3-fast-live-auto-continuous-12h-a6525318-20260930" ]]'
        in text
    )
    assert (
        '[[ "$RELEASE_MAIN" == '
        '"afbf078a1be8bb29bb26ad7b99b2a35f10501473" ]]'
        in text
    )
    assert (
        '[[ "$RUNTIME_EXPIRES" == '
        '"2026-09-30T11:58:23.648915+00:00" ]]'
        in text
    )
    assert 'zero_activity_restart_runtime_already_expired' in text
    assert 'zero_activity_restart_authorization_id_mismatch' in text
    assert 'zero_activity_restart_release_main_mismatch' in text
    assert 'zero_activity_restart_runtime_expiry_mismatch' in text
    assert (
        '[[ "$CLEANUP_MODE" == "zero_activity_abort" || '
        '"$CLEANUP_MODE" == "zero_activity_restart" || '
        '"$CLEANUP_MODE" == "source_lag_restart" ]]'
        in text
    )


def test_fast_live_cleanup_operator_transition_is_exactly_scoped() -> None:
    text = CLEANUP.read_text(encoding="utf-8")

    assert (
        '[[ "$AUTH_ID" == '
        '"phase15-v3-fast-live-auto-continuous-12h-21ee9a70-20260930T150000Z" ]]'
        in text
    )
    assert 'git -C "$ROOT" cat-file -e "$RELEASE_MAIN^{commit}"' in text
    assert 'git -C "$ROOT" merge-base --is-ancestor "$RELEASE_MAIN" "$HELPER_HEAD"' in text
    assert '[[ "$AUTH_MODE" == "auto-telegram-continuous-v1" ]]' in text
    assert '"2026-09-30T15:00:00.318438+00:00"' in text
    assert '"2026-10-01T03:00:00.231215+00:00"' in text
    assert "runtime_expiry_not_after_authorization" in text
    assert "runtime_exceeds_source_authorization" in text
    assert 'operator_transition_runtime_already_expired' in text
    assert 'operator_transition_authorization_id_mismatch' in text
    assert 'operator_transition_release_main_unknown' in text
    assert 'operator_transition_release_main_not_in_main_history' in text
    assert 'operator_transition_recorder_release_read_failed' in text
    assert 'operator_transition_executor_release_read_failed' in text
    assert 'operator_transition_recorder_release_path_invalid' in text
    assert 'operator_transition_executor_release_path_invalid' in text
    assert 'operator_transition_recorder_release_invalid' in text
    assert 'operator_transition_executor_release_invalid' in text
    assert 'operator_transition_deployed_release_mismatch' in text
    assert 'operator_transition_deployed_release_unknown' in text
    assert 'operator_transition_deployed_release_not_in_main_history' in text
    assert 'DEPLOYED_RELEASE_MAIN=%s' in text
    assert 'operator_transition_authorization_mode_mismatch' in text
    assert 'operator_transition_runtime_expiry_outside_source_window' in text

def test_fast_live_cleanup_source_lag_restart_is_exactly_scoped() -> None:
    text = CLEANUP.read_text(encoding="utf-8")

    assert "I_ACCEPT_ROTATE_ZERO_ATTEMPT_SOURCE_LAG_SESSION" in text
    assert 'CLEANUP_MODE="source_lag_restart"' in text
    assert (
        '[[ "$AUTH_ID" == '
        '"phase15-v3-fast-live-auto-continuous-v2-12h-2302945a-20261001T124442Z" ]]'
        in text
    )
    assert (
        '[[ "$RELEASE_MAIN" == '
        '"2302945a0fd6fe7f04654a6c7915767bdde5ef7d" ]]'
        in text
    )
    assert '[[ "$AUTH_MODE" == "auto-telegram-continuous-v2" ]]' in text
    assert (
        '[[ "$RUNTIME_EXPIRES" == "2026-10-02T00:44:42+00:00" ]]'
        in text
    )
    assert "source_lag_restart_runtime_already_expired" in text
    assert "source_lag_restart_authorization_id_mismatch" in text
    assert "source_lag_restart_release_main_mismatch" in text
    assert "source_lag_restart_authorization_mode_mismatch" in text
    assert "source_lag_restart_runtime_expiry_mismatch" in text
    assert 'Path("/var/lib/bp-canary/fast-live/attempts")' in text
    assert 'str(payload.get("authorization_id") or "") == authorization_id' in text
    assert "assert not attempts" in text
    assert "assert not results" in text
    assert 'Path("/var/lib/bp/phase15-fast-live/published")' in text
    assert 'payload.get("network_submission_attempt_consumed") is False' in text
    assert 'payload.get("real_order_submitted") is False' in text
    assert 'result.get("network_submission_attempt_consumed") is False' in text
    assert 'recorded.get("event_type") == "closed_before_submission"' in text
    assert 'recorded.get("official_reconciliation_required") is False' in text


def test_fast_live_cleanup_expired_zero_attempt_is_exactly_scoped() -> None:
    text = CLEANUP.read_text(encoding="utf-8")

    assert "I_ACCEPT_CLEAN_EXPIRED_ZERO_ATTEMPT_FAST_LIVE_SESSION" in text
    assert 'CLEANUP_MODE="expired_zero_attempt"' in text
    assert (
        '[[ "$AUTH_ID" == '
        '"phase15-v3-fast-live-auto-continuous-v2-12h-9824a0b1-20261001T201044Z" ]]'
        in text
    )
    assert (
        '[[ "$RELEASE_MAIN" == '
        '"9824a0b16f64b5018739a33634dc5e4dea673be8" ]]'
        in text
    )
    assert '[[ "$AUTH_MODE" == "auto-telegram-continuous-v2" ]]' in text
    assert (
        '[[ "$RUNTIME_EXPIRES" == "2026-10-02T08:10:44+00:00" ]]'
        in text
    )
    assert 'Path("/var/lib/bp-canary/fast-live/attempts")' in text
    assert 'Path("/var/lib/bp/phase15-fast-live/published")' in text
    assert '[[ "$SESSION_ATTEMPTS" == "0" && "$SESSION_EXEC_RESULTS" == "0" ]]' in text
    assert 'result.get("network_submission_attempt_consumed") is not False' in text
    assert 'recorded.get("event_type") != "closed_before_submission"' in text
    assert (
        'recorded.get("official_reconciliation_required") is not False'
        in text
    )
    assert "expired_zero_attempt_unexpected_publication_present" in text
    assert '[[ "$SESSION_PUBLICATIONS" == "0" ]]' in text
    assert "ZERO_NETWORK_ATTEMPT_VERIFIED=true" in text
    assert "SESSION_REAL_ORDER_SUBMITTED=false" in text
    assert "CLEANUP_COMPLETED=true" in text
    assert "current_session_requires_expired_zero_attempt_mode" in text
    assert 'geo.get("blocked") is not False' in text
    assert 'geo.get("country") != "ZA"' in text
    assert "RECORDER_RUNTIME_AUTHORIZATION_PRESENT=false" in text
    assert "EXECUTOR_RUNTIME_AUTHORIZATION_PRESENT=false" in text
    assert "subscription_still_present:$sub" in text
    assert "topic_still_present:$topic" in text
    assert "sudo test ! -e /etc/bp-fast-live/PROJECT_STATE.json" in text
    assert "sudo test ! -e /etc/bp/phase15-fast-live-source.env" in text
    assert "sudo test ! -e /etc/bp-fast-live/receiver.env" in text
