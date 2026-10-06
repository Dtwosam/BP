from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_fresh_book_shadow_closeout_cloudshell.sh"
)


def test_v4_shadow_closeout_helper_is_shell_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_v4_shadow_closeout_helper_is_exact_run_bound_and_read_only() -> None:
    text = HELPER.read_text(encoding="utf-8")

    for marker in (
        "f5c76576619c35e65fc8a317d47c8a31dd263950",
        "v4-fresh-book-shadow-20261006T140323Z-f5c76576619c",
        "bp-$EXPECTED_RUN_ID.service",
        "/var/lib/bp/evidence/$EXPECTED_RUN_ID.jsonl",
        "6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf",
        "EXPECTED_RUN_SECONDS=86400",
        "DURATION_TOLERANCE_SECONDS=120",
        "verify_v4_fresh_book_shadow_closeout.py",
        "report_v4_fresh_book_pnl.py",
        "systemctl is-active --quiet",
        'fail "shadow_still_active"',
        "v4-source-time-fresh-book-shadow-$EXPECTED_RUN_MAIN",
        "v4-paper-venv-$EXPECTED_RUN_MAIN",
        "--evidence-glob",
        "SERVICE_MUTATION_PERFORMED=false",
        "ORDER_SUBMISSION_PERFORMED=false",
        "WALLET_MATERIAL_LOADED=false",
    ):
        assert marker in text

    for forbidden in (
        "systemctl start",
        "systemctl stop",
        "systemctl restart",
        "systemctl enable ",
        "post_order(",
        "create_market_order",
        "POLYMARKET_PRIVATE_KEY",
    ):
        assert forbidden not in text
