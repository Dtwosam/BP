from pathlib import Path


def test_phase6_cloudshell_helper_runs_opt_bp_work_only_remotely() -> None:
    path = Path("scripts/deploy/phase6_cloudshell_accept.sh")
    source = path.read_text(encoding="utf-8")

    assert "gcloud compute ssh" in source
    assert "REMOTE_SCRIPT=" in source
    remote_index = source.index("REMOTE_SCRIPT=")
    assert "/opt/bp" not in source[:remote_index]
    assert "git -C /opt/bp fetch" in source[remote_index:]
    assert "phase6_host_acceptance.sh" in source[remote_index:]
    assert "PHASE6_HEAD" in source
    assert "build/phase-6-feature-engine" in source


def test_phase6_host_acceptance_contains_all_fail_closed_gates() -> None:
    path = Path("scripts/deploy/phase6_host_acceptance.sh")
    source = path.read_text(encoding="utf-8")

    required = (
        "EXPECTED_HEAD",
        "git -C \"$REPO\" rev-parse HEAD",
        "LIVE_TRADING_ENABLED",
        "MAX_TRADE_SIZE_USD",
        "MAX_DAILY_LOSS_USD",
        "systemctl is-active bp-recorder",
        "0006_market_features.sql",
        "scripts/generate_features.py",
        "features-first.json",
        "features-second.json",
        "SECOND_RUN_INSERTED",
        "INVALID_FUTURE_CUTOFFS",
        "DUPLICATE_KEYS",
        "LABEL_KEY_VIOLATIONS",
        "OFFICIAL_REFERENCE_VIOLATIONS",
        "DISK_STATUS",
        "storage_maintenance.py report",
        "VERDICT=PASS",
        "RECORDER_BEFORE=",
        "RECORDER_AFTER=",
        "LIVE_TRADING_ENABLED=",
    )
    for needle in required:
        assert needle in source


def test_phase6_host_acceptance_uses_jsonb_cutoff_iterator() -> None:
    source = Path("scripts/deploy/phase6_host_acceptance.sh").read_text(encoding="utf-8")

    assert "jsonb_each_text(f.source_cutoffs)" in source
    assert "json_each_text(f.source_cutoffs)" not in source


def test_phase6_host_acceptance_uses_phase5_window_and_evidence_root() -> None:
    source = Path("scripts/deploy/phase6_host_acceptance.sh").read_text(encoding="utf-8")

    assert "2026-08-24T18:00:00Z" in source
    assert "2026-08-24T19:00:00Z" in source
    assert "/var/lib/bp/evidence/phase6-feature-engine" in source
    assert "TARGET_MARKETS=" in source
    assert "FEATURE_ROWS=" in source


def test_phase6_ci_syntax_checks_both_helpers() -> None:
    source = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "bash -n scripts/deploy/phase6_host_acceptance.sh" in source
    assert "bash -n scripts/deploy/phase6_cloudshell_accept.sh" in source


def test_phase6_runbook_documents_one_line_gate_and_pass_fields() -> None:
    source = Path("docs/PHASE-6-DEPLOYMENT.md").read_text(encoding="utf-8")

    assert "PHASE6_HEAD=" in source
    assert "phase6_cloudshell_accept.sh" in source
    assert "/var/lib/bp/evidence/phase6-feature-engine" in source
    for field in (
        "VERDICT=PASS",
        "SECOND_RUN_INSERTED=0",
        "INVALID_FUTURE_CUTOFFS=0",
        "DUPLICATE_KEYS=0",
        "LABEL_KEY_VIOLATIONS=0",
        "OFFICIAL_REFERENCE_VIOLATIONS=0",
        "RECORDER_BEFORE=active",
        "RECORDER_AFTER=active",
        "LIVE_TRADING_ENABLED=false",
    ):
        assert field in source
