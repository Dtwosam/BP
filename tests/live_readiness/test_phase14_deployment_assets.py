from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "scripts" / "deploy" / "phase14_host_acceptance.sh"
CLOUD = ROOT / "scripts" / "deploy" / "phase14_cloudshell_accept.sh"
RUNBOOK = ROOT / "docs" / "PHASE-14-LIVE-READINESS.md"
RUNNER = ROOT / "scripts" / "run_live_readiness.py"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _text(path: Path) -> str:
    assert path.exists(), f"missing Phase 14 deployment asset: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def test_phase14_acceptance_scripts_exist_and_are_bash_syntax_valid() -> None:
    for path in (HOST, CLOUD):
        _text(path)
        result = subprocess.run(
            ["bash", "-n", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


def test_phase14_host_acceptance_pins_exact_head_and_environment_file() -> None:
    host = _text(HOST)

    for required in (
        "EXPECTED_HEAD",
        "rev-parse HEAD",
        "BP_VERIFIED_HEAD",
        "BP_ENV_FILE",
        "CANDIDATE_HEAD=$EXPECTED_HEAD",
        "PHASE14_HOST_ACCEPTANCE=PASS",
    ):
        assert required in host


def test_phase14_host_acceptance_checks_services_before_and_after() -> None:
    host = _text(HOST)

    for service in (
        "bp-recorder.service",
        "bp-postgres.service",
        "bp-dashboard-api.service",
        "bp-dashboard-web.service",
        "bp-paper-execution.service",
    ):
        assert service in host
    assert host.count("assert_services_active") >= 3
    assert "SERVICES_ACTIVE=PASS" in host
    for forbidden in (
        "systemctl stop bp-recorder.service",
        "systemctl restart bp-recorder.service",
        "systemctl stop bp-postgres.service",
        "systemctl restart bp-postgres.service",
    ):
        assert forbidden not in host


def test_phase14_host_acceptance_enforces_research_live_disabled_zero_limits() -> None:
    host = _text(HOST)

    for required in (
        "MODE",
        "research",
        "TradingMode.RESEARCH",
        "LIVE_TRADING_ENABLED",
        "false",
        "MAX_TRADE_SIZE_USD",
        "MAX_DAILY_LOSS_USD",
        "LIVE_TRADING_ENABLED=false",
        "MAX_TRADE_SIZE_USD=0",
        "MAX_DAILY_LOSS_USD=0",
        "LIVE_GATE_ELIGIBLE=false",
        "REAL_ORDER_SIDE_EFFECTS=0",
    ):
        assert required in host
    assert 'str(settings.mode) != "research"' not in host


def test_phase14_host_acceptance_imports_sdk_and_reads_only_public_geoblock() -> None:
    host = _text(HOST)

    for required in (
        "polymarket",
        "SDK_IMPORT=PASS",
        "GeoblockClient",
        "polymarket_geoblock_url",
        "GEOBLOCK_BLOCKED=",
    ):
        assert required in host
    for forbidden in (
        "SecureClient.create",
        "OfficialPolymarketTradingClient.create_from_environment",
        "load_private_key_for_sdk",
        "POLYMARKET_PRIVATE_KEY",
        "PRIVATE_KEY",
        "seed phrase",
        "WALLET_PRIVATE",
    ):
        assert forbidden not in host


def test_phase14_host_acceptance_proves_gateway_block_with_fail_if_called_client() -> None:
    host = _text(HOST)

    for required in (
        "PolymarketLiveExecutionGateway",
        "sqlite://",
        "fail_client_factory",
        "client_factory=fail_client_factory",
        "INTERLOCK_BLOCKS_SUBMISSION=PASS",
        "RISK_RULES=PASS",
        "LIVE_GATE_ELIGIBLE=false",
    ):
        assert required in host
    assert "client factory must not be called" in host


def test_phase14_host_acceptance_reconciles_without_real_order_side_effects() -> None:
    host = _text(HOST)

    for required in (
        "reconcile_snapshot",
        "RECONCILIATION=PASS",
        "REAL_ORDER_SIDE_EFFECTS=0",
    ):
        assert required in host


def test_phase14_host_acceptance_avoids_secret_echo_and_secret_argv_extraction() -> None:
    host = _text(HOST)

    for forbidden in (
        "set -x",
        "printenv",
        "env |",
        'DATABASE_URL="$DATABASE_URL"',
        '--database-url "$DATABASE_URL"',
        "DATABASE_URL=$(read_env DATABASE_URL)",
        "cat \"$ENV_FILE\"",
    ):
        assert forbidden not in host


def test_phase14_cloudshell_helper_pins_exact_candidate_and_never_passes_secrets() -> None:
    helper = _text(CLOUD)

    for required in (
        "PHASE14_HEAD",
        "phase-14-live-readiness-v1",
        "phase14_host_acceptance.sh",
        "PHASE14_HOST_ACCEPTANCE=PASS",
        "BP_ENV_FILE",
        "candidate_head_changed",
        "worktree_head_mismatch",
    ):
        assert required in helper
    for forbidden in (
        "DATABASE_URL=",
        "--database-url",
        "POLYMARKET_PRIVATE_KEY",
        "PRIVATE_KEY",
        "WALLET_PRIVATE",
    ):
        assert forbidden not in helper


def test_phase14_runbook_documents_non_spending_acceptance_and_master_gate_boundary() -> None:
    runbook = _text(RUNBOOK)

    for required in (
        "Phase 14",
        "LIVE_TRADING_ENABLED=false",
        "MAX_TRADE_SIZE_USD=0",
        "MAX_DAILY_LOSS_USD=0",
        "PHASE14_HOST_ACCEPTANCE=PASS",
        "non-spending",
        "Master live gate",
        "GEOBLOCK_BLOCKED",
        "REAL_ORDER_SIDE_EFFECTS=0",
        "explicit user authorization",
        "do not relocate",
        "do not proxy",
    ):
        assert required in runbook


def test_ci_validates_phase14_deployment_assets_and_readiness_runner() -> None:
    workflow = _text(CI)

    for command in (
        "bash -n scripts/deploy/phase14_host_acceptance.sh",
        "bash -n scripts/deploy/phase14_cloudshell_accept.sh",
        "python -m py_compile scripts/run_live_readiness.py",
    ):
        assert command in workflow
    assert RUNNER.exists()
