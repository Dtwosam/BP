from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/bp_engine/execution/canary_executor.py"


def test_executor_has_atomic_one_submit_guard_and_one_dollar_cap() -> None:
    text = SOURCE.read_text(encoding="utf-8")
    assert 'CANARY_MAX_NOTIONAL_USD = Decimal("1.00")' in text
    assert "os.O_EXCL" in text
    assert "first-submit.reserved" in text
    assert "one-dollar canary submit already consumed" in text
    assert "execution host is geoblocked" in text
    assert "POLYMARKET_PRIVATE_KEY" in text
    assert "print(private_key)" not in text
