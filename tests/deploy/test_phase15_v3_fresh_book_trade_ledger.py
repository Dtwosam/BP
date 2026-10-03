from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_fresh_book_trade_ledger_cloudshell.sh"
)


def test_v3_trade_ledger_helper_is_shell_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_v3_trade_ledger_is_read_only_and_uses_official_labels() -> None:
    helper = HELPER.read_text(encoding="utf-8")
    report = (ROOT / "scripts" / "report_v3_fresh_book_trades.py").read_text(
        encoding="utf-8"
    )

    for marker in (
        "REPORT_READ_ONLY=true",
        "report_v3_fresh_book_trades.py",
        "/var/lib/bp/evidence/v3-fresh-book-shadow-*.jsonl",
    ):
        assert marker in helper

    for marker in (
        'OFFICIAL_LABEL_VERSION = "official-outcome-v1"',
        "schema.market_labels",
        "default_transaction_read_only=on",
        "SHOW default_transaction_read_only",
        '"settlement_source": "market_labels"',
        '"trade_count": len(ledger)',
    ):
        assert marker in report

    for forbidden in (
        "schema.polymarket_markets.c.resolved_outcome",
        "systemctl start",
        "systemctl stop",
        "systemctl restart",
        "post_order(",
        "create_market_order",
    ):
        assert forbidden not in helper
        assert forbidden not in report
