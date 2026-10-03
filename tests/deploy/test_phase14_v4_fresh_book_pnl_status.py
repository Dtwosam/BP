from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "deploy" / "phase14_v4_fresh_book_pnl_status_cloudshell.sh"


def test_v4_pnl_helper_is_shell_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_v4_pnl_helper_is_read_only_and_uses_latest_evidence() -> None:
    text = HELPER.read_text(encoding="utf-8")
    for marker in (
        "git fetch origin main",
        "git pull --ff-only origin main",
        "REPORT_READ_ONLY=true",
        "report_v4_fresh_book_pnl.py",
        "ls -1t /var/lib/bp/evidence/v4-fresh-book-shadow-*.jsonl",
        "PYTHONPATH=/opt/bp/src",
    ):
        assert marker in text
    for forbidden in (
        "systemctl start",
        "systemctl stop",
        "systemctl restart",
        "systemctl enable",
        "post_order(",
        "create_market_order",
    ):
        assert forbidden not in text
