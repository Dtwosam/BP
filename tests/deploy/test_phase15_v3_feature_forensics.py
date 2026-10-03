from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_feature_forensics_cloudshell.sh"
)


def test_v3_feature_forensics_helper_is_shell_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_v3_feature_forensics_helper_is_read_only() -> None:
    text = HELPER.read_text(encoding="utf-8")

    for marker in (
        "git fetch origin main",
        "git pull --ff-only origin main",
        "REPORT_READ_ONLY=true",
        "V4_HOLDOUT_LABELS_READ=false",
        "report_v3_fresh_book_trades.py",
        "report_v3_feature_forensics.py",
        "/var/lib/bp/evidence/v3-fresh-book-shadow-*.jsonl",
        "PYTHONPATH=/opt/bp/src",
        'chmod 0755 \\"\\$tmp\\"',
    ):
        assert marker in text

    for forbidden in (
        "systemctl start",
        "systemctl stop",
        "systemctl restart",
        "systemctl enable",
        "post_order(",
        "create_market_order",
        "LIVE_TRADING_ENABLED=true",
    ):
        assert forbidden not in text
