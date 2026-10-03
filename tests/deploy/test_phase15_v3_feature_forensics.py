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


def test_v3_feature_forensics_helper_stages_exact_main_read_only() -> None:
    text = HELPER.read_text(encoding="utf-8")

    for marker in (
        "git fetch origin main",
        "git pull --ff-only origin main",
        "git archive --format=tar.gz",
        "ARCHIVE_SHA256=",
        "gcloud compute scp",
        "archive_sha_mismatch",
        "REPORT_READ_ONLY=true",
        "V4_HOLDOUT_LABELS_READ=false",
        "scripts/report_v3_fresh_book_trades.py",
        "scripts/report_v3_feature_forensics.py",
        "src/bp_engine/features/v3_models.py",
        'chmod 0755 "$tmp"',
        'PYTHONPATH="$repo/src"',
        "/var/lib/bp/evidence/v3-fresh-book-shadow-*.jsonl",
    ):
        assert marker in text

    for forbidden in (
        "PYTHONPATH=/opt/bp/src",
        "systemctl start",
        "systemctl stop",
        "systemctl restart",
        "systemctl enable",
        "post_order(",
        "create_market_order",
        "LIVE_TRADING_ENABLED=true",
    ):
        assert forbidden not in text
