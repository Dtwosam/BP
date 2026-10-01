from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_fresh_book_shadow_run_cloudshell.sh"
)


def test_fresh_book_shadow_run_helper_is_shell_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_fresh_book_shadow_run_helper_is_fail_closed_and_money_disabled() -> None:
    text = HELPER.read_text(encoding="utf-8")

    for marker in (
        "bp-phase15-fast-live-source.service",
        'fail "fast_live_source_active"',
        'fail "fast_live_source_enabled"',
        "run_phase15_v3_fast_live_source[.]py",
        "bp-v3-frozen-predictor.service",
        "LIVE_TRADING_ENABLED=false",
        "MAX_TRADE_SIZE_USD=0",
        "MAX_DAILY_LOSS_USD=0",
        "--run-seconds",
        "--quote-fresh-seconds 0.25",
        "fresh_book_shadow_started",
        "DATABASE_WRITES_PERFORMED=false",
        "ORDER_SUBMISSION_ENABLED=false",
        "ORDER_SUBMISSION_PERFORMED=false",
        "WALLET_MATERIAL_LOADED=false",
    ):
        assert marker in text

    for forbidden in (
        "post_order(",
        "create_limit_order(",
        "SecureClient.create",
        "systemctl start bp-phase15-fast-live-source.service",
        "systemctl restart bp-phase15-fast-live-source.service",
        "systemctl enable bp-phase15-fast-live-source.service",
        "gcloud pubsub",
    ):
        assert forbidden not in text


def test_fresh_book_shadow_run_is_bounded_transient_and_read_only() -> None:
    text = HELPER.read_text(encoding="utf-8")

    assert "RUN_SECONDS >= 300 && RUN_SECONDS <= 43200" in text
    assert "run_seconds >= 300 && run_seconds <= 43200" in text
    assert "systemd-run" in text
    assert "--collect" in text
    assert "RuntimeMaxSec=${run_seconds}s" in text
    assert "systemctl enable" not in text
    assert "/var/lib/bp/evidence" in text
    assert "run_v3_fresh_book_shadow.py" in text
