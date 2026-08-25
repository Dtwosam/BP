from pathlib import Path


def test_phase7_cloudshell_helper_keeps_opt_bp_work_remote() -> None:
    source = Path("scripts/deploy/phase7_cloudshell_accept.sh").read_text(encoding="utf-8")

    assert "gcloud compute ssh" in source
    assert "REMOTE_SCRIPT=" in source
    remote_index = source.index("REMOTE_SCRIPT=")
    assert "/opt/bp" not in source[:remote_index]
    assert "git -C /opt/bp fetch" in source[remote_index:]
    assert "phase7_host_acceptance.sh" in source[remote_index:]
    assert "PHASE7_HEAD" in source
    assert "build/phase-7-baseline-modeling" in source


def test_phase7_cloudshell_helper_hands_candidate_to_bp_before_install() -> None:
    source = Path("scripts/deploy/phase7_cloudshell_accept.sh").read_text(encoding="utf-8")

    worktree_index = source.index('git -C /opt/bp worktree add --detach "\\$WT" "\\$SHA"')
    ownership_index = source.index('chown -R bp:bp "\\$WT"')
    acceptance_index = source.index('BP_REPO="\\$WT" bash')
    assert worktree_index < ownership_index < acceptance_index


def test_phase7_host_acceptance_reads_candidate_head_as_bp() -> None:
    source = Path("scripts/deploy/phase7_host_acceptance.sh").read_text(encoding="utf-8")

    assert 'actual_head=$(sudo -u bp git -C "$REPO" rev-parse HEAD)' in source
    assert 'actual_head=$(git -C "$REPO" rev-parse HEAD)' not in source


def test_phase7_host_acceptance_contains_research_and_safety_gates() -> None:
    source = Path("scripts/deploy/phase7_host_acceptance.sh").read_text(encoding="utf-8")

    required = (
        "EXPECTED_HEAD",
        "sudo -u bp git -C \"$REPO\" rev-parse HEAD",
        "LIVE_TRADING_ENABLED",
        "MAX_TRADE_SIZE_USD",
        "MAX_DAILY_LOSS_USD",
        "systemctl is-active bp-recorder",
        "0007_model_training_runs.sql",
        "historical_backfill.py",
        "generate_labels.py",
        "generate_features.py",
        "train_baselines.py",
        "models-first.json",
        "models-second.json",
        "LABELS_5M",
        "LABELS_15M",
        "SEMANTIC_RERUN_MATCH=1",
        "REGISTRY_SECOND_RUN_DELTA=0",
        "PARTITION_VIOLATIONS=0",
        "SINGLE_CLASS_PARTITIONS=0",
        "ARTIFACT_HASH_VIOLATIONS=0",
        "storage_maintenance.py",
        "VERDICT=PASS",
    )
    for needle in required:
        assert needle in source


def test_phase7_host_acceptance_uses_fixed_day_and_minimum_coverage() -> None:
    source = Path("scripts/deploy/phase7_host_acceptance.sh").read_text(encoding="utf-8")

    assert "2026-08-24T00:00:00Z" in source
    assert "2026-08-25T00:00:00Z" in source
    assert "labels[300] < 100" in source
    assert "labels[900] < 30" in source
    assert "/var/lib/bp/evidence/phase7-baseline-modeling" in source
    assert "/var/lib/bp/artifacts/phase7-baseline-modeling" in source


def test_phase7_acceptance_installs_candidate_in_isolated_venv() -> None:
    source = Path("scripts/deploy/phase7_host_acceptance.sh").read_text(encoding="utf-8")

    assert "/var/tmp/bp-phase7-venv-" in source
    assert '"$HOST_PY" -m venv "$VENV"' in source
    assert 'pip install --disable-pip-version-check "$REPO"' in source
    assert "rm -rf \"$VENV\"" in source


def test_phase7_host_acceptance_checks_disk_lightweight_before_expensive_work() -> None:
    source = Path("scripts/deploy/phase7_host_acceptance.sh").read_text(encoding="utf-8")

    disk_health_index = source.index('storage_maintenance.py" disk-health')
    install_index = source.index('pip install --disable-pip-version-check "$REPO"')
    full_report_index = source.index('storage_maintenance.py" report')
    assert disk_health_index < install_index < full_report_index
    assert "DISK_STATUS_BEFORE" in source
    assert "storage-disk-health-before.json" in source


def test_phase7_ci_syntax_checks_both_helpers() -> None:
    source = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "bash -n scripts/deploy/phase7_host_acceptance.sh" in source
    assert "bash -n scripts/deploy/phase7_cloudshell_accept.sh" in source


def test_phase7_runbook_documents_one_line_gate_and_pass_fields() -> None:
    source = Path("docs/PHASE-7-DEPLOYMENT.md").read_text(encoding="utf-8")

    assert "PHASE7_HEAD=" in source
    assert "phase7_cloudshell_accept.sh" in source
    assert "/var/lib/bp/evidence/phase7-baseline-modeling" in source
    assert "Bybit" in source
    assert "403" in source
    for field in (
        "VERDICT=PASS",
        "LABELS_5M=",
        "LABELS_15M=",
        "SEMANTIC_RERUN_MATCH=1",
        "REGISTRY_SECOND_RUN_DELTA=0",
        "PARTITION_VIOLATIONS=0",
        "SINGLE_CLASS_PARTITIONS=0",
        "ARTIFACT_HASH_VIOLATIONS=0",
        "DISK_STATUS=ok",
        "RECORDER_BEFORE=active",
        "RECORDER_AFTER=active",
        "LIVE_TRADING_ENABLED=false",
    ):
        assert field in source
