from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "PROJECT_STATE.json"
EVIDENCE = (
    ROOT
    / "docs"
    / "evidence"
    / "phase-15-v3-fresh-watcher-restart-authorization-20260927.json"
)
POST_TERMINAL_EVIDENCE = (
    ROOT
    / "docs"
    / "evidence"
    / "phase-15-v3-post-terminal-reconciliation-watcher-restart-authorization-20260927.json"
)
PORTABILITY_EVIDENCE = (
    ROOT
    / "docs"
    / "evidence"
    / "phase-15-v3-post-terminal-reconciliation-watcher-restart-macos-portability-20260927.json"
)
POST_TERMINAL_PASS_EVIDENCE = (
    ROOT
    / "docs"
    / "evidence"
    / "phase-15-v3-post-terminal-reconciliation-watcher-restart-pass-production-20260927.json"
)
START = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_canary_prepare_watch_start_cloudshell.sh"
)


def test_fresh_watcher_restart_authorization_was_consumed_by_safe_fail() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    watch = gate["persistent_prepare_watch"]
    second = gate["second_live_canary_authorization"]

    assert watch["status"] == "PRODUCTION_ACTIVE_WAITING_FOR_FRESH_CANDIDATE"
    assert watch["start_authorized"] is False
    assert watch["service_active"] is True
    assert watch["last_status"] == "running"
    assert watch["last_status_reason"] == "waiting_for_new_frozen_v3_trade_order"
    assert watch["post_expiry_restart_authorized"] is True
    assert watch["post_expiry_restart_authorization_consumed"] is True
    assert watch["post_expiry_restart_production_performed"] is True
    assert watch["post_expiry_restart_result"] == "PASS"
    assert watch["post_expiry_restart_run_id"] == (
        "phase15-prepare-watch-20260927T122620Z-31ba83ab"
    )
    assert watch["post_expiry_restart_service_active"] is True
    assert watch["post_expiry_restart_no_real_order_submitted"] is True
    assert watch["post_expiry_restart_arm_automated"] is False
    assert watch["post_expiry_restart_submission_automated"] is False
    assert watch["post_expiry_restart_one_shot"] is True
    assert watch["post_expiry_restart_max_wait_seconds"] == 7200
    assert watch["post_expiry_restart_prepare_only"] is True
    assert watch["post_expiry_restart_research_mode"] is True
    assert watch["post_expiry_restart_zero_money"] is True
    assert watch["post_expiry_restart_requires_new_candidate_after_activation"] is True
    assert watch["post_expiry_restart_previous_run_id"] == (
        "phase15-prepare-watch-20260927T094843Z-4b584f95"
    )
    assert watch["post_expiry_restart_previous_run_status"] == "expired"
    assert watch["post_expiry_restart_previous_submission_attempt_consumed"] is False
    assert watch["post_expiry_restart_previous_real_order_submitted"] is False
    assert watch["post_expiry_restart_previous_arm_attempted"] is False
    assert watch["post_expiry_restart_does_not_authorize_telegram_approve"] is True
    assert watch["post_expiry_restart_does_not_authorize_executor_arm_or_invoke"] is True
    assert watch["post_expiry_restart_does_not_authorize_order_submission"] is True
    assert watch["post_expiry_restart_does_not_authorize_live_trading_enablement"] is True
    assert watch["fresh_restart_authorized"] is True
    assert watch["fresh_restart_authorization_consumed"] is True
    assert watch["fresh_restart_authorized_at_main"] == (
        "f3afdf940478d487a46244e79e4263ccb5aa02f7"
    )
    assert watch["fresh_restart_max_wait_seconds"] == 7200
    assert watch["fresh_restart_prepare_only"] is True
    assert watch["fresh_restart_research_mode"] is True
    assert watch["fresh_restart_zero_money"] is True
    assert watch["fresh_restart_requires_new_candidate_after_activation"] is True
    assert watch["fresh_restart_production_performed"] is True
    assert watch["fresh_restart_result"] == (
        "SAFE_FAIL_PENDING_TERMINAL_INTENT_RECONCILIATION"
    )
    assert watch["fresh_restart_start_result"] == "PASS"
    assert watch["fresh_restart_terminal_status"] == "failed"
    assert watch["fresh_restart_terminal_reason"] == "canary_prepare_blocked"
    assert watch["fresh_restart_terminal_last_report_reason"] == (
        "pending_live_intent_requires_reconciliation"
    )
    assert watch["fresh_restart_submission_attempt_consumed"] is False
    assert watch["fresh_restart_no_real_order_submitted"] is True
    assert watch["fresh_restart_may_not_be_reused"] is True
    assert watch["fresh_restart_requires_new_authorization_after_reconciliation"] is True
    assert watch["prepared_intent_reconciled"] is True
    assert watch["prepared_reconciliation_result_status"] == "reconciled"
    assert watch["prepared_reconciliation_id_observed"] == (
        "live-reconciliation-745a1e86a0075b4e98cdfbb30517a154"
    )
    assert watch["blocked_pending_intent_id"] is None
    assert watch["terminal_intent_reconciliation_completed"] is True
    assert watch["fresh_restart_authorization_required_now"] is False
    assert watch["fresh_restart_authorized_now"] is False
    assert watch["post_terminal_reconciliation_restart_authorized"] is True
    assert watch["post_terminal_reconciliation_restart_authorization_consumed"] is True
    assert watch["post_terminal_reconciliation_restart_one_shot"] is True
    assert watch["post_terminal_reconciliation_restart_max_wait_seconds"] == 7200
    assert watch["post_terminal_reconciliation_restart_prepare_only"] is True
    assert watch["post_terminal_reconciliation_restart_research_mode"] is True
    assert watch["post_terminal_reconciliation_restart_zero_money"] is True
    assert (
        watch["post_terminal_reconciliation_restart_requires_new_candidate_after_activation"]
        is True
    )
    assert watch["post_terminal_reconciliation_restart_authorized_at_main"] == (
        "b7517a2279c8168021a72eb68e5884f1ade80c94"
    )
    assert watch["post_terminal_reconciliation_restart_production_performed"] is True
    assert (
        watch[
            "post_terminal_reconciliation_restart_authorization_scope_changed_by_helper_repair"
        ]
        is False
    )
    assert (
        watch["post_terminal_reconciliation_restart_authorization_rebound_for_portability"]
        is True
    )

    assert watch["fresh_restart_forbidden_intent_id"] == (
        "live-intent-4cb75bd0f114e378130b28d7699320e9"
    )
    assert watch["fresh_restart_forbidden_prediction_id"] == (
        "cca4840dcf6034ac624a48ca3e88e91e4f5d3a96b5d334705e4c1694e449c05c"
    )
    assert watch["fresh_restart_forbidden_paper_order_id"] == (
        "18072fef623e29fa095c433cdd0657958848c5e6a34c194ae6527e532c4dc2ab"
    )
    assert watch["latest_prepared_intent_terminal"] is True
    assert watch["latest_prepared_intent_retry_allowed"] is False
    assert watch["latest_prepared_intent_network_attempt_consumed"] is False
    assert watch["latest_prepared_intent_real_order_submitted"] is False

    assert watch["fresh_restart_does_not_authorize_telegram_approve"] is True
    assert watch["fresh_restart_does_not_authorize_executor_arm_or_invoke"] is True
    assert watch["fresh_restart_does_not_authorize_order_submission"] is True
    assert watch["fresh_restart_does_not_authorize_live_trading_enablement"] is True
    assert watch["second_canary_network_attempt_consumed"] is False
    assert second["status"] == "AUTHORIZED_NOT_SUBMITTED"
    assert second["max_network_submission_attempts"] == 1
    assert state["live_trading_enabled"] is False
    assert gate["live_trading_enabled"] is False


def test_fresh_watcher_restart_is_bound_to_unchanged_runtime_artifacts() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    watch = state["phase_15_v3_live_canary"]["persistent_prepare_watch"]

    assert watch["fresh_restart_start_helper_git_blob_sha"] == (
        "0a98b03e35d8019a85063e45d1a979fea96c0532"
    )
    assert watch["fresh_restart_runner_git_blob_sha"] == (
        "efe6dc9c3b37f5788b701107366bb634a4b1f357"
    )
    assert watch["fresh_restart_service_unit_git_blob_sha"] == (
        "5e20c65edd57e398d2106c7f6fe93fbb477b7572"
    )
    assert watch["fresh_restart_canary_git_blob_sha"] == (
        "df5e60b1b2ba632fd77d65103509fac70187be7d"
    )
    assert watch["fresh_restart_live_git_blob_sha"] == (
        "0617ffeda8365cdd6ab2636af00e58ca8954c4db"
    )
    assert watch["fresh_restart_arm_helper_git_blob_sha"] == (
        "692d60cc73438a4703db2d74d56448ce868bd9fe"
    )
    assert watch["fresh_restart_executor_git_blob_sha"] == (
        "0a0cdc42882c6de5d8b09d5f630826cd6fab39a3"
    )
    assert watch["post_terminal_reconciliation_restart_start_helper_git_blob_sha"] == (
        "3d883da14592f230dc0ea7a51b76991ea898bede"
    )
    assert watch["post_terminal_reconciliation_restart_original_start_helper_git_blob_sha"] == (
        "0a98b03e35d8019a85063e45d1a979fea96c0532"
    )
    assert watch["post_terminal_reconciliation_restart_runner_git_blob_sha"] == (
        "efe6dc9c3b37f5788b701107366bb634a4b1f357"
    )
    assert watch["post_terminal_reconciliation_restart_service_unit_git_blob_sha"] == (
        "5e20c65edd57e398d2106c7f6fe93fbb477b7572"
    )
    assert watch["post_terminal_reconciliation_restart_canary_git_blob_sha"] == (
        "df5e60b1b2ba632fd77d65103509fac70187be7d"
    )
    assert watch["post_terminal_reconciliation_restart_live_git_blob_sha"] == (
        "0617ffeda8365cdd6ab2636af00e58ca8954c4db"
    )
    assert watch["post_terminal_reconciliation_restart_arm_helper_git_blob_sha"] == (
        "692d60cc73438a4703db2d74d56448ce868bd9fe"
    )
    assert watch["post_terminal_reconciliation_restart_executor_git_blob_sha"] == (
        "0a0cdc42882c6de5d8b09d5f630826cd6fab39a3"
    )


def test_fresh_watcher_restart_evidence_matches_authorization() -> None:
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    assert evidence["authorized_at_main"] == (
        "f3afdf940478d487a46244e79e4263ccb5aa02f7"
    )
    assert evidence["authorized_restart"]["max_wait_seconds"] == 7200
    assert evidence["authorized_restart"]["prepare_only"] is True
    assert evidence["authorized_restart"]["research_mode"] is True
    assert evidence["authorized_restart"]["zero_money"] is True
    assert evidence["authorized_restart"]["fresh_candidate_required"] is True
    assert evidence["authorization_consumed"] is False
    assert evidence["production_restart_performed"] is False
    assert evidence["previous_terminal_candidate"]["retry_allowed"] is False
    assert evidence["previous_terminal_candidate"]["must_not_be_reused"] is True
    assert evidence["previous_terminal_candidate"]["network_attempt_consumed"] is False
    assert evidence["excluded_actions"]["telegram_approve"] is False
    assert evidence["excluded_actions"]["executor_arm_or_invoke"] is False
    assert evidence["excluded_actions"]["order_submission"] is False
    assert evidence["excluded_actions"]["live_trading_enablement"] is False


def test_post_terminal_reconciliation_restart_evidence_matches_authorization() -> None:
    evidence = json.loads(POST_TERMINAL_EVIDENCE.read_text(encoding="utf-8"))

    assert evidence["authorized_at_main"] == (
        "b7517a2279c8168021a72eb68e5884f1ade80c94"
    )
    assert evidence["authorized_restart"]["max_wait_seconds"] == 7200
    assert evidence["authorized_restart"]["prepare_only"] is True
    assert evidence["authorized_restart"]["research_mode"] is True
    assert evidence["authorized_restart"]["zero_money"] is True
    assert evidence["authorized_restart"]["one_shot"] is True
    assert evidence["authorized_restart"]["fresh_candidate_required"] is True
    assert evidence["authorization_consumed"] is False
    assert evidence["production_restart_performed"] is False
    assert evidence["reconciled_terminal_candidate"]["reconciliation_status"] == "reconciled"
    assert evidence["reconciled_terminal_candidate"]["retry_allowed"] is False
    assert evidence["reconciled_terminal_candidate"]["must_not_be_reused"] is True
    assert evidence["reconciled_terminal_candidate"]["network_attempt_consumed"] is False
    assert evidence["excluded_actions"]["telegram_approve"] is False
    assert evidence["excluded_actions"]["executor_arm_or_invoke"] is False
    assert evidence["excluded_actions"]["order_submission"] is False
    assert evidence["excluded_actions"]["live_trading_enablement"] is False


def test_post_terminal_restart_portability_rebind_preserves_scope() -> None:
    evidence = json.loads(PORTABILITY_EVIDENCE.read_text(encoding="utf-8"))

    assert evidence["source_main"] == "91d1564790378fc3d7fc3efbb1b972cc7a6feee9"
    assert evidence["authorization_scope_changed"] is False
    assert evidence["authorization_consumed"] is False
    assert evidence["production_restart_performed"] is False
    assert evidence["repair"]["previous_git_blob_sha"] == (
        "0a98b03e35d8019a85063e45d1a979fea96c0532"
    )
    assert evidence["repair"]["repaired_git_blob_sha"] == (
        "3d883da14592f230dc0ea7a51b76991ea898bede"
    )
    assert evidence["preserved_authorization"]["max_wait_seconds"] == 7200
    assert evidence["preserved_authorization"]["prepare_only"] is True
    assert evidence["preserved_authorization"]["research_mode"] is True
    assert evidence["preserved_authorization"]["zero_money"] is True
    assert evidence["excluded_actions"]["telegram_approve"] is False
    assert evidence["excluded_actions"]["executor_arm_or_invoke"] is False
    assert evidence["excluded_actions"]["order_submission"] is False
    assert evidence["excluded_actions"]["live_trading_enablement"] is False


def test_post_terminal_restart_production_pass_matches_operator_output() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    watch = state["phase_15_v3_live_canary"]["persistent_prepare_watch"]
    evidence = json.loads(POST_TERMINAL_PASS_EVIDENCE.read_text(encoding="utf-8"))

    assert watch["post_terminal_reconciliation_restart_result"] == "PASS"
    assert watch["post_terminal_reconciliation_restart_source_main"] == (
        "4b584f95256844a1fef42740e5b4008daa1cdd48"
    )
    assert watch["post_terminal_reconciliation_restart_run_id"] == (
        "phase15-prepare-watch-20260927T094843Z-4b584f95"
    )
    assert watch["post_terminal_reconciliation_restart_service_active"] is True
    assert watch["post_terminal_reconciliation_restart_no_real_order_submitted"] is True
    assert watch["post_terminal_reconciliation_restart_arm_automated"] is False
    assert watch["post_terminal_reconciliation_restart_submission_automated"] is False
    assert watch["second_canary_network_attempt_consumed"] is False

    assert evidence["source_main"] == "4b584f95256844a1fef42740e5b4008daa1cdd48"
    assert evidence["run"]["run_id"] == (
        "phase15-prepare-watch-20260927T094843Z-4b584f95"
    )
    assert evidence["run"]["service_active"] is True
    assert evidence["operator_output"]["start_result"] == "PASS"
    assert evidence["operator_output"]["no_real_order_submitted"] is True
    assert evidence["operator_output"]["arm_automated"] is False
    assert evidence["operator_output"]["submission_automated"] is False
    assert evidence["safety"]["telegram_approval_performed"] is False
    assert evidence["safety"]["executor_armed"] is False
    assert evidence["safety"]["executor_invoked"] is False
    assert evidence["safety"]["order_submission_performed"] is False
    assert evidence["safety"]["second_canary_network_attempt_consumed"] is False
    assert evidence["authorization_consumed"] is True
    assert evidence["result"] == "PASS"


def test_start_helper_remains_prepare_only_and_fail_closed() -> None:
    text = START.read_text(encoding="utf-8")
    for marker in (
        "PHASE15_ACCEPT_PERSISTENT_PREPARE_WATCH",
        'watch["start_authorized"] is True',
        'watch["prepare_only"] is True',
        'watch["arm_automated"] is False',
        'watch["submission_automated"] is False',
        '[[ "$(read_env "$path" LIVE_TRADING_ENABLED)" == "false" ]]',
        '[[ "$(read_env "$path" MAX_TRADE_SIZE_USD)" == "0" ]]',
        '[[ "$(read_env "$path" MAX_DAILY_LOSS_USD)" == "0" ]]',
        'payload["kill_switch_engaged"] is True',
        'payload["submission_ready"] is False',
        'payload["live_order_submitted"] is False',
        "hashlib.sha256",
        "base64.b64encode",
    ):
        assert marker in text
    assert "base64 -w0" not in text


def test_second_post_expiry_restart_is_freshly_authorized() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    watch = state["phase_15_v3_live_canary"]["persistent_prepare_watch"]
    auth = watch["second_post_expiry_restart_authorization"]

    assert auth["authorized"] is True
    assert auth["consumed"] is True
    assert auth["one_shot"] is True
    assert auth["max_wait_seconds"] == 7200
    assert auth["prepare_only"] is True
    assert auth["research_mode"] is True
    assert auth["zero_money"] is True
    assert auth["requires_new_candidate_after_activation"] is True
    assert auth["previous_run_id"] == "phase15-prepare-watch-20260927T122620Z-31ba83ab"
    assert auth["previous_run_status"] == "expired"
    assert auth["previous_run_reason"] == "no_eligible_v3_trade_within_wait_window"
    assert auth["previous_arm_attempted"] is False
    assert auth["previous_real_order_submitted"] is False
    assert auth["previous_submission_attempt_consumed"] is False
    assert auth["does_not_authorize_telegram_approve"] is True
    assert auth["does_not_authorize_executor_arm_or_invoke"] is True
    assert auth["does_not_authorize_order_submission"] is True
    assert auth["does_not_authorize_live_trading_enablement"] is True


def test_second_post_expiry_restart_production_pass_matches_operator_output() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    watch = state["phase_15_v3_live_canary"]["persistent_prepare_watch"]
    auth = watch["second_post_expiry_restart_authorization"]
    evidence_path = (
        ROOT
        / "docs"
        / "evidence"
        / "phase-15-v3-second-post-expiry-watcher-restart-pass-production-20260927.json"
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

    assert auth["production_restart_performed"] is True
    assert auth["production_restart_result"] == "PASS"
    assert auth["run_id"] == "phase15-prepare-watch-20260927T154458Z-656caf74"
    assert auth["source_main"] == "656caf74aaf17b52af8b5844fddf7c7a9d4b0d30"
    assert auth["service_active"] is True
    assert auth["no_real_order_submitted"] is True
    assert auth["arm_automated"] is False
    assert auth["submission_automated"] is False

    assert evidence["result"] == "PASS"
    assert evidence["authorization_consumed"] is True
    assert evidence["run"]["service_active"] is True
    assert evidence["operator_output"]["start_result"] == "PASS"
    assert evidence["operator_output"]["no_real_order_submitted"] is True
    assert evidence["operator_output"]["arm_automated"] is False
    assert evidence["operator_output"]["submission_automated"] is False


def test_controlled_canary_fresh_prepare_restart_is_authorized_after_clean_expiry() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    controlled = gate["controlled_auto_approved_canary_authorization"]
    restart = controlled["fresh_prepare_restart_authorization"]

    assert controlled["consumed"] is False
    assert controlled["network_attempt_observed"] is False
    assert controlled["real_order_submission_observed"] is False
    assert controlled["prepare_watcher_result"] == "running_until_candidate"

    assert restart["authorized"] is False
    assert restart["superseded"] is True
    assert restart["superseded_by"] == "until_candidate_prepare_authorization"
    assert restart["consumed"] is False
    assert restart["one_shot"] is True
    assert restart["max_wait_seconds"] == 7200
    assert restart["prepare_only"] is True
    assert restart["research_mode"] is True
    assert restart["zero_money"] is True
    assert restart["requires_new_candidate_after_activation"] is True
    assert restart["previous_run_id"] == "phase15-prepare-watch-20260927T154458Z-656caf74"
    assert restart["previous_run_status"] == "expired"
    assert restart["previous_real_order_submitted"] is False
    assert restart["previous_arm_attempted"] is False
    assert restart["previous_submission_attempt_consumed"] is False
    assert restart["controlled_canary_authorization_remains_unconsumed"] is True
    assert restart["auto_approver_may_handle_fresh_valid_prompt"] is True
    assert (
        restart[
            "does_not_authorize_additional_network_attempts_beyond_existing_second_canary_limit"
        ]
        is True
    )

    assert restart["start_helper_git_blob_sha"] == "3d883da14592f230dc0ea7a51b76991ea898bede"
    assert restart["runner_git_blob_sha"] == "efe6dc9c3b37f5788b701107366bb634a4b1f357"
    assert restart["service_unit_git_blob_sha"] == "5e20c65edd57e398d2106c7f6fe93fbb477b7572"
    assert restart["canary_git_blob_sha"] == "df5e60b1b2ba632fd77d65103509fac70187be7d"
    assert restart["live_git_blob_sha"] == "0617ffeda8365cdd6ab2636af00e58ca8954c4db"
    assert restart["arm_helper_git_blob_sha"] == "692d60cc73438a4703db2d74d56448ce868bd9fe"
    assert restart["executor_git_blob_sha"] == "0a0cdc42882c6de5d8b09d5f630826cd6fab39a3"


def test_controlled_canary_until_candidate_watcher_is_authorized() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    watch = gate["persistent_prepare_watch"]
    controlled = gate["controlled_auto_approved_canary_authorization"]
    auth = controlled["until_candidate_prepare_authorization"]

    assert watch["status"] == "PRODUCTION_ACTIVE_WAITING_FOR_FRESH_CANDIDATE"
    assert watch["start_authorized"] is False
    assert watch["service_active"] is True
    assert watch["max_wait_seconds"] == 0
    assert watch["wait_mode"] == "until_candidate"
    assert watch["runtime_cap_removed"] is True
    assert watch["enabled_across_vm_reboot"] is False

    assert controlled["consumed"] is False
    assert controlled["network_attempt_observed"] is False
    assert controlled["real_order_submission_observed"] is False
    assert controlled["prepare_watcher_mode"] == "until_candidate"
    assert controlled["prepare_watcher_max_wait_seconds"] == 0
    assert controlled["prepare_watcher_expected_expiry_at"] is None

    assert auth["authorized"] is True
    assert auth["consumed"] is True
    assert auth["one_shot"] is True
    assert auth["wait_mode"] == "until_candidate"
    assert auth["max_wait_seconds"] == 0
    assert auth["time_expiry_disabled"] is True
    assert auth["prepare_only"] is True
    assert auth["research_mode"] is True
    assert auth["zero_money"] is True
    assert auth["requires_new_candidate_after_activation"] is True
    assert auth["stops_after_one_prepared_candidate"] is True
    assert auth["stops_on_fail_closed_terminal_condition"] is True
    assert auth["enabled_across_vm_reboot"] is False
    assert auth["controlled_canary_authorization_remains_unconsumed"] is True
    assert auth["auto_approver_may_handle_fresh_valid_prompt"] is True
    assert auth["max_network_submission_attempts"] == 1
    assert auth["additional_network_attempts_authorized"] is False
    assert auth["production_restart_performed"] is True
    assert auth["production_restart_result"] == "PASS"
    assert auth["run_id"] == "phase15-prepare-watch-20260927T183730Z-fc79d368"
    assert auth["service_active"] is True
    assert auth["no_real_order_submitted"] is True
    assert auth["arm_automated"] is False
    assert auth["submission_automated"] is False
    assert auth["submission_attempt_consumed"] is False
    assert auth["helper_output_zero_seconds_means_until_candidate"] is True
    assert "hard runtime cap of 2h5m" not in watch["safety"]
    assert (
        "no time expiry; stops after one prepared candidate or fail-closed terminal condition"
        in watch["safety"]
    )

    assert auth["start_helper_git_blob_sha"] == "318479fc6c4059e078be3d2851f89874bda489f5"
    assert auth["runner_git_blob_sha"] == "ed3ced72c75291a0fd79a15009b0a468561683d9"
    assert auth["service_unit_git_blob_sha"] == "2f162c9c17658d6917544b056176a56cc50bea46"
    assert auth["canary_git_blob_sha"] == "df5e60b1b2ba632fd77d65103509fac70187be7d"
    assert auth["live_git_blob_sha"] == "0617ffeda8365cdd6ab2636af00e58ca8954c4db"
    assert auth["arm_helper_git_blob_sha"] == "692d60cc73438a4703db2d74d56448ce868bd9fe"
    assert auth["executor_git_blob_sha"] == "0a0cdc42882c6de5d8b09d5f630826cd6fab39a3"
