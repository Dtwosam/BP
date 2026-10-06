from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_recorder_current_visibility_cloudshell.sh"
)


def test_v4_recorder_current_visibility_helper_is_shell_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_v4_recorder_current_visibility_helper_is_read_only() -> None:
    text = HELPER.read_text(encoding="utf-8")

    for marker in (
        "RECORDER_BATCH_SIZE",
        "RECORDER_WRITER_WORKERS",
        "RECORDER_FLUSH_INTERVAL_SECONDS",
        "RECORDER_QUEUE_MAXSIZE",
        "report_v4_recorder_visibility.py",
        "core_six_anchor_only",
        "received_at_lte_decision_at",
        "SHADOW_RETRY_COUNT_MAX",
        "SHADOW_SOURCE_REASON_COUNTS",
        "PHASE14_V4_RECORDER_CURRENT_VISIBILITY=PASS",
        "DATABASE_ACCESS=read_only",
        "DATABASE_WRITES_PERFORMED=false",
        "SERVICE_MUTATION_PERFORMED=false",
        "ORDER_SUBMISSION_PERFORMED=false",
        "LIVE_TRADING_ENABLED=false",
        "REAL_MONEY_USD=0",
    ):
        assert marker in text

    for forbidden in (
        "systemctl restart",
        "systemctl stop",
        "systemctl start",
        "systemctl enable",
        "systemctl disable",
        "sed -i",
        "tee /etc/",
        "rm -f /var/lib/bp/evidence",
        "create_market_order",
        "post_order(",
        "POLYMARKET_PRIVATE_KEY=",
        "POLYMARKET_WALLET_ADDRESS=",
    ):
        assert forbidden not in text
