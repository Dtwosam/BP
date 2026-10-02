from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_fresh_book_shadow_run_cloudshell.sh"
)


def test_v4_fresh_book_shadow_run_helper_is_shell_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_v4_fresh_book_shadow_run_helper_is_hash_bound_and_money_disabled() -> None:
    text = HELPER.read_text(encoding="utf-8")

    for marker in (
        'EXPECTED_MODEL_SHA256="6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf"',
        "EXPECTED_MODEL_SIZE_BYTES=230132",
        "RUN_SECONDS >= 300 && RUN_SECONDS <= 43200",
        "git archive --format=tar.gz",
        '"database_read_only":true',
        "MODE=research",
        "LIVE_TRADING_ENABLED=false",
        "MAX_TRADE_SIZE_USD=0",
        "MAX_DAILY_LOSS_USD=0",
        "fast_live_source_active",
        "fast_live_source_enabled",
        "v3_fresh_book_shadow_already_running",
        "v4_fresh_book_shadow_already_running",
        "frozen_v4_model_artifact_not_found",
        "sha256sum",
        "frozen-v4-model.joblib",
        "load_frozen_v4_bundle",
        "--quote-fresh-seconds 0.25",
        "--max-decision-lag-seconds 2.0",
        "RuntimeMaxSec=",
        "--collect",
        "--uid=bp",
        "--gid=bp",
        '"event":"v4_fresh_book_shadow_started"',
        '"database_read_only":true',
        '"order_submission_enabled":false',
        '"holdout_labels_read":false',
        '"model_refit_performed":false',
        '"threshold_tuning_performed":false',
        "PHASE14_V4_FRESH_BOOK_SHADOW_RUN=PASS",
        "DATABASE_WRITES_PERFORMED=false",
        "HOLDOUT_LABELS_READ=false",
        "ORDER_SUBMISSION_PERFORMED=false",
        "WALLET_MATERIAL_LOADED=false",
    ):
        assert marker in text

    for forbidden in (
        "systemctl enable",
        "systemctl restart",
        "post_order(",
        "create_market_order",
        "POLYMARKET_PRIVATE_KEY=",
        "POLYMARKET_WALLET_ADDRESS=",
    ):
        assert forbidden not in text
