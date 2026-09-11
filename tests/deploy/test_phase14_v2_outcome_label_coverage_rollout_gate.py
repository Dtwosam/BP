from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts/deploy/phase14_v2_outcome_label_coverage_rollout_cloudshell.sh"
CI = ROOT / ".github/workflows/ci.yml"


def test_rollout_gate_binds_exact_isolated_candidate_and_approval() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for required in (
        "PHASE14_V2_OUTCOME_LABEL_ROLLOUT_HELPER_HEAD",
        "PHASE14_V2_OUTCOME_LABEL_ROLLOUT_APPROVAL",
        "I_APPROVE_PHASE14_V2_OUTCOME_LABEL_ROLLOUT",
        "production_approval_mismatch",
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


def test_rollout_gate_preserves_research_storage_and_unrelated_services() -> None:
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
        "unrelated_service_pid_changed",
        "bp-recorder.service",
        "bp-live-predictor.service",
        "bp-paper-execution.service",
        "bp-prospective-outcomes.service",
    ):
        assert required in content

    assert 'systemctl stop "$OUTCOME_UNIT"' in content
    assert 'systemctl start "$OUTCOME_UNIT"' in content
    assert 'systemctl restart "$OUTCOME_UNIT"' not in content
    assert 'systemctl stop "$RECORDER_UNIT"' not in content
    assert 'systemctl restart "$RECORDER_UNIT"' not in content
    assert 'systemctl stop "$PREDICTOR_UNIT"' not in content
    assert 'systemctl restart "$PREDICTOR_UNIT"' not in content


def test_rollout_gate_rolls_back_checkout_and_outcome_service_only() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for required in (
        "PHASE14_V2_OUTCOME_LABEL_ROLLOUT_ROLLBACK=START",
        "PHASE14_V2_OUTCOME_LABEL_ROLLOUT_ROLLBACK=COMPLETE",
        'git -C "$REPO" checkout --detach "$CANDIDATE_HEAD"',
        'git -C "$REPO" checkout --detach "$FROM_HEAD"',
        '"gate_b_actions_performed": False',
        '"holdout_access_performed": False',
        "phase14-v2-outcome-label-rollout-",
        "PHASE14_V2_OUTCOME_LABEL_ROLLOUT=PASS",
    ):
        assert required in content

    assert "evaluate-holdout" not in content
    assert "run_v2_gate_b_research.py" not in content
    assert "phase14_v2_gate_b_resume" not in content
    assert "LIVE_TRADING_ENABLED=true" not in content


def test_ci_syntax_checks_outcome_label_rollout_gate() -> None:
    ci = CI.read_text(encoding="utf-8")
    assert (
        "bash -n scripts/deploy/phase14_v2_outcome_label_coverage_rollout_cloudshell.sh"
        in ci
    )
