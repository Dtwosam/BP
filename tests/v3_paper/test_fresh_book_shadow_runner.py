from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_v3_fresh_book_shadow.py"


def test_fresh_book_shadow_runner_parses() -> None:
    ast.parse(RUNNER.read_text(encoding="utf-8"))


def test_fresh_book_shadow_runner_is_read_only_and_money_disabled() -> None:
    text = RUNNER.read_text(encoding="utf-8")

    for marker in (
        '"-c default_transaction_read_only=on"',
        "SHOW default_transaction_read_only",
        "StreamingBookCache",
        "V3_PAPER_PREDICTION_VERSION",
        "evaluate_fresh_book_shadow",
        '"order_submission_enabled": False',
        '"wallet_material_loaded": False',
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
        "BP_TELEGRAM_BOT_TOKEN",
        'raise SystemExit("LIVE_TRADING_ENABLED must be false")',
        'raise SystemExit("MAX_TRADE_SIZE_USD must be 0")',
        'raise SystemExit("MAX_DAILY_LOSS_USD must be 0")',
    ):
        assert marker in text

    for forbidden in (
        "post_order(",
        "create_limit_order(",
        "SecureClient.create",
        "from sqlalchemy import insert",
        "from sqlalchemy import update",
        "from sqlalchemy import delete",
        "engine.begin(",
    ):
        assert forbidden not in text


def test_fresh_book_shadow_runner_prewarms_nearby_five_minute_tokens() -> None:
    text = RUNNER.read_text(encoding="utf-8")

    assert "schema.polymarket_markets.c.horizon_seconds == 300" in text
    assert "schema.polymarket_markets.c.active.is_(True)" in text
    assert "schema.polymarket_markets.c.end_at >= now" in text
    assert "schema.polymarket_markets.c.start_at <= future" in text
    assert "cache.subscribe(tokens)" in text
