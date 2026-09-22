from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "deploy" / "phase14_observation_cloudshell.sh"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _helper() -> str:
    return HELPER.read_text(encoding="utf-8")


def test_observation_cloudshell_bridge_is_read_only_and_runtime_bound() -> None:
    source = _helper()

    for marker in (
        "52b4355d6f077373b873f7a6f42bc37a20ddbc7b",
        "/var/lib/bp/runtime/v3-paper-9d52eb753355365848a637ffa6663928664bf770",
        "/var/lib/bp/runtime/v4-forward-36b02d0687194173ab5d3862d3b88c6c90607574",
        "default_transaction_read_only=on",
        "SHOW default_transaction_read_only",
        "build_v3_paper_report",
        "build_v4_coverage_report",
        "build_composite_storage_health",
        '"all_observation_guards_ok"',
        "PHASE14_OBSERVATION=PASS",
    ):
        assert marker in source

    forbidden = (
        "git -C \"$REPO\" checkout",
        "systemctl start ",
        "systemctl stop ",
        "systemctl restart ",
        "systemctl enable ",
        "systemctl disable ",
        "systemctl daemon-reload",
        "mkdir ",
        "mktemp ",
        "install -d",
        "Path.mkdir",
        "engine.begin(",
        "connection.commit(",
    )
    for marker in forbidden:
        assert marker not in source


def test_observation_cloudshell_bridge_keeps_zero_money_safety() -> None:
    source = _helper()

    for marker in (
        'MODE=research',
        'LIVE_TRADING_ENABLED=false',
        'MAX_TRADE_SIZE_USD=0',
        'MAX_DAILY_LOSS_USD=0',
        '"research_zero_money": True',
        '"live_trading_enabled": False',
    ):
        assert marker in source


def test_ci_syntax_checks_observation_cloudshell_bridge() -> None:
    ci = CI.read_text(encoding="utf-8")
    assert "bash -n scripts/deploy/phase14_observation_cloudshell.sh" in ci
