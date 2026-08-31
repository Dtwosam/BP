from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
UNIT = ROOT / "deploy" / "bp-prospective-outcomes.service"
CLOUDSHELL = ROOT / "scripts" / "deploy" / "phase14_prospective_outcome_sync_cloudshell.sh"


def test_outcome_sync_unit_is_unprivileged_research_scoped_and_network_capable() -> None:
    text = UNIT.read_text()

    for required in (
        "User=bp",
        "Group=bp",
        "EnvironmentFile=/etc/bp/bp.env",
        "Environment=MODE=research",
        "Environment=LIVE_TRADING_ENABLED=false",
        "Environment=MAX_TRADE_SIZE_USD=0",
        "Environment=MAX_DAILY_LOSS_USD=0",
        "ExecStart=/opt/bp/.venv/bin/python -m bp_engine.prospective_outcomes run",
        "NoNewPrivileges=true",
        "ProtectHome=true",
        "ProtectSystem=full",
        "PrivateTmp=true",
        "Restart=always",
    ):
        assert required in text
    assert "IPAddressDeny=any" not in text
    assert "wallet" not in text.lower()
    assert "private_key" not in text.lower()


def test_cloudshell_acceptance_is_exact_head_money_disabled_and_non_deploying() -> None:
    text = CLOUDSHELL.read_text()

    for required in (
        "PROSPECTIVE_OUTCOME_SYNC_HEAD",
        "PROSPECTIVE_OUTCOME_SYNC_HOST_ACCEPTANCE=PASS",
        "LIVE_TRADING_ENABLED",
        "MAX_TRADE_SIZE_USD",
        "MAX_DAILY_LOSS_USD",
        "bp-paper-execution.service",
        "bp-live-predictor.service",
        "git -C /opt/bp worktree add --detach",
        "-m bp_engine.prospective_outcomes once",
        "-m bp_engine.execution --once",
        "DEPLOYED_HEAD_UNCHANGED",
    ):
        assert required in text
    for forbidden in (
        "pip install",
        "systemctl restart",
        "systemctl stop",
        "git -C /opt/bp checkout",
        "git -C /opt/bp reset",
    ):
        assert forbidden not in text
