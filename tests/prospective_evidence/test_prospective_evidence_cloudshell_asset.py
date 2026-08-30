from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path("scripts/deploy/phase14_prospective_evidence_cloudshell.sh")


def test_cloudshell_helper_is_exact_head_money_disabled_and_read_only() -> None:
    assert SCRIPT.is_file()
    text = SCRIPT.read_text(encoding="utf-8")

    for required in (
        "PROSPECTIVE_EVIDENCE_HEAD",
        "git -C /opt/bp fetch --no-tags origin",
        "git -C /opt/bp worktree add --detach",
        "bp-paper-execution.service",
        "LIVE_TRADING_ENABLED",
        "MAX_TRADE_SIZE_USD",
        "MAX_DAILY_LOSS_USD",
        "bp_engine.prospective_evidence.cli report",
        "automatic_promotion",
        "PROSPECTIVE_EVIDENCE_HOST_REPORT=PASS",
    ):
        assert required in text

    for forbidden in (
        "systemctl restart",
        "systemctl stop",
        "pip install",
        "alembic",
        "psql ",
    ):
        assert forbidden not in text

    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
