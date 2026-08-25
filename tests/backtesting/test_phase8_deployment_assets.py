from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]
HOST = ROOT / "scripts" / "deploy" / "phase8_host_acceptance.sh"
CLOUD = ROOT / "scripts" / "deploy" / "phase8_cloudshell_accept.sh"
RUNBOOK = ROOT / "docs" / "PHASE-8-DEPLOYMENT.md"
CI = ROOT / ".github" / "workflows" / "ci.yml"
EXECUTION = ROOT / "src" / "bp_engine" / "backtesting" / "execution.py"

SOURCE_5M = "phase7-300-0a822e17ceced11742bf6d3bc8214f44"
SOURCE_15M = "phase7-900-e36d978aecc29816c5b9e2b67b30d6e2"


def _required_text(path: Path) -> str:
    assert path.exists(), f"missing required Phase 8 deployment asset: {path}"
    return path.read_text(encoding="utf-8")


def test_phase8_host_acceptance_contract_is_fail_closed() -> None:
    text = _required_text(HOST)

    required = (
        "BP_VERIFIED_HEAD",
        "LIVE_TRADING_ENABLED",
        "MAX_TRADE_SIZE_USD",
        "MAX_DAILY_LOSS_USD",
        "RECORDER_BEFORE",
        "DISK_STATUS_BEFORE",
        "0008_backtest_runs.sql",
        SOURCE_5M,
        SOURCE_15M,
        "2026-08-24T00:00:00Z",
        "2026-08-25T00:00:00Z",
        "run_walk_forward_backtest.py",
        "backtests-first.json",
        "backtests-second.json",
        "REGISTRY_SECOND_RUN_DELTA=0",
        'for horizon, suffix in ((300, "5M"), (900, "15M")):',
        "FOLDS_{suffix}",
        "FINAL_HOLDOUT_{suffix}",
        "PARTITION_OVERLAP_VIOLATIONS=0",
        "ORDINARY_TEST_REUSE_VIOLATIONS=0",
        "SINGLE_CLASS_PARTITIONS=0",
        "PREDICTION_COVERAGE_VIOLATIONS=0",
        "NONFINITE_METRIC_VIOLATIONS=0",
        "EXECUTION_SEMANTIC_VIOLATIONS=0",
        "SEMANTIC_RERUN_MATCH=1",
        "VERDICT=PASS",
        "RECORDER_AFTER",
        "DISK_STATUS=ok",
    )
    for token in required:
        assert token in text

    assert "safe.directory" not in text
    assert "httpx" not in text
    assert "websockets" not in text


def test_phase8_host_execution_source_probe_matches_dynamic_selected_side_lookup() -> None:
    host = _required_text(HOST)
    execution = _required_text(EXECUTION)
    required_patterns = (
        'prefix = "pm_up" if probability >= 0.5 else "pm_down"',
        'row.predictors.get(f"{prefix}_best_ask")',
        "missing__{prefix}_book_missing",
        "missing__{prefix}_book_stale",
        "gross_execution_pnl_before_costs",
    )

    for pattern in required_patterns:
        assert pattern in execution
        assert pattern in host

    assert '"pm_up_best_ask",' not in host
    assert '"pm_down_best_ask",' not in host


def test_phase8_host_uses_bounded_disk_health_for_preflight_and_postflight() -> None:
    text = _required_text(HOST)

    assert text.count('storage_maintenance.py" disk-health') == 2
    assert 'storage_maintenance.py" report' not in text
    assert "storage-disk-health-before.json" in text
    assert "storage-disk-health-after.json" in text


def test_phase8_cloudshell_wrapper_uses_verified_export_architecture() -> None:
    text = _required_text(CLOUD)

    required = (
        "PHASE8_HEAD",
        "build/phase-8-walk-forward-backtester",
        "git -C /opt/bp worktree add --detach",
        "WORKTREE_HEAD",
        r'git -C "\$WT" archive --format=tar',
        r'sudo -u bp tar -xf - -C "\$SRC"',
        r'BP_REPO="\$SRC"',
        r'BP_VERIFIED_HEAD="\$WORKTREE_HEAD"',
        "phase8_host_acceptance.sh",
        "phase8-host-acceptance-latest.log",
        "PHASE8_HOST_ACCEPTANCE=PASS",
    )
    for token in required:
        assert token in text

    assert "chown" not in text
    assert "safe.directory" not in text


def test_phase8_runbook_documents_evidence_and_valid_negative_research_results() -> None:
    text = _required_text(RUNBOOK)

    required = (
        SOURCE_5M,
        SOURCE_15M,
        "2026-08-24T00:00:00Z",
        "2026-08-25T00:00:00Z",
        "VERDICT=PASS",
        "phase8-host-acceptance-latest.log",
        "phase8-backtests",
        "negative",
        "execution coverage",
        "before-costs",
        "not net profitability",
        "live trading",
    )
    lowered = text.lower()
    for token in required:
        assert token.lower() in lowered


def test_ci_validates_phase8_shell_syntax() -> None:
    text = _required_text(CI)

    assert "bash -n scripts/deploy/phase8_host_acceptance.sh" in text
    assert "bash -n scripts/deploy/phase8_cloudshell_accept.sh" in text
