from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts/deploy/phase14_v2_outcome_label_coverage_rollout_preflight_cloudshell.sh"
CI = ROOT / ".github/workflows/ci.yml"


def test_rollout_preflight_binds_exact_isolated_candidate_and_future_approval() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for required in (
        "PHASE14_V2_OUTCOME_LABEL_ROLLOUT_HELPER_HEAD",
        "I_APPROVE_PHASE14_V2_OUTCOME_LABEL_ROLLOUT",
        "EXPECTED_APPROVAL",
        "71b33d3beaba4a11ef93e7c5bde1c517323f3440",
        "7c3af78da1922a0e5187c24b799951130cc98887",
        "ops/phase14-v2-outcome-label-coverage-rollout-candidate",
        "src/bp_engine/prospective_outcomes/service.py",
        "tests/prospective_outcomes/test_prospective_outcome_sync_service.py",
        "candidate_scope_mismatch",
        "candidate_runtime_blob_not_exact_main",
        "candidate_test_blob_not_exact_main",
        "remote_main_changed",
        "candidate_branch_changed",
    ):
        assert required in content


def test_rollout_preflight_checks_production_research_storage_and_services_read_only() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for required in (
        "phase14-partitioned-storage-rollout-20260909T070219Z.json",
        "f33a28f5306e46c509b0000a176d226c079aa2d160d595095ca228118542ce19",
        "mode_not_research",
        "live_trading_enabled",
        "max_trade_size_nonzero",
        "max_daily_loss_nonzero",
        "automatic_promotion must remain false",
        'payload.get("status") != "ok"',
        '"maintenance_fresh"',
        '"current_partition_present"',
        '"retention_current"',
        "bp-recorder.service",
        "bp-live-predictor.service",
        "bp-paper-execution.service",
        "bp-prospective-outcomes.service",
        "PRODUCTION_MUTATIONS_PERFORMED=false",
        "HOLDOUT_ACCESS_PERFORMED=false",
        "GATE_B_ACTIONS_PERFORMED=false",
    ):
        assert required in content

    for forbidden in (
        "systemctl stop",
        "systemctl start",
        "systemctl restart",
        "git -C \"$REPO\" checkout",
        "gcloud compute disks",
        "evaluate-holdout",
        "run_v2_gate_b_research.py",
        "phase14_v2_gate_b_resume",
        "LIVE_TRADING_ENABLED=true",
    ):
        assert forbidden not in content


def test_rollout_preflight_emits_exact_boundaries_without_consuming_authorization() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for required in (
        "PHASE14_V2_OUTCOME_LABEL_ROLLOUT_PREFLIGHT=PASS",
        "EXPECTED_APPROVAL=$EXPECTED_APPROVAL",
        "FROM_HEAD=$FROM_HEAD",
        "CANDIDATE_HEAD=$CANDIDATE_HEAD",
        "STORAGE_EVIDENCE_SHA256=$STORAGE_EVIDENCE_SHA256",
        "OUTCOME_SERVICE_PID=",
        "UNRELATED_SERVICE_PIDS=",
    ):
        assert required in content

    assert "PHASE14_V2_OUTCOME_LABEL_ROLLOUT_APPROVAL" not in content
    assert "production_approval_mismatch" not in content


def test_ci_syntax_checks_outcome_label_rollout_preflight() -> None:
    ci = CI.read_text(encoding="utf-8")
    assert (
        "bash -n scripts/deploy/phase14_v2_outcome_label_coverage_rollout_preflight_cloudshell.sh"
        in ci
    )
