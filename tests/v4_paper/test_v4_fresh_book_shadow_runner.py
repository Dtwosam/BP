from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_v4_fresh_book_shadow.py"


def test_v4_shadow_runner_is_read_only_money_disabled_and_source_time_safe() -> None:
    text = RUNNER.read_text(encoding="utf-8")

    for marker in (
        "--model-path",
        "load_frozen_v4_bundle",
        "build_source_time_v4_features",
        "predict_frozen_v4_probability",
        "StreamingBookCache",
        "quote_fresh_seconds",
        "MAX_SOURCE_AGE_SECONDS",
        "MAX_FUTURE_SKEW_SECONDS",
        "MAX_DECISION_LAG_SECONDS = 2.0",
        '"-c default_transaction_read_only=on"',
        "SHOW default_transaction_read_only",
        "TARGET_NOTIONAL_USD",
        "FROZEN_V4_MIN_EDGE",
        "EXTREME_EDGE_OBSERVATION_THRESHOLD",
        '"extreme_edge_policy": "observe_only_no_block"',
        '"extreme_edge_evaluated_count"',
        '"extreme_edge_trade_count"',
        '"holdout_labels_read": False',
        '"model_refit_performed": False',
        '"threshold_tuning_performed": False',
        '"order_submission_enabled": False',
        '"order_submission_performed": False',
        '"database_writes_performed": False',
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
    ):
        assert marker in text

    for forbidden in (
        "post_order(",
        "create_market_order",
        "market_order",
        "connection.execute(insert",
        "connection.execute(update",
        "connection.execute(delete",
    ):
        assert forbidden not in text.lower()
