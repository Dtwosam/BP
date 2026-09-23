from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXECUTOR = ROOT / "scripts/deploy/phase15_v3_canary_executor.py"
BOOTSTRAP = ROOT / "scripts/deploy/phase15_v3_canary_bootstrap_cloudshell.sh"
PREPARE = ROOT / "scripts/deploy/phase15_v3_canary_prepare_cloudshell.sh"
RECORD = ROOT / "scripts/deploy/phase15_v3_canary_record_cloudshell.sh"


def test_executor_is_ten_dollar_geoblock_checked_and_ttl_bounded() -> None:
    text = EXECUTOR.read_text(encoding="utf-8")
    for marker in (
        'MAX_NOTIONAL_USD = Decimal("10")',
        "TTL_SECONDS = 2",
        "https://polymarket.com/api/geoblock",
        'if geo["blocked"] is not False',
        "canary_notional_limit_exceeded",
        "create_limit_order",
        "post_order",
        "cancel_order",
    ):
        assert marker in text
    assert "print(private_key)" not in text
    assert "print(wallet)" not in text


def test_bootstrap_never_submits_an_order() -> None:
    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert "PHASE15_ACCEPT_WALLET_SETUP" in text
    assert "input hidden; never sent to ChatGPT" in text
    assert "polymarket-client==0.7.1" in text
    assert "TRADING_ORDER_SUBMITTED=false" in text
    assert '{"action":"health"}' in text
    assert '{"action":"submit"}' not in text


def test_prepare_is_manual_review_only_and_writes_no_real_order() -> None:
    text = PREPARE.read_text(encoding="utf-8")
    assert "prepare_next_canary" in text
    assert "NO_REAL_ORDER_SUBMITTED=true" in text
    assert "PHASE15_V3_CANARY_PREPARE=PASS" in text
    assert "phase15_v3_canary_executor.py" not in text
    assert "post_order" not in text
    assert "PHASE15_ACCEPT_REAL_MONEY" not in text


def test_record_helper_only_persists_executor_result() -> None:
    text = RECORD.read_text(encoding="utf-8")
    assert "record_canary_submission" in text
    assert "CANARY_RESULT_B64" in text
    assert "post_order" not in text
    assert "create_limit_order" not in text
