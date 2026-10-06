from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
V4_SPEC = (
    "docs/superpowers/specs/"
    "2026-09-20-phase-14-v4-regime-aware-challenger.md"
)
V3_HOLDOUT_EVIDENCE = (
    "docs/evidence/"
    "phase-14-v3-gate-b-successor-final-holdout-20260920.json"
)


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v4_source_truth_is_separate_and_prospective() -> None:
    state = json.loads(_text("PROJECT_STATE.json"))
    assert state["source_of_truth_version"] == "0.14.187"

    v4 = state["phase_14_v4_regime_aware"]
    assert v4["feature_version"] == "core-v4-regime-aware"
    assert v4["dataset_version"] == "supervised-core-v4-regime-aware-v1"
    assert v4["label_version"] == "official-outcome-v1"
    assert v4["horizon_seconds"] == 300
    assert v4["offsets_seconds"] == [60, 120, 180, 240]
    assert v4["regime_lookbacks_seconds"] == [300, 900, 3600]
    assert v4["regime_names"] == ["bull", "bear", "sideways_mixed", "unknown"]
    assert v4["polymarket_predictor_keys_allowed"] is False
    assert v4["v3_final_holdout_tuning_allowed"] is False
    assert v4["prospective_gate_b_required"] is True
    assert v4["successor_scope"] == "COMPREHENSIVE_V3_WEAKNESS_REMEDIATION"
    assert v4["regime_awareness_is_only_one_objective"] is True
    assert v4["weakness_remediation_objectives"] == [
        "regime_dependence",
        "trade_side_asymmetry",
        "selected_model_simplicity_and_feature_underuse",
        "calibration_robustness",
        "timing_dependence",
        "trade_quality_vs_coverage",
        "loss_drawdown_robustness",
        "execution_availability",
    ]
    assert v4["future_gate_b_required_model_families"] == [
        "simple_baseline",
        "multivariate_btc_native",
        "nonlinear_btc_native",
    ]
    assert v4["future_gate_b_required_offsets_seconds"] == [60, 120, 180, 240]
    assert v4["future_gate_b_must_report_coverage_frontier"] is True
    assert v4["future_gate_b_must_report_drawdown_and_losing_streak"] is True
    assert v4["future_gate_b_must_separate_execution_from_forecast_quality"] is True
    assert v4["v3_holdout_may_only_motivate_hypotheses"] is True
    assert v4["v3_holdout_numeric_tuning_allowed"] is False
    assert v4["current_collector_change_required"] is False
    assert v4["production_materialization_authorized"] is True
    assert v4["production_collection_authorized"] is True
    assert v4["prospective_collection_epoch_start"] == "2026-09-20T12:40:53Z"
    assert v4["collector_preserves_deployed_checkout"] is True
    assert v4["collector_restarts_recorder"] is False
    assert v4["production_materialization_performed"] is True
    assert v4["production_collector_enabled"] is True
    assert v4["production_collector_active"] is True
    assert v4["production_rollout_passed"] is True
    assert v4["initial_coverage_market_count"] == 9
    assert v4["initial_coverage_row_count"] == 36
    assert v4["initial_bull_market_count"] == 2
    assert v4["initial_bear_market_count"] == 0
    assert v4["initial_sideways_mixed_market_count"] == 7
    assert v4["initial_unknown_market_count"] == 0
    assert v4["initial_future_cutoff_violation_count"] == 0
    assert v4["initial_polymarket_predictor_key_count"] == 0
    assert v4["initial_regime_invariant_violation_count"] == 0
    assert v4["training_performed"] is True
    assert v4["final_holdout_access_performed"] is True
    assert v4["paper_activation_performed"] is False
    assert v4["automatic_promotion"] is False
    assert v4["live_trading_enabled"] is False
    assert v4["max_trade_size_usd"] == 0
    assert v4["max_daily_loss_usd"] == 0

    evaluator = v4["final_holdout_evaluator"]
    assert evaluator["status"] == "FINAL_HOLDOUT_REVIEWED_PAPER_SHADOW_STARTED_RUNTIME_COMPATIBLE"
    assert evaluator["guarded_runner"] == (
        "scripts/deploy/phase14_v4_gate_b_final_holdout_cloudshell.sh"
    )
    assert evaluator["repository_main"] == (
        "d73c980df4807d95425f975af0716b671053868c"
    )
    assert evaluator["post_merge_ci_run"] == 37444469680
    assert evaluator["post_merge_ci_passed"] is True
    assert evaluator["preflight_only_supported"] is True
    assert evaluator["preflight_env"] == (
        "PHASE14_V4_GATE_B_FINAL_HOLDOUT_PREFLIGHT_ONLY=true"
    )
    assert evaluator["preflight_contacts_production"] is False
    assert evaluator["preflight_accesses_database"] is False
    assert evaluator["preflight_accesses_holdout"] is False
    assert evaluator["frozen_plan_sha256"] == (
        "9c017b1d968925a8cddab18324628227ed8b8b381e43c653f72c0f26366ee557"
    )
    assert evaluator["frozen_selection_sha256"] == (
        "895cb70ae0cdbc22f4e3585c77db3ad20f8186d1ee1992a58025d89bb1e2bb1a"
    )
    assert evaluator["frozen_model_artifact_sha256"] == (
        "6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf"
    )
    assert evaluator["final_holdout_market_count"] == 288
    assert evaluator["holdout_labels_read"] is True
    assert evaluator["holdout_evaluated"] is True
    assert evaluator["one_shot_authorization_required"] is True
    assert evaluator["one_shot_authorization_granted"] is True
    assert evaluator["one_shot_authorization_consumed"] is True
    assert evaluator["rerun_allowed"] is False
    assert evaluator["result_metrics_reviewed"] is True
    assert evaluator["holdout_evidence_sha256"] == (
        "b34f2a2263eb5376687caff42a8249359a0b18abb020cd2a4bd8b5d6c92e849f"
    )
    assert evaluator["forecast_accuracy"] == 0.90625
    assert evaluator["trade_count"] == 84
    assert evaluator["realized_pnl_after_assumed_costs"] == 11.675645999999999
    assert evaluator["profit_factor"] == 2.3143859832402827
    assert evaluator["bear_after_cost_pnl"] == -0.5209760000000004
    assert evaluator["uniform_regime_robustness_established"] is False
    assert evaluator["aggregate_holdout_supports_paper_observation"] is True
    assert evaluator["paper_activation_recommended_after_runtime_compatibility"] is True
    assert evaluator["required_paper_sklearn_version"] == "1.9.1"
    assert evaluator["observed_production_sklearn_version"] == "1.9.0"
    assert evaluator["paper_runtime_compatible"] is False
    assert evaluator["holdout_driven_threshold_change_allowed"] is False
    assert evaluator["database_writes_performed"] is False
    assert evaluator["sklearn_version_mismatch_observed"] is True
    assert evaluator["artifact_sklearn_version"] == "1.9.1"
    assert evaluator["evaluation_runtime_sklearn_version"] == "1.9.0"
    assert evaluator["durable_attempt_marker_required_before_label_read"] is True
    assert evaluator["model_refit_allowed"] is False
    assert evaluator["threshold_tuning_allowed"] is False
    assert evaluator["policy_reselection_allowed"] is False
    assert evaluator["automatic_promotion"] is False
    assert evaluator["paper_activation_authorized"] is False
    assert evaluator["paper_activation_performed"] is False
    assert evaluator["live_trading_enabled"] is False
    assert evaluator["real_money_usd"] == 0

    paper_runtime = v4["paper_runtime"]
    assert paper_runtime["status"] == (
        "FOURTH_AUTHORIZED_RUNTIME_VALIDATION_PASS_SHADOW_STARTED_24H_OBSERVATION_PENDING_COMPLETION"
    )
    assert paper_runtime["repository_main"] == (
        "f5c76576619c35e65fc8a317d47c8a31dd263950"
    )
    assert paper_runtime["post_merge_ci_run"] == 37472314160
    assert paper_runtime["post_merge_ci_passed"] is True
    assert paper_runtime["runtime_requirements"] == (
        "deploy/phase14-v4-paper-runtime-requirements.txt"
    )
    assert paper_runtime["uses_shared_production_venv"] is False
    assert paper_runtime["required_sklearn_version"] == "1.9.1"
    assert paper_runtime["required_xgboost_version"] == "3.4.1"
    assert paper_runtime["required_joblib_version"] == "1.5.3"
    assert paper_runtime["exact_version_validation_before_model_load"] is True
    assert paper_runtime["atomic_venv_staging"] is False
    assert paper_runtime["in_place_venv_with_cleanup_on_failure"] is True
    assert paper_runtime["runtime_ready_marker_required"] is True
    assert paper_runtime["exact_metadata_and_module_version_validation"] is True
    assert paper_runtime["python_no_user_site"] is True
    assert paper_runtime["project_install_constrained_to_runtime_pins"] is True
    assert paper_runtime["production_runtime_validation_attempted"] is True
    assert paper_runtime["production_runtime_validation_passed"] is True
    assert paper_runtime["failed_attempt_authorized_main"] == (
        "8a472b752625477cf26969e776823a620a39c519"
    )
    assert paper_runtime["failed_attempt_authorization_consumed"] is True
    assert paper_runtime["failed_attempt_reached_production"] is True
    assert paper_runtime["failed_attempt_reached_runtime_staging"] is True
    assert paper_runtime["failed_attempt_shadow_started"] is False
    assert paper_runtime["failed_attempt_runner_status"] == (
        "FAIL:remote_stage_or_start_failed"
    )
    assert paper_runtime["launcher_updates_checkout_during_execution"] is False
    assert paper_runtime["requires_preupdated_local_main"] is True
    assert paper_runtime["stale_local_main_fails_before_gcloud"] is True
    assert paper_runtime["second_failed_attempt_authorized_main"] == (
        "c1d9c9a34f826f075b8fff292d31d576676707c8"
    )
    assert paper_runtime["second_failed_attempt_authorization_consumed"] is True
    assert paper_runtime["second_failed_attempt_reached_production"] is True
    assert paper_runtime["second_failed_attempt_reached_runtime_staging"] is True
    assert paper_runtime["second_failed_attempt_ml_pins_installed"] is True
    assert paper_runtime["second_failed_attempt_sklearn_version"] == "1.9.1"
    assert paper_runtime["second_failed_attempt_xgboost_version"] == "3.4.1"
    assert paper_runtime["second_failed_attempt_joblib_version"] == "1.5.3"
    assert paper_runtime["second_failed_attempt_shadow_started"] is False
    assert paper_runtime["second_failed_attempt_inner_status"] == (
        "FAIL:paper_runtime_pip_check_failed"
    )
    assert paper_runtime["second_failed_attempt_outer_status"] == (
        "FAIL:remote_stage_or_start_failed"
    )
    assert paper_runtime["xgboost_metadata_distribution_name"] == "xgboost-cpu"
    assert paper_runtime["named_version_validation_failure"] == (
        "paper_runtime_version_validation_failed"
    )
    assert paper_runtime["third_failed_attempt_authorized_main"] == (
        "62dbf0d653d7434b6d0bc610009228efff29cb29"
    )
    assert paper_runtime["third_failed_attempt_authorization_consumed"] is True
    assert paper_runtime["third_failed_attempt_reached_production"] is True
    assert paper_runtime["third_failed_attempt_reached_runtime_staging"] is True
    assert paper_runtime["third_failed_attempt_ml_pins_installed"] is True
    assert paper_runtime["third_failed_attempt_project_installed"] is True
    assert paper_runtime["third_failed_attempt_sklearn_version"] == "1.9.1"
    assert paper_runtime["third_failed_attempt_xgboost_distribution"] == "xgboost-cpu"
    assert paper_runtime["third_failed_attempt_xgboost_version"] == "3.4.1"
    assert paper_runtime["third_failed_attempt_joblib_version"] == "1.5.3"
    assert paper_runtime["third_failed_attempt_model_validation_reached"] is False
    assert paper_runtime["third_failed_attempt_shadow_started"] is False
    assert paper_runtime["third_failed_attempt_outer_status"] == (
        "FAIL:remote_stage_or_start_failed"
    )
    assert len(paper_runtime["authorized_attempts"]) == 4
    assert paper_runtime["authorized_attempts"][1]["installed_runtime_versions"] == {
        "scikit-learn": "1.9.1",
        "xgboost": "3.4.1",
        "joblib": "1.5.3",
    }
    assert paper_runtime["authorized_attempts"][2]["installed_runtime_versions"] == {
        "scikit-learn": "1.9.1",
        "xgboost-cpu": "3.4.1",
        "joblib": "1.5.3",
    }
    assert paper_runtime["authorized_attempts"][2]["model_validation_reached"] is False
    fourth = paper_runtime["authorized_attempts"][3]
    assert fourth["authorized_main"] == "f5c76576619c35e65fc8a317d47c8a31dd263950"
    assert fourth["authorization_consumed"] is True
    assert fourth["runtime_validation_passed"] is True
    assert fourth["model_validation_reached"] is True
    assert fourth["shadow_started"] is True
    assert fourth["result"] == "START_PASS_COMPLETION_PENDING"
    assert fourth["run_seconds"] == 86400
    assert fourth["installed_runtime_versions"] == {
        "scikit-learn": "1.9.1",
        "xgboost-cpu": "3.4.1",
        "joblib": "1.5.3",
    }
    assert paper_runtime["paper_shadow_start_evidence"] == (
        "docs/evidence/phase-14-v4-fresh-book-shadow-start-20261006.json"
    )
    assert paper_runtime["paper_shadow_completion_pending"] is True
    assert paper_runtime["paper_shadow_full_run_completed"] is False
    assert paper_runtime["preflight_only_supported"] is True
    assert paper_runtime["preflight_contacts_production"] is False
    assert paper_runtime["preflight_mutates_production"] is False
    assert paper_runtime["production_runtime_validation_performed"] is True
    assert paper_runtime["paper_shadow_started"] is True
    assert paper_runtime["paper_activation_authorized"] is False
    assert paper_runtime["paper_activation_performed"] is False
    assert paper_runtime["live_trading_enabled"] is False
    assert paper_runtime["real_money_usd"] == 0


def test_consumed_v3_holdout_is_durable_motivation_not_v4_tuning_data() -> None:
    evidence = json.loads(_text(V3_HOLDOUT_EVIDENCE))
    assert evidence["status"] == "V3_GATE_B_SUCCESSOR_FINAL_HOLDOUT_EVALUATED"
    assert evidence["final_holdout"]["market_count"] == 144
    assert evidence["final_holdout"]["trade_count"] == 20
    assert evidence["final_holdout"]["realized_pnl_after_assumed_costs"] == 1.654224
    assert evidence["trade_ledger_summary"]["by_side"]["up"]["wins"] == 7
    assert evidence["trade_ledger_summary"]["by_side"]["down"]["wins"] == 2
    assert evidence["safety"]["model_refit_performed"] is False
    assert evidence["safety"]["automatic_promotion"] is False
    assert evidence["safety"]["activation_performed"] is False
    assert evidence["interpretation"]["final_holdout_consumed"] is True
    assert evidence["interpretation"]["reusable_for_future_tuning"] is False
    assert evidence["interpretation"]["v4_motivation_only"] is True


def test_v4_design_freezes_regime_definition_and_safety_boundary() -> None:
    spec = _text(V4_SPEC).lower()
    for required in (
        "core-v4-regime-aware",
        "supervised-core-v4-regime-aware-v1",
        "return_5m",
        "return_15m",
        "return_60m",
        "bull",
        "bear",
        "sideways_mixed",
        "unknown",
        "majority sign",
        "v3 final holdout is permanently consumed",
        "must not be used",
        "wholly future cohort",
        "active in research mode",
        "model fitting",
        "paper activation",
        "live trading",
        "v3 weakness-remediation objectives",
        "trade-side asymmetry",
        "selected-model simplicity / feature underuse",
        "calibration robustness",
        "timing dependence",
        "trade-quality versus coverage",
        "loss/drawdown robustness",
        "execution availability",
        "simple baseline",
        "multivariate btc-native models",
        "nonlinear challenger",
    ):
        assert required in spec


def test_v4_collection_remains_active_during_frozen_v3_paper_activation() -> None:
    start = _text("START-HERE.md")
    build = _text("docs/BUILD-ORDER.md")
    master = _text("docs/MASTER-SOURCE-OF-TRUTH.md")
    decisions = _text("docs/DECISION-LOG.md")
    changelog = _text("docs/CHANGELOG.md")

    assert "core-v4-regime-aware" in master
    assert "V4 regime-aware" in start
    assert "V4 regime-aware feature collector" in build
    assert "V4 regime-aware collection continues in parallel" in master

    assert "## D-049 —" in decisions
    assert "## D-050 —" in decisions
    assert "## D-051 —" in decisions
    assert "## D-052 —" in decisions
    assert "## D-053 —" in decisions
    assert "## D-054 —" in decisions
    assert "## D-056 —" in decisions
    assert "## 0.14.174 — 22 September 2026" in changelog
    assert "## 0.14.145 — 20 September 2026" in changelog
    assert "automatic promotion" in master.lower()
    assert "live trading" in master.lower()


def test_v4_gate_b_v1_is_frozen_future_only_and_still_label_free() -> None:
    state = json.loads(_text("PROJECT_STATE.json"))
    spec = _text(
        "docs/superpowers/specs/"
        "2026-09-22-phase-14-v4-gate-b-preregistration.md"
    ).lower()
    evidence = json.loads(
        _text("docs/evidence/phase-14-v4-gate-b-preregistration-20260922.json")
    )

    gate = state["phase_14_v4_regime_aware"]["gate_b_preregistration_v1"]
    assert gate["status"] == "FROZEN_FUTURE_EPOCH_RUNTIME_IMPLEMENTED_NOT_EXECUTED"
    assert gate["research_plan_version"] == "v4-gate-b-preregister-v1"
    assert gate["epoch_start"] == "2026-09-23T00:00:00Z"
    assert gate["epoch_end"] == "2026-09-30T00:00:00Z"
    assert gate["pre_epoch_v4_rows_reusable_for_selection"] is False
    assert gate["ordinary_fold_count"] == 7
    assert gate["final_holdout_hours"] == 24
    assert gate["forecast_candidates"] == [
        "training_prior",
        "single_feature_btc_logistic",
        "short_context_v4_logistic",
        "full_v4_logistic",
        "full_v4_xgboost",
    ]
    assert gate["side_specific_policy_allowed"] is False
    assert gate["regime_specific_policy_allowed"] is False
    assert gate["readiness_run_performed"] is False
    assert gate["plan_run_performed"] is False
    assert gate["labels_read"] is False
    assert gate["training_performed"] is False
    assert gate["policy_selected"] is False
    assert gate["final_holdout_access_performed"] is False
    assert gate["automatic_promotion"] is False
    assert gate["live_trading_enabled"] is False
    assert gate["max_trade_size_usd"] == 0
    assert gate["max_daily_loss_usd"] == 0
    assert gate["phase15_permitted"] is False

    assert evidence["status"] == "V4_GATE_B_PREREGISTRATION_FROZEN_RESEARCH_ONLY"
    assert evidence["epoch"]["pre_epoch_v4_rows_reusable_for_selection"] is False
    assert evidence["anti_overfit"]["v4_labels_or_outcomes_used_to_set_contract"] is False
    assert evidence["anti_overfit"]["v4_pnl_used_to_set_contract"] is False
    assert evidence["safety"]["training_performed"] is False
    assert evidence["safety"]["final_holdout_access_performed"] is False
    assert evidence["runtime"]["labeled_prepare_command_present"] is False
    assert evidence["runtime"]["holdout_command_present"] is False

    for required in (
        "2026-09-23t00:00:00z",
        "2026-09-30t00:00:00z",
        "pre-epoch v4 rows",
        "structurally ineligible",
        "side-specific",
        "regime-specific",
        "outcome-blind readiness",
        "feature-only",
        "no-clobber",
        "no v4 `prepare`",
    ):
        assert required in spec


def test_v4_gate_b_v2_is_outcome_blind_and_preserves_the_final_holdout() -> None:
    state = json.loads(_text("PROJECT_STATE.json"))
    spec = _text(
        "docs/superpowers/specs/"
        "2026-09-30-phase-14-v4-gate-b-preregistration-v2.md"
    ).lower()
    evidence = json.loads(
        _text(
            "docs/evidence/"
            "phase-14-v4-gate-b-preregistration-v2-20260930.json"
        )
    )

    v4 = state["phase_14_v4_regime_aware"]
    gate = v4["gate_b_preregistration_v2"]
    failure = v4["gate_b_v1_plan_feasibility"]

    assert gate["status"] == "FROZEN_V2_OUTCOME_BLIND_PLAN_NOT_EXECUTED"
    assert gate["research_plan_version"] == "v4-gate-b-preregister-v2"
    assert gate["epoch_start"] == "2026-09-24T00:00:00Z"
    assert gate["epoch_end"] == "2026-09-30T00:00:00Z"
    assert gate["ordinary_fold_count"] == 5
    assert gate["required_non_negative_validation_folds"] == 5
    assert gate["final_holdout_hours"] == 24
    assert gate["readiness_run_performed"] is False
    assert gate["plan_run_performed"] is False
    assert gate["labels_read"] is False
    assert gate["training_performed"] is False
    assert gate["policy_selected"] is False
    assert gate["final_holdout_access_performed"] is False

    assert failure["plan_file_created"] is False
    assert failure["labels_read"] is False
    assert failure["outcomes_read"] is False
    assert failure["fold_0_test_market_count"] == 78
    assert failure["fold_1_validation_after_embargo_market_count"] == 77
    assert failure["final_holdout_market_count"] == 288

    assert evidence["integrity"]["labels_read"] is False
    assert evidence["integrity"]["outcomes_read"] is False
    assert evidence["integrity"]["training_performed"] is False
    assert evidence["integrity"]["final_holdout_evaluated"] is False
    assert evidence["v2_decision"]["final_holdout_changed"] is False
    assert evidence["v2_decision"]["required_non_negative_validation_folds"] == 5

    for required in (
        "v4-gate-b-preregister-v2",
        "five",
        "final holdout",
        "60, 120, 180, and 240",
        "5 of 5",
        "outcome-blind",
        "does **not** authorize labeled preparation",
    ):
        assert required in spec


def test_v4_gate_b_v2_plan_freeze_remains_durable_historical_evidence() -> None:
    state = json.loads(_text("PROJECT_STATE.json"))
    evidence = json.loads(
        _text(
            "docs/evidence/"
            "phase-14-v4-gate-b-v2-plan-freeze-20260930.json"
        )
    )

    v4 = state["phase_14_v4_regime_aware"]
    plan = v4["gate_b_plan"]

    assert plan["source_main"] == "cc8386671fd3c299b3c6c406e5ebcdec42e187a4"
    assert plan["research_plan_version"] == "v4-gate-b-preregister-v2"
    assert plan["market_count"] == 1662
    assert plan["ordinary_fold_count"] == 5
    assert plan["final_holdout_market_count"] == 288
    assert plan["readiness_input_sha256"] == (
        "308f6bd4f97205eff6f40e8521806753315d2c2ee459985cac4110cf9c95c852"
    )
    assert plan["config_sha256"] == (
        "c2dc791e127f78cb6baa060314f7c29ee48192711d2f6c92fa5f1869d654e954"
    )
    assert plan["feature_manifest_sha256"] == (
        "388bbb8bdba9c2c9d38ede8017f0b8c6721df6ad50274a40efb35ec42adbf81f"
    )
    assert plan["plan_sha256"] == (
        "9c017b1d968925a8cddab18324628227ed8b8b381e43c653f72c0f26366ee557"
    )
    assert plan["plan_file_sha256"] == (
        "564e0c299b360062c5d1e37ceb10050e5b600f4fc29f35451aef8c08a5884dad"
    )
    assert plan["labels_read"] is False
    assert plan["outcomes_read"] is False
    assert plan["training_performed"] is False
    assert plan["policy_selected"] is False
    assert plan["final_holdout_evaluated"] is False

    assert evidence["plan_sha256"] == plan["plan_sha256"]
    assert evidence["final_holdout_market_count"] == 288
    assert evidence["integrity"]["labels_read"] is False
    assert evidence["integrity"]["outcomes_read"] is False
    assert evidence["integrity"]["training_performed"] is False
    assert evidence["integrity"]["policy_selected"] is False
    assert evidence["integrity"]["final_holdout_evaluated"] is False


def test_v4_gate_b_v2_ordinary_selection_is_frozen_and_holdout_untouched() -> None:
    state = json.loads(_text("PROJECT_STATE.json"))
    evidence = json.loads(
        _text(
            "docs/evidence/"
            "phase-14-v4-gate-b-v2-ordinary-selection-20260930.json"
        )
    )

    v4 = state["phase_14_v4_regime_aware"]
    selection = v4["gate_b_ordinary_selection"]

    assert v4["status"] == (
        "V4_ZERO_MONEY_FRESH_BOOK_SHADOW_STARTED_24H_OBSERVATION_PENDING_COMPLETION"
    )
    assert selection["plan_sha256"] == (
        "9c017b1d968925a8cddab18324628227ed8b8b381e43c653f72c0f26366ee557"
    )
    assert selection["selection_sha256"] == (
        "895cb70ae0cdbc22f4e3585c77db3ad20f8186d1ee1992a58025d89bb1e2bb1a"
    )
    assert selection["model_artifact_sha256"] == (
        "6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf"
    )
    assert selection["selected_model"] == "full_v4_xgboost"
    assert selection["selected_offset_seconds"] == 240
    assert selection["selected_calibration"] == "identity"
    assert selection["selected_edge_policy"] == "trade_threshold"
    assert selection["selected_min_edge"] == 0.05

    ordinary = selection["ordinary_test"]
    assert ordinary["market_count"] == 720
    assert ordinary["correct_predictions"] == 665
    assert ordinary["trade_count"] == 139
    assert ordinary["correct_trades"] == 104
    assert ordinary["all_folds_positive_pnl"] is True
    assert ordinary["all_folds_profit_factor_gt_2"] is True

    assert selection["labels_read_non_holdout"] is True
    assert selection["holdout_market_count"] == 288
    assert selection["holdout_labels_read"] is False
    assert selection["holdout_evaluated"] is False
    assert selection["training_performed"] is True
    assert selection["policy_selected"] is True
    assert selection["automatic_promotion"] is False
    assert selection["activation_performed"] is False

    assert evidence["integrity"]["holdout_labels_read"] is False
    assert evidence["integrity"]["holdout_evaluated"] is False
    assert evidence["integrity"]["automatic_promotion"] is False
    assert evidence["integrity"]["activation_performed"] is False
