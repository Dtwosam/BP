from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "scripts" / "deploy" / "phase13_host_acceptance.sh"
CLOUD = ROOT / "scripts" / "deploy" / "phase13_cloudshell_accept.sh"
RUNBOOK = ROOT / "docs" / "PHASE-13-RESEARCH.md"
CI = ROOT / ".github" / "workflows" / "ci.yml"

ACCEPTED_PHASE9_5M_RUN_ID = "phase9-300-c9f0e00eb7836af08008c66909f8f179"
ACCEPTED_PHASE9_5M_SHA = (
    "c9f0e00eb7836af08008c66909f8f179f03089413426508469353c75bcbcae24"
)
ACCEPTED_PHASE8_5M_RUN_ID = "phase8-300-efdf493067e9d56419afc4d88452bec6"
ACCEPTED_PHASE8_5M_SHA = (
    "efdf493067e9d56419afc4d88452bec6effb871482664d19f109b3bbe4dd1d93"
)
ACCEPTED_PHASE7_5M_RUN_ID = "phase7-300-0a822e17ceced11742bf6d3bc8214f44"
ACCEPTED_PHASE7_5M_SHA = (
    "0a822e17ceced11742bf6d3bc8214f44f4755c7bc23bb1d3f2dcfa897f3edcc0"
)


def _text(path: Path) -> str:
    assert path.exists(), f"missing Phase 13 deployment asset: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def test_phase13_acceptance_scripts_exist_and_are_bash_syntax_valid() -> None:
    for path in (HOST, CLOUD):
        _text(path)
        result = subprocess.run(
            ["bash", "-n", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


def test_phase13_host_acceptance_pins_exact_head_and_keeps_secrets_out_of_argv() -> None:
    host = _text(HOST)

    for required in (
        "EXPECTED_HEAD",
        "rev-parse HEAD",
        "BP_ENV_FILE",
        "get_settings",
        "PHASE13_HOST_ACCEPTANCE=PASS",
    ):
        assert required in host
    for forbidden in (
        'DATABASE_URL="$DATABASE_URL"',
        '--database-url "$DATABASE_URL"',
        "DATABASE_URL=$(read_env DATABASE_URL)",
        "PRIVATE_KEY",
        "WALLET_PRIVATE",
        "LiveExecutionGateway",
    ):
        assert forbidden not in host


def test_phase13_host_acceptance_enforces_research_money_and_execution_boundary() -> None:
    host = _text(HOST)

    for required in (
        "MODE",
        "research",
        "LIVE_TRADING_ENABLED",
        "false",
        "MAX_TRADE_SIZE_USD",
        "MAX_DAILY_LOSS_USD",
        "execution_available",
        "LIVE_TRADING_ENABLED=false",
        "MAX_TRADE_SIZE_USD=0",
        "MAX_DAILY_LOSS_USD=0",
    ):
        assert required in host
    assert "execution_available is not False" in host or "execution_available" in host


def test_phase13_host_acceptance_verifies_exact_immutable_champion_chain() -> None:
    host = _text(HOST)

    for required in (
        ACCEPTED_PHASE9_5M_RUN_ID,
        ACCEPTED_PHASE9_5M_SHA,
        ACCEPTED_PHASE8_5M_RUN_ID,
        ACCEPTED_PHASE8_5M_SHA,
        ACCEPTED_PHASE7_5M_RUN_ID,
        ACCEPTED_PHASE7_5M_SHA,
        "load_champion_ref",
        "load_phase9_report",
    ):
        assert required in host


def test_phase13_host_acceptance_requires_idempotent_research_records_and_safe_decision() -> None:
    host = _text(HOST)

    for required in (
        "build_spread_guard_experiment",
        "register_experiment",
        "evaluate_experiment",
        "record_decision",
        "PromotionDecision.KEEP_CHAMPION",
        "PromotionDecision.PROMOTE_CHALLENGER",
        "promotion_eligible",
        "independent_confirmation_missing",
        "EXPERIMENT_IDEMPOTENT=PASS",
        "EVALUATION_IDEMPOTENT=PASS",
        "PROMOTION_GUARD=PASS",
        "DECISION=keep_champion",
    ):
        assert required in host
    assert host.count("register_experiment(") >= 2
    assert host.count("evaluate_experiment(") >= 2
    assert "semantic_sha256" in host


def test_phase13_host_acceptance_preserves_five_services_and_reconciliation() -> None:
    host = _text(HOST)

    for required in (
        "bp-recorder.service",
        "bp-postgres.service",
        "bp-dashboard-api.service",
        "bp-dashboard-web.service",
        "bp-paper-execution.service",
        "reconciliation",
        "RECONCILIATION=PASS",
        "SERVICES_ACTIVE=PASS",
    ):
        assert required in host
    for forbidden in (
        "systemctl stop bp-recorder.service",
        "systemctl restart bp-recorder.service",
        "systemctl stop bp-postgres.service",
        "systemctl restart bp-postgres.service",
    ):
        assert forbidden not in host


def test_phase13_cloudshell_helper_pins_exact_candidate_without_database_secret_arguments() -> None:
    helper = _text(CLOUD)

    for required in (
        "PHASE13_HEAD",
        "phase-13-improvement-loop-v1",
        "phase13_host_acceptance.sh",
        "PHASE13_HOST_ACCEPTANCE=PASS",
        "BP_ENV_FILE",
    ):
        assert required in helper
    for forbidden in (
        "DATABASE_URL=",
        "--database-url",
        "PRIVATE_KEY",
        "WALLET_PRIVATE",
    ):
        assert forbidden not in helper


def test_phase13_runbook_documents_research_evidence_and_keep_champion_semantics() -> None:
    runbook = _text(RUNBOOK)

    for required in (
        "Phase 13",
        "research",
        "LIVE_TRADING_ENABLED=false",
        "MAX_TRADE_SIZE_USD=0",
        "MAX_DAILY_LOSS_USD=0",
        "fresh_holdout",
        "prospective_paper",
        "independent_confirmation_missing",
        "keep_champion",
        "promotion_eligible",
        "PHASE13_HOST_ACCEPTANCE=PASS",
    ):
        assert required in runbook


def test_ci_validates_phase13_deployment_assets() -> None:
    workflow = _text(CI)

    for command in (
        "bash -n scripts/deploy/phase13_host_acceptance.sh",
        "bash -n scripts/deploy/phase13_cloudshell_accept.sh",
        "python -m py_compile scripts/run_improvement.py",
    ):
        assert command in workflow
