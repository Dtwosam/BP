import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECOVERY = ROOT / "scripts" / "deploy" / "phase14_v2_gate_b_label_recovery_cloudshell.sh"
RESUME = ROOT / "scripts" / "deploy" / "phase14_v2_gate_b_resume_cloudshell.sh"


def test_gate_b_label_recovery_helper_is_exact_main_and_frozen_plan_only() -> None:
    source = RECOVERY.read_text(encoding="utf-8")
    assert "git fetch --quiet origin main" in source
    assert 'test "$LOCAL_HEAD" = "$REMOTE_MAIN"' in source
    assert 'test -z "$(git status --porcelain)"' in source
    assert "PHASE14_V2_GATE_B_LABEL_RECOVERY_PARTIAL_DIR" in source
    assert '[[ -f "$PARTIAL_DIR/plan.json" ]]' in source
    assert 'test ! -e "$PARTIAL_DIR/selection.json"' in source
    assert 'test ! -e "$PARTIAL_DIR/holdout.json"' in source
    assert 'test ! -e "$PARTIAL_DIR/summary.json"' in source
    assert "run_v2_gate_b_label_recovery.py" in source
    assert "run_v2_gate_b_research.py plan" not in source
    assert "evaluate-holdout" not in source


def test_gate_b_label_recovery_requires_sha_bound_approval_only_for_mutation() -> None:
    source = RECOVERY.read_text(encoding="utf-8")
    assert "PHASE14_V2_GATE_B_LABEL_RECOVERY_ACTION" in source
    assert '"audit"|"recover"' in source
    assert "PHASE14_V2_GATE_B_LABEL_RECOVERY_APPROVAL" in source
    assert "I_APPROVE_PHASE14_V2_GATE_B_LABEL_RECOVERY" in source
    assert 'if [[ "$ACTION" == "recover" ]]' in source
    assert 'test "$APPROVAL" = "$EXPECTED_APPROVAL"' in source
    assert '"missing_label_count"' in source
    assert '"holdout_touched"' in source


def test_gate_b_label_recovery_binds_storage_and_runtime_health() -> None:
    source = RECOVERY.read_text(encoding="utf-8")
    assert "PHASE14_V2_GATE_B_LABEL_RECOVERY_STORAGE_EVIDENCE" in source
    assert "PHASE14_V2_GATE_B_LABEL_RECOVERY_STORAGE_EVIDENCE_SHA256" in source
    assert 'sha256sum "$STORAGE_EVIDENCE"' in source
    assert "SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env" in source
    assert "require_research_zero_money" in source
    assert "require_services" in source
    assert "read_recorder_config_workers" in source
    assert '== "4"' in source
    assert "run_storage_health" in source
    assert "DISK_BEFORE" in source
    assert "DISK_AFTER" in source
    assert "maintenance_fresh" in source
    assert "current_partition_present" in source
    assert "retention_current" in source


def test_gate_b_resume_reuses_existing_plan_and_has_separate_holdout_authorization() -> None:
    source = RESUME.read_text(encoding="utf-8")
    assert "PHASE14_V2_GATE_B_RESUME_PARTIAL_DIR" in source
    assert "PHASE14_V2_GATE_B_RESUME_PLAN_SHA256" in source
    assert "PHASE14_V2_GATE_B_RESUME_APPROVAL" in source
    assert "I_APPROVE_PHASE14_V2_GATE_B_RESUME" in source
    assert 'test -f "$PARTIAL_DIR/plan.json"' in source
    assert 'test ! -e "$PARTIAL_DIR/selection.json"' in source
    assert 'test ! -e "$PARTIAL_DIR/holdout.json"' in source
    assert "run_v2_gate_b_label_recovery.py" in source
    assert " audit " in source
    assert "run_v2_gate_b_research.py" in source
    assert " prepare " in source
    assert " evaluate-holdout " in source
    assert "run_v2_gate_b_research.py plan" not in source
    assert "recover_gate_b_non_holdout_labels" not in source


def test_gate_b_resume_binds_storage_and_runtime_health() -> None:
    source = RESUME.read_text(encoding="utf-8")
    assert "PHASE14_V2_GATE_B_RESUME_STORAGE_EVIDENCE" in source
    assert "PHASE14_V2_GATE_B_RESUME_STORAGE_EVIDENCE_SHA256" in source
    assert 'sha256sum "$STORAGE_EVIDENCE"' in source
    assert "SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env" in source
    assert "require_research_zero_money" in source
    assert "require_services" in source
    assert "read_recorder_config_workers" in source
    assert '== "4"' in source
    assert "run_storage_health" in source
    assert "DISK_BEFORE" in source
    assert "DISK_AFTER" in source
    assert "maintenance_fresh" in source
    assert "current_partition_present" in source
    assert "retention_current" in source


def test_gate_b_recovery_and_resume_helpers_have_clean_bash_syntax() -> None:
    for path in (RECOVERY, RESUME):
        result = subprocess.run(
            ["bash", "-n", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
