from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOST_EVIDENCE = ROOT / "docs/evidence/phase-15-v3-canary-host-geoblock-20260923.json"
LIVE_CANARY_EVIDENCE = ROOT / "docs/evidence/phase-15-v3-first-live-canary-submission-20260924.json"
DB_RECONCILIATION_PASS_EVIDENCE = (
    ROOT
    / "docs/evidence/phase-15-v3-first-canary-db-reconciliation-pass-production-20260926.json"
)
WATCHER_EXPIRED_EVIDENCE = (
    ROOT
    / "docs/evidence/phase-15-v3-post-reconciliation-watcher-expired-readonly-20260926.json"
)
WATCHER_RESTART_AUTH_EVIDENCE = (
    ROOT
    / "docs/evidence/phase-15-v3-post-reconciliation-watcher-restart-authorization-20260926.json"
)
WATCHER_RESTART_PASS_EVIDENCE = (
    ROOT
    / "docs/evidence/phase-15-v3-post-reconciliation-watcher-restart-pass-production-20260926.json"
)


def test_phase15_second_live_canary_is_telegram_authorized_but_not_submitted() -> None:
    state = json.loads((ROOT / "PROJECT_STATE.json").read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    master = state["phase_14_checkpoint"]["master_live_gate"]

    assert state["source_of_truth_version"] == "0.14.180"
    assert state["current_phase"] == 15
    assert state["status"] == "PHASE_15_SECOND_LIVE_CANARY_TELEGRAM_AUTHORIZED_NOT_SUBMITTED"
    assert all(value == "pass" for value in master.values())
    assert state["phase_14_checkpoint"]["overall_live_gate"] == "pass"
    assert state["phase_14_checkpoint"]["phase15_permitted"] is True

    assert gate["status"] == "SECOND_LIVE_CANARY_TELEGRAM_AUTHORIZED_NOT_SUBMITTED"
    assert gate["phase15_canary_authorized"] is True
    assert gate["source_prediction_version"] == "v3-frozen-paper-v1"
    assert gate["source_execution_version"] == "paper-execution-v3-frozen-v1"
    assert gate["strategy_target_notional_usd"] == 5
    assert gate["user_authorized_max_trade_size_usd"] == 10
    assert gate["max_trade_size_usd"] == 10
    assert gate["max_total_exposure_usd"] == 10
    assert gate["max_daily_loss_usd"] == 10
    assert gate["max_consecutive_losses"] == 1
    assert gate["max_accepted_orders"] == 1
    assert gate["max_submission_attempts"] == 1
    assert gate["live_min_liquidity_usd"] == 5
    assert gate["official_account_preflight_required"] is True
    assert gate["official_open_order_count_required"] == 0
    assert gate["minimum_collateral_balance_usd"] == 5
    assert gate["activation_binds_exact_intent"] is True
    assert gate["activation_binds_request_sha256"] is True
    assert gate["activation_binds_executor_sha256"] is True
    assert gate["executor_direct_post_order_no_sdk_recovery_retry"] is True
    assert gate["ambiguous_result_retry_allowed"] is False
    assert (
        gate["unsubmitted_intent_reconciliation_helper"]
        == "scripts/deploy/phase15_v3_canary_reconcile_unsubmitted_cloudshell.sh"
    )
    assert gate["pre_submission_closed_event_type"] == "closed_before_submission"
    assert gate["pre_submission_closure_consumes_submission_attempt"] is False
    assert gate["pre_submission_reconciliation_requires_kill_switch_engaged"] is True
    assert gate["pre_submission_reconciliation_requires_activation_invalid"] is True
    assert gate["pre_submission_reconciliation_requires_submission_not_ready"] is True
    assert (
        gate["pre_submission_reconciliation_requires_live_order_submitted_false"] is True
    )
    assert (
        gate["pre_submission_reconciliation_requires_zero_official_open_orders"] is True
    )
    assert gate["order_ttl_seconds"] == 2
    assert gate["historical_trade_reuse_allowed"] is False
    assert gate["wallet_material_allowed_on_us_host"] is False
    assert gate["automated_real_money_submission"] is True
    assert gate["manual_real_money_submission_required"] is False
    assert gate["live_trading_enabled"] is False
    assert gate["canary_order_submitted"] is True
    assert gate["reconciliation_required_before_second_order"] is True
    assert gate["second_order_authorized"] is True
    assert gate["telegram_one_tap_submission_authorized"] is True
    assert gate["telegram_persistent_execution_transport_authorized"] is True
    assert gate["telegram_pubsub_transport_authorized"] is True
    second = gate["second_live_canary_authorization"]
    assert second["status"] == "AUTHORIZED_NOT_SUBMITTED"
    assert second["strategy_target_notional_usd"] == 5
    assert second["hard_max_trade_size_usd"] == 10
    assert second["max_network_submission_attempts"] == 1
    assert second["global_attempt_marker_is_one_shot"] is True
    assert second["global_attempt_marker_path"] == (
        "/var/lib/bp-canary/telegram-live-handoff/second-canary.attempt.json"
    )
    assert second["requires_fresh_telegram_approval"] is True
    assert second["requires_execution_package_verifier_pass"] is True
    assert second["requires_privileged_handoff_contract_verifier_pass"] is True
    assert second["requires_official_reconciliation_before_any_third_order"] is True
    assert second["retry_on_missing_malformed_or_ambiguous_result"] is False
    assert second["stake_growth_authorized"] is False
    assert second["v3_strategy_mutation_authorized"] is False
    assert second["v4_mutation_authorized"] is False
    assert second["broad_autonomous_live_rollout_authorized"] is False
    first = gate["first_live_canary"]
    assert first["status"] == "RECONCILED_ZERO_FILL"
    assert first["intent_id"] == "live-intent-6cdfcfd28d0eb52f1ee0762bfd351409"
    assert first["external_order_id"] == (
        "0x7c85e5e8753a875dfd9fec8ffd45726164863648127351b21c2a8ba1819a28de"
    )
    assert first["target_notional_usd"] == 5
    assert first["executor_result"] == "accepted"
    assert first["cancellation_result"] == "cancelled"
    assert first["network_submission_attempt_consumed"] is True
    assert first["retry_authorized"] is False
    assert first["second_order_authorized"] is False
    assert first["official_order_fill_reconciliation_required"] is True
    assert first["official_order_fill_reconciliation_status"] == "PASS_ZERO_FILL"
    assert first["confirmed_filled_shares"] == 0
    assert first["confirmed_filled_notional_usd"] == 0
    assert first["confirmed_fill_fraction_of_requested"] == 0
    assert first["exposure_usd"] == 0
    assert first["realized_trade_pnl_usd"] == 0
    assert first["fill_quality_observed"] is False
    assert first["slippage_observed"] is False
    assert first["submission_path_validated"] is True
    assert first["cancellation_path_validated"] is True
    assert first["official_fill_probe_result"] == "PASS"
    assert first["official_fill_state"] == "zero_fill_observed"
    assert first["official_reconciliation_complete"] is True
    assert first["external_official_reconciliation_complete"] is True
    assert first["live_risk_ledger_reconciliation_complete"] is True
    assert first["live_risk_ledger_reconciliation_status"] == (
        "PASS_POST_SUBMISSION_ZERO_FILL"
    )
    assert first["live_risk_blocked_by_missing_post_submission_reconciliation"] is False
    assert first["db_reconciliation_required"] is False
    assert first["db_reconciliation_id"] == (
        "live-reconciliation-81489372163985723f74f2003c7ef1d7"
    )
    assert first["db_reconciliation_unresolved_count"] == 0
    assert first["db_reconciliation_critical_count"] == 0
    repair = gate["post_submission_db_reconciliation_repair"]
    assert repair["status"] == "PRODUCTION_PASS"
    assert repair["authorized"] is False
    assert repair["authorization_consumed"] is True
    assert repair["helper"] == (
        "scripts/deploy/phase15_v3_first_canary_db_reconciliation_cloudshell.sh"
    )
    assert repair["candidate_helper_git_blob_sha"] == (
        "b21456d222f8256b381d43d70c1373bbbee6d78d"
    )
    assert repair["helper_git_blob_sha"] == (
        "b21456d222f8256b381d43d70c1373bbbee6d78d"
    )
    assert repair["target_intent_id"] == first["intent_id"]
    assert repair["target_external_order_id"] == first["external_order_id"]
    assert repair["candidate_count_observed_since_corrected_watcher_start"] == 3
    assert repair["candidate_risk_reason_observed"] == "reconciliation_blocked"
    assert repair["all_observed_candidates_otherwise_trade_and_executable"] is True
    assert repair["strategy_changed"] is False
    assert repair["live_risk_thresholds_changed"] is False
    assert repair["sizing_changed"] is False
    assert repair["executor_changed"] is False
    assert repair["watcher_binding_paths_changed"] is False
    assert repair["does_not_authorize_telegram_approve"] is True
    assert repair["does_not_authorize_executor_arm_or_invoke"] is True
    assert repair["does_not_authorize_order_submission"] is True
    assert repair["production_db_mutation_performed"] is True
    assert repair["production_authorization_required"] is True
    assert repair["authorization_date"] == "2026-09-26"
    assert repair["authorization_recorded_from_main"] == (
        "53345938ee4fe08ba0a1eae3ad7c364e25e999e7"
    )
    assert repair["fresh_official_zero_fill_reverification_required"] is True
    assert repair["authorization_evidence"] == (
        "docs/evidence/phase-15-v3-first-canary-db-reconciliation-authorization-20260926.json"
    )
    assert repair["production_run_source_main"] == (
        "38b6081621eab59aecafcdb0628e1d68e9497827"
    )
    assert repair["production_result_status"] == "reconciled"
    assert repair["reconciliation_id"] == first["db_reconciliation_id"]
    assert repair["unresolved_count"] == 0
    assert repair["critical_count"] == 0
    assert repair["official_zero_fill_reverified"] is True
    assert repair["telegram_approval_performed"] is False
    assert repair["executor_armed"] is False
    assert repair["executor_invoked"] is False
    assert repair["order_submission_performed"] is False
    assert repair["network_submission_attempt_consumed_by_repair"] is False
    assert repair["production_evidence"] == (
        "docs/evidence/phase-15-v3-first-canary-db-reconciliation-pass-production-20260926.json"
    )

    db_reconciliation_evidence = json.loads(
        DB_RECONCILIATION_PASS_EVIDENCE.read_text(encoding="utf-8")
    )
    assert db_reconciliation_evidence["helper_result"] == "PASS"
    assert db_reconciliation_evidence["reconciliation_result"]["status"] == "reconciled"
    assert db_reconciliation_evidence["reconciliation_result"]["unresolved_count"] == 0
    assert db_reconciliation_evidence["reconciliation_result"]["critical_count"] == 0
    assert db_reconciliation_evidence["official_fill_reverification"]["fill_state"] == (
        "zero_fill_observed"
    )
    assert db_reconciliation_evidence["safety"]["telegram_approval_performed"] is False
    assert db_reconciliation_evidence["safety"]["executor_armed"] is False
    assert db_reconciliation_evidence["safety"]["executor_invoked"] is False
    assert db_reconciliation_evidence["safety"]["order_submission_performed"] is False

    live_evidence = json.loads(LIVE_CANARY_EVIDENCE.read_text(encoding="utf-8"))
    assert live_evidence["status"] == "SUBMITTED_AND_RECORDED_RECONCILIATION_PENDING"
    assert live_evidence["submission_result"]["accepted"] is True
    assert live_evidence["submission_result"]["cancellation"]["cancelled"] is True
    assert live_evidence["record_result"]["event_type"] == "accepted"
    assert live_evidence["safety"]["exactly_one_network_submission_attempt_consumed"] is True
    assert live_evidence["safety"]["retry_authorized"] is False
    assert live_evidence["reconciliation"]["required"] is True
    assert live_evidence["reconciliation"]["status"] == "PENDING"

    evidence = json.loads(HOST_EVIDENCE.read_text(encoding="utf-8"))
    assert evidence["helper_result"] == "PASS"
    assert evidence["direct_geoblock"]["blocked"] is False
    assert evidence["direct_geoblock"]["country"] == "ZA"
    assert evidence["safety"]["trading_software_installed"] is False
    assert evidence["safety"]["wallet_or_signing_material_present"] is False

def test_phase15_telegram_transport_runtime_repair_resume_passed() -> None:
    state = json.loads((ROOT / "PROJECT_STATE.json").read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    stage = gate["telegram_transport_stage"]
    activation = gate["telegram_transport_activation_authorization"]
    second = gate["second_live_canary_authorization"]
    watch = gate["persistent_prepare_watch"]

    assert stage["status"] == "PRODUCTION_ACTIVE_WAITING_FOR_FRESH_TELEGRAM_APPROVAL"
    assert stage["stage_id"] == "phase15-telegram-stage-c2dc6e8eab1a0b00200b7486"
    assert stage["release_head"] == "51fdb27374c70ff04e07cf3c697c0fb2e4f81a44"
    assert stage["release_sha256"] == (
        "c31544dae4b99393377a1c6597f3d5875b2ab9df4427396b7fcfed6281b33a22"
    )
    assert stage["stage_install_status"] == "PASS"
    assert stage["stage_status_status"] == "PASS"
    assert stage["pubsub_readiness_status"] == "PASS"
    assert stage["stage_ready_for_later_configuration_review"] is True
    assert stage["publisher_service_active"] is True
    assert stage["executor_services_active"] is True
    assert stage["services_started"] is True
    assert stage["services_enabled"] is True
    assert stage["environment_files_present"] is True
    assert stage["key_files_present"] is True
    assert stage["current_runtime_usable"] is True
    assert stage["runtime_health_status"] == "PASS"
    assert stage["second_canary_network_attempt_consumed"] is False
    assert stage["real_order_submitted"] is False
    assert stage["waiting_for_fresh_telegram_approval"] is True
    assert stage["latest_activation_attempt_status"] == "PASS_WAITING_FOR_FRESH_TELEGRAM_APPROVAL"
    assert stage["latest_activation_attempt_real_order_submitted"] is False

    assert activation["status"] == "ACTIVATED_WAITING_FOR_FRESH_TELEGRAM_APPROVAL"
    assert activation["waiting_for_fresh_telegram_approval"] is True
    assert activation["telegram_approval_performed"] is False
    assert activation["executor_armed"] is False
    assert activation["order_submission_performed"] is False
    assert activation["real_order_submitted"] is False
    assert activation["fresh_private_telegram_approval_still_required_for_second_canary"] is True
    assert activation["global_second_canary_attempt_marker_still_one_shot"] is True
    assert activation["official_reconciliation_required_before_any_third_order"] is True
    assert activation["broad_autonomous_live_rollout_authorized"] is False
    assert gate["pending_unsubmitted_intent"] is None
    assert watch["status"] == "PRODUCTION_ACTIVE_WAITING_FOR_FRESH_CANDIDATE"
    assert watch["authorized"] is True
    assert watch["start_authorized"] is False
    assert watch["runtime_reauthorization_required"] is False
    assert watch["runtime_reauthorization_authorized"] is True
    assert watch["runtime_reauthorization_authorized_at_main"] == (
        "51e4cf1fa5063ff0f4a263dd5ec1646dc18684cf"
    )
    assert watch["runtime_reauthorization_consumed"] is True
    assert watch["post_reconciliation_restart_authorization_required"] is False
    assert watch["post_reconciliation_restart_authorized"] is False
    assert watch["post_reconciliation_restart_authorization_consumed"] is True
    assert watch["post_reconciliation_restart_authorized_at_main"] == (
        "6488426580c61cbe76fe31c2ad7b482b519e63fc"
    )
    assert watch["post_reconciliation_restart_does_not_authorize_telegram_approve"] is True
    assert (
        watch["post_reconciliation_restart_does_not_authorize_executor_arm_or_invoke"]
        is True
    )
    assert watch["post_reconciliation_restart_does_not_authorize_order_submission"] is True
    assert watch["post_reconciliation_restart_source_main"] == (
        "271b35fffdb3ccff1490e213672dfa98b7583d75"
    )
    assert watch["post_reconciliation_restart_production_performed"] is True
    assert watch["post_reconciliation_restart_result"] == "PASS"
    assert watch["post_reconciliation_restart_run_id"] == (
        "phase15-prepare-watch-20260926T195409Z-271b35ff"
    )
    assert watch["post_reconciliation_restart_start_helper_git_blob_sha"] == (
        "0a98b03e35d8019a85063e45d1a979fea96c0532"
    )
    assert watch["post_reconciliation_restart_runner_git_blob_sha"] == (
        "efe6dc9c3b37f5788b701107366bb634a4b1f357"
    )
    assert watch["post_reconciliation_restart_service_unit_git_blob_sha"] == (
        "5e20c65edd57e398d2106c7f6fe93fbb477b7572"
    )
    assert watch["post_reconciliation_restart_canary_git_blob_sha"] == (
        "df5e60b1b2ba632fd77d65103509fac70187be7d"
    )
    assert watch["runtime_reauthorization_does_not_authorize_telegram_approve"] is True
    assert (
        watch["runtime_reauthorization_does_not_authorize_executor_arm_or_invoke"]
        is True
    )
    assert watch["runtime_reauthorization_does_not_authorize_order_submission"] is True
    assert watch["telegram_second_canary_compatible"] is True
    assert watch["prepare_only"] is True
    assert watch["arm_automated"] is False
    assert watch["submission_automated"] is False
    assert watch["service_active"] is True
    assert watch["start_result"] == "PASS"
    assert watch["run_id"] == "phase15-prepare-watch-20260927T094843Z-4b584f95"
    assert watch["remote_run_dir"] == (
        "/var/lib/bp/phase15-canary-prepare-watch/runs/"
        "phase15-prepare-watch-20260927T094843Z-4b584f95"
    )
    assert watch["helper_head"] == "4b584f95256844a1fef42740e5b4008daa1cdd48"
    assert watch["last_status"] == "failed"
    assert watch["last_status_reason"] == "canary_prepare_blocked"
    assert watch["current_run_fresh_candidate_required"] is True
    assert watch["current_run_historical_prepared_intents_forbidden"] is True
    assert watch["current_run_second_canary_network_attempt_consumed"] is False
    assert watch["second_canary_network_attempt_consumed"] is False
    assert watch["second_canary_authorization_preserved"] is True
    assert watch["no_real_order_submitted"] is True
    assert watch["telegram_approval_performed"] is False
    assert watch["executor_armed"] is False
    assert watch["executor_invoked"] is False
    assert watch["order_submission_performed"] is False
    assert watch["fresh_restart_authorization_consumed"] is True
    assert watch["fresh_restart_production_performed"] is True
    assert watch["fresh_restart_result"] == (
        "SAFE_FAIL_PENDING_TERMINAL_INTENT_RECONCILIATION"
    )
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
    assert watch["fresh_restart_authorization_required_now"] is False
    assert watch["fresh_restart_authorized_now"] is False
    assert watch["post_terminal_reconciliation_restart_authorized"] is True
    assert watch["post_terminal_reconciliation_restart_authorization_consumed"] is True
    assert watch["post_terminal_reconciliation_restart_one_shot"] is True
    assert watch["post_terminal_reconciliation_restart_max_wait_seconds"] == 7200
    assert watch["post_terminal_reconciliation_restart_prepare_only"] is True
    assert watch["post_terminal_reconciliation_restart_research_mode"] is True
    assert watch["post_terminal_reconciliation_restart_zero_money"] is True
    assert watch["post_terminal_reconciliation_restart_authorized_at_main"] == (
        "b7517a2279c8168021a72eb68e5884f1ade80c94"
    )
    assert watch["post_terminal_reconciliation_restart_production_performed"] is True
    assert watch["post_terminal_reconciliation_restart_result"] == "PASS"
    assert watch["post_terminal_reconciliation_restart_source_main"] == (
        "4b584f95256844a1fef42740e5b4008daa1cdd48"
    )
    assert watch["post_terminal_reconciliation_restart_run_id"] == (
        "phase15-prepare-watch-20260927T094843Z-4b584f95"
    )
    assert watch["post_terminal_reconciliation_restart_service_active"] is True
    assert watch["post_terminal_reconciliation_restart_no_real_order_submitted"] is True
    assert (
        watch["post_terminal_reconciliation_restart_second_canary_network_attempt_consumed"]
        is False
    )
    assert watch["candidate_start_helper_git_blob_sha"] == (
        "0a98b03e35d8019a85063e45d1a979fea96c0532"
    )
    assert watch["candidate_runner_git_blob_sha"] == (
        "efe6dc9c3b37f5788b701107366bb634a4b1f357"
    )
    assert watch["candidate_service_unit_git_blob_sha"] == (
        "5e20c65edd57e398d2106c7f6fe93fbb477b7572"
    )
    assert watch["candidate_canary_git_blob_sha"] == (
        "df5e60b1b2ba632fd77d65103509fac70187be7d"
    )
    assert watch["candidate_authorized_lifetime_submission_attempt_limit"] == 2
    assert watch["candidate_authorized_lifetime_accepted_order_limit"] == 2
    assert watch["legacy_default_lifetime_submission_attempt_limit"] == 1
    assert watch["legacy_default_lifetime_accepted_order_limit"] == 1
    assert watch["execution_one_shot_still_enforced_by_global_marker"] is True
    assert watch["global_attempt_marker_path"] == second["global_attempt_marker_path"]
    assert watch["current_run_stop_evidence"] == (
        "docs/evidence/phase-15-v3-second-telegram-prepare-watch-ledger-stop-production-20260926.json"
    )
    assert watch["runtime_reauthorization_evidence"] == (
        "docs/evidence/phase-15-v3-second-telegram-prepare-watch-runtime-reauthorization-20260926.json"
    )
    assert watch["current_run_evidence"] == (
        "docs/evidence/phase-15-v3-post-reconciliation-watcher-restart-pass-production-20260926.json"
    )
    assert watch["previous_run_id"] == "phase15-prepare-watch-20260926T155010Z-f3de8c00"
    assert watch["previous_last_status"] == "expired"
    assert watch["previous_last_status_reason"] == "no_eligible_v3_trade_within_wait_window"
    watcher_evidence = json.loads(WATCHER_EXPIRED_EVIDENCE.read_text(encoding="utf-8"))
    assert watcher_evidence["run"]["status"] == "expired"
    assert watcher_evidence["run"]["reason"] == "no_eligible_v3_trade_within_wait_window"
    assert watcher_evidence["run"]["arm_attempted"] is False
    assert watcher_evidence["run"]["real_order_submitted"] is False
    assert watcher_evidence["run"]["submission_attempt_consumed"] is False
    assert watcher_evidence["post_reconciliation_context"]["blocker_cleared"] is True
    assert (
        watcher_evidence["authorization_boundary"]["fresh_restart_authorization_required"]
        is True
    )
    assert watcher_evidence["authorization_boundary"]["fresh_restart_authorized"] is False

    restart_auth_evidence = json.loads(
        WATCHER_RESTART_AUTH_EVIDENCE.read_text(encoding="utf-8")
    )
    assert restart_auth_evidence["authorized_at_main"] == (
        "6488426580c61cbe76fe31c2ad7b482b519e63fc"
    )
    assert restart_auth_evidence["authorized_restart"]["max_wait_seconds"] == 7200
    assert restart_auth_evidence["authorized_restart"]["prepare_only"] is True
    assert restart_auth_evidence["authorized_restart"]["research_mode"] is True
    assert restart_auth_evidence["authorized_restart"]["zero_money"] is True
    assert restart_auth_evidence["authorization_consumed"] is False
    assert restart_auth_evidence["production_restart_performed"] is False
    assert restart_auth_evidence["excluded_actions"]["telegram_approve"] is False
    assert restart_auth_evidence["excluded_actions"]["executor_arm_or_invoke"] is False
    assert restart_auth_evidence["excluded_actions"]["order_submission"] is False

    restart_pass_evidence = json.loads(
        WATCHER_RESTART_PASS_EVIDENCE.read_text(encoding="utf-8")
    )
    assert restart_pass_evidence["source_main"] == (
        "271b35fffdb3ccff1490e213672dfa98b7583d75"
    )
    assert restart_pass_evidence["result"] == "PASS"
    assert restart_pass_evidence["run"]["service_active"] is True
    assert restart_pass_evidence["run"]["max_wait_seconds"] == 7200
    assert restart_pass_evidence["safety"]["prepare_only"] is True
    assert restart_pass_evidence["safety"]["no_real_order_submitted"] is True
    assert restart_pass_evidence["safety"]["arm_automated"] is False
    assert restart_pass_evidence["safety"]["submission_automated"] is False
    assert restart_pass_evidence["safety"]["telegram_approval_performed"] is False
    assert restart_pass_evidence["safety"]["executor_armed"] is False
    assert restart_pass_evidence["safety"]["order_submission_performed"] is False

    assert state["live_trading_enabled"] is False
    assert gate["live_trading_enabled"] is False


def test_phase15_telegram_approval_listener_runtime_repair_passed() -> None:
    state = json.loads((ROOT / "PROJECT_STATE.json").read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    listener = gate["telegram_approval_listener_install_authorization"]
    activation = gate["telegram_transport_activation_authorization"]

    assert listener["status"] == "RUNTIME_REPAIR_PASS"
    assert listener["target_host"] == "bp-recorder"
    assert listener["deployed_head"] == "72aea2868cae1991af5bc66fab75e2ee518e0db7"
    assert listener["previous_deployed_head"] == "13b3f358543ef7a26c31bea65ec57b68f9ebf45f"
    assert listener["runtime_health_status"] == "PASS"
    assert listener["service_active"] is True
    assert listener["service_enabled"] is True
    assert listener["service_healthy"] is True
    assert listener["listener_binding_current"] is True
    assert listener["release_import_usable_by_service_user"] is True
    assert listener["service_pid_stable"] is True
    assert listener["existing_telegram_env_reused"] is True
    assert listener["core_service_pids_preserved"] is True
    assert listener["journal_error_line_count_last_15m"] == 0
    assert listener["handoff_configured"] is False
    assert listener["handoff_env_present"] is False
    assert listener["runtime_repair_required"] is False
    assert listener["runtime_repair_authorized"] is False
    assert listener["runtime_repair_authorization_consumed"] is True
    assert listener["previous_runtime_failure_reason"] == (
        "telegram_approval_module_import_failed"
    )
    assert listener["previous_observed_restart_count"] == 1749
    assert listener["no_real_order_submitted"] is True
    assert activation["status"] == "ACTIVATED_WAITING_FOR_FRESH_TELEGRAM_APPROVAL"
    assert activation["fresh_explicit_authorization_required_for_current_stage"] is False
    assert activation["does_not_submit_real_order"] is True
    assert state["live_trading_enabled"] is False
    assert gate["live_trading_enabled"] is False
    assert listener["runtime_repair_evidence"] == (
        "docs/evidence/phase-15-v3-telegram-approval-listener-runtime-repair-production-20260926.json"
    )

def test_phase15_iap_ssh_api_enablement_is_narrowly_authorized() -> None:
    state = json.loads((ROOT / "PROJECT_STATE.json").read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    iap = gate["iap_ssh_access_authorization"]

    assert iap["status"] == "AUTHORIZED_NOT_ENABLED"
    assert iap["project_id"] == "project-4397f2c0-7098-4c1c-abb"
    assert iap["service"] == "iap.googleapis.com"
    assert iap["does_not_authorize_firewall_change"] is True
    assert iap["does_not_authorize_vm_mutation"] is True
    assert iap["does_not_authorize_transport_activation"] is True
    assert iap["does_not_authorize_order_submission"] is True


def test_phase15_telegram_transport_runtime_repair_completed() -> None:
    state = json.loads((ROOT / "PROJECT_STATE.json").read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    repair = gate["telegram_transport_runtime_repair"]
    stage = gate["telegram_transport_stage"]
    partial = repair["partial_repair"]

    assert repair["status"] == "PASS"
    assert repair["authorized"] is True
    assert repair["authorization_consumed"] is True
    assert repair["resume_authorized"] is False
    assert repair["broken_stage_id"] == "phase15-telegram-stage-05b83214b159772872bc7347"
    assert repair["helper"] == (
        "scripts/deploy/phase15_v3_telegram_transport_runtime_repair_cloudshell.sh"
    )
    assert repair["helper_git_blob_sha"] == "a124942660cbd37f1ae94d3875fe1de2e0671ab9"
    assert repair["resume_helper"] == (
        "scripts/deploy/phase15_v3_telegram_transport_runtime_repair_resume_cloudshell.sh"
    )
    assert repair["resume_helper_git_blob_sha"] == (
        "4cf575663761c88c75b6c1cd4c291db27f7653c9"
    )
    assert repair["resume_stage_id"] == "phase15-telegram-stage-c2dc6e8eab1a0b00200b7486"
    assert repair["resume_release_head"] == (
        "51fdb27374c70ff04e07cf3c697c0fb2e4f81a44"
    )
    assert repair["resume_release_sha256"] == (
        "c31544dae4b99393377a1c6597f3d5875b2ab9df4427396b7fcfed6281b33a22"
    )
    assert repair["enable_pubsub_api_authorized"] is True
    assert repair["transport_reactivation_authorized"] is True
    assert repair["does_not_authorize_telegram_approve"] is True
    assert repair["does_not_authorize_executor_arm_or_invoke"] is True
    assert repair["does_not_authorize_order_submission"] is True
    assert repair["second_canary_network_attempt_consumed"] is False
    assert repair["failed_approved_intent_retry_allowed"] is False
    assert repair["failed_approved_intent_must_not_be_replayed"] is True
    assert repair["production_result"] == "PASS"
    assert repair["resume_authorization_consumed"] is True
    assert repair["transport_reactivated"] is True
    assert repair["repair_resume_result"] == "PASS"
    assert repair["completed_at_main"] == "da85d3f9c2714d1bf2a9fdeabe888608697bf402"
    assert repair["activation_helper_git_blob_sha"] == "1b1be9a0abb8747710f72f7c14e3792130f287f8"
    assert repair["pubsub_readiness_status"] == "PASS"
    assert repair["pubsub_readiness_blockers"] == []
    assert repair["post_resume_executor_safe_idle"] is True
    assert repair["post_resume_services_active"] is True
    assert repair["post_resume_real_order_submitted"] is False
    assert repair["post_resume_second_canary_network_attempt_consumed"] is False
    assert repair["pass_evidence"] == (
        "docs/evidence/phase-15-v3-telegram-transport-runtime-repair-resume-pass-production-20260926.json"
    )
    assert repair["first_run_failure_reason"] == "pubsub_readiness_not_pass"
    assert repair["first_run_failure_was_before_transport_activation"] is True

    assert partial["status"] == "SUPERSEDED_BY_REPAIR_RESUME_PASS"
    assert partial["broken_stage_rollback_status"] == "PASS"
    assert partial["pubsub_api_enabled"] is True
    assert partial["corrected_stage_install_status"] == "PASS"
    assert partial["corrected_stage_id"] == repair["resume_stage_id"]
    assert partial["corrected_release_head"] == repair["resume_release_head"]
    assert partial["services_started"] is False
    assert partial["services_enabled"] is False
    assert partial["readiness_blockers"] == ["telegram_transport_activation_not_authorized"]
    assert partial["real_order_submitted"] is False
    assert partial["second_canary_network_attempt_consumed"] is False

    assert stage["runtime_health_status"] == "PASS"
    assert stage["current_runtime_usable"] is True
    assert stage["second_canary_network_attempt_consumed"] is False

