from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]
HOST = ROOT / "scripts" / "deploy" / "phase9_host_acceptance.sh"
CLOUD = ROOT / "scripts" / "deploy" / "phase9_cloudshell_accept.sh"
RUNBOOK = ROOT / "docs" / "PHASE-9-DEPLOYMENT.md"
CI = ROOT / ".github" / "workflows" / "ci.yml"
EDGE = ROOT / "src" / "bp_engine" / "calibration" / "edge.py"

SOURCE_5M = "phase8-300-efdf493067e9d56419afc4d88452bec6"
SOURCE_15M = "phase8-900-64aaf2b1774ee7af37bd110b84b37ec1"
SOURCE_SEMANTIC_5M = (
    "efdf493067e9d56419afc4d88452bec6effb871482664d19f109b3bbe4dd1d93"
)
SOURCE_SEMANTIC_15M = (
    "64aaf2b1774ee7af37bd110b84b37ec19f85bdc875a283986d4dba16ae921828"
)


def _required_text(path: Path) -> str:
    assert path.exists(), f"missing required Phase 9 deployment asset: {path}"
    return path.read_text(encoding="utf-8")


def test_phase9_host_acceptance_contract_is_fail_closed() -> None:
    text = _required_text(HOST)

    required = (
        "BP_VERIFIED_HEAD",
        "LIVE_TRADING_ENABLED",
        "MAX_TRADE_SIZE_USD",
        "MAX_DAILY_LOSS_USD",
        "RECORDER_BEFORE",
        "DISK_STATUS_BEFORE",
        "0009_calibration_edge_runs.sql",
        SOURCE_5M,
        SOURCE_15M,
        SOURCE_SEMANTIC_5M,
        SOURCE_SEMANTIC_15M,
        "2026-08-24T00:00:00Z",
        "2026-08-25T00:00:00Z",
        "run_calibration_edge.py",
        "--fee-rate 0.07",
        "--slippage-buffer 0.01",
        "calibration-first.json",
        "calibration-second.json",
        "REGISTRY_SECOND_RUN_DELTA=0",
        "SEMANTIC_RERUN_MATCH=1",
        "SOURCE_OFFSET_MISMATCH_VIOLATIONS=0",
        "OOS_HOLDOUT_OVERLAP_VIOLATIONS=0",
        "EXECUTABLE_CONTRACT_VIOLATIONS=0",
        "COST_ASSUMPTION_VIOLATIONS=0",
        "VERDICT=PASS",
        "RECORDER_AFTER",
        "DISK_STATUS=ok",
        "PHASE9_HOST_ACCEPTANCE=PASS",
    )
    for token in required:
        assert token in text

    lowered = text.lower()
    for forbidden in (
        "safe.directory",
        "midpoint",
        "synthetic_fill",
        "synthetic fill",
    ):
        assert forbidden not in lowered


def test_phase9_host_probes_dynamic_selected_side_best_ask_edge_contract() -> None:
    host = _required_text(HOST)
    edge = _required_text(EDGE)
    shared_patterns = (
        'side = "up" if probability_up >= 0.5 else "down"',
        'prefix = f"pm_{side}"',
        "missing__{prefix}_book_missing",
        "missing__{prefix}_book_stale",
        "fee = config.fee_rate * ask * (1.0 - ask)",
        "cost_adjusted_edge = raw_edge - fee - config.slippage_buffer",
    )
    for pattern in shared_patterns:
        assert pattern in edge
        assert pattern in host

    assert 'row.predictors.get(f"{prefix}_best_ask")' in host
    assert 'predictors.get(f"{prefix}_best_ask")' in edge
    assert "edge_decision_from_predictors" in edge


def test_phase9_host_uses_bounded_disk_health_preflight_and_postflight() -> None:
    text = _required_text(HOST)

    assert text.count('storage_maintenance.py" disk-health') == 2
    assert 'storage_maintenance.py" report' not in text
    assert "storage-disk-health-before.json" in text
    assert "storage-disk-health-after.json" in text


def test_phase9_cloudshell_wrapper_uses_verified_export_architecture() -> None:
    text = _required_text(CLOUD)

    required = (
        "PHASE9_HEAD",
        "build/phase-9-probability-calibration-edge",
        "git -C /opt/bp worktree add --detach",
        "WORKTREE_HEAD",
        r'git -C "\$WT" archive --format=tar',
        r'sudo -u bp tar -xf - -C "\$SRC"',
        r'BP_REPO="\$SRC"',
        r'BP_VERIFIED_HEAD="\$WORKTREE_HEAD"',
        "phase9_host_acceptance.sh",
        "phase9-host-acceptance-latest.log",
        "PHASE9_HOST_ACCEPTANCE=PASS",
    )
    for token in required:
        assert token in text

    assert "chown" not in text
    assert "safe.directory" not in text


def test_phase9_runbook_documents_costs_evidence_and_valid_no_trade_results() -> None:
    text = _required_text(RUNBOOK).lower()

    required = (
        SOURCE_5M.lower(),
        SOURCE_15M.lower(),
        "2026-08-24t00:00:00z",
        "2026-08-25t00:00:00z",
        "fee-rate 0.07",
        "slippage-buffer 0.01",
        "no_trade",
        "negative",
        "valid research result",
        "not a profitability claim",
        "phase9-host-acceptance-latest.log",
        "live trading",
    )
    for token in required:
        assert token in text


def test_ci_validates_phase9_shell_syntax() -> None:
    text = _required_text(CI)

    assert "bash -n scripts/deploy/phase9_host_acceptance.sh" in text
    assert "bash -n scripts/deploy/phase9_cloudshell_accept.sh" in text
