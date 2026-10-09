from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.report_v4_linear_price_visibility import (
    CATEGORIES,
    _distribution,
    _load_rows,
    _sample_category,
    build_report,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "report_v4_linear_price_visibility.py"


def _ticker(*, row_id: int, observed_at: datetime, age: float, fields: dict):
    timestamp = observed_at - timedelta(seconds=age)
    return {
        "id": row_id,
        "event_type": "ticker",
        "source_timestamp": timestamp,
        "received_at": timestamp + timedelta(milliseconds=120),
        "payload": {"data": fields},
    }


def _trade(*, row_id: int, observed_at: datetime, age: float):
    timestamp = observed_at - timedelta(seconds=age)
    return {
        "id": row_id,
        "event_type": "trade",
        "source_timestamp": timestamp,
        "received_at": timestamp + timedelta(milliseconds=120),
        "payload": {"data": [{"p": "89000.12"}]},
    }


def test_price_bearing_ticker_is_distinct_from_partial_ticker() -> None:
    cutoff = datetime(2026, 10, 9, 10, 15, tzinfo=UTC)
    rows = [
        _ticker(
            row_id=1,
            observed_at=cutoff,
            age=0.5,
            fields={"markPrice": "90000"},
        ),
        _ticker(
            row_id=2,
            observed_at=cutoff,
            age=2.317,
            fields={"lastPrice": "89000"},
        ),
    ]
    result = _sample_category(rows, category="ticker_last_price", observed_at=cutoff)
    assert result["present"] is True
    assert result["eligible"] is False
    assert result["nearest_row_id"] == 2
    assert result["nearest_source_age_seconds"] == pytest.approx(2.317)
    assert "89000" not in str(result)


def test_price_bearing_trade_can_be_fresh_even_when_tickers_have_no_price() -> None:
    cutoff = datetime(2026, 10, 9, 10, 15, tzinfo=UTC)
    rows = [_trade(row_id=3, observed_at=cutoff, age=0.332)]
    result = _sample_category(rows, category="trade_price", observed_at=cutoff)
    assert result["eligible"] is True
    assert result["nearest_source_age_seconds"] == pytest.approx(0.332)
    assert result["nearest_received_age_seconds"] == pytest.approx(0.212)


def test_nearest_eligible_candidate_is_preferred() -> None:
    cutoff = datetime(2026, 10, 9, 10, 15, tzinfo=UTC)
    rows = [
        _trade(row_id=4, observed_at=cutoff, age=2.062),
        _trade(row_id=5, observed_at=cutoff, age=1.1),
    ]
    result = _sample_category(rows, category="trade_price", observed_at=cutoff)
    assert result["eligible"] is True
    assert result["nearest_row_id"] == 5


class _FakeRows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self.rows)


class _FakeConnection:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.statements = []
        self.params = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        self.params.append(params)
        if "SHOW default_transaction_read_only" in sql:
            return _FakeValue("on")
        return _FakeRows(self.rows)


class _FakeValue:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


def test_query_is_bounded_and_source_filtered() -> None:
    cutoff = datetime(2026, 10, 9, 10, 15, tzinfo=UTC)
    conn = _FakeConnection()
    _load_rows(conn, category="trade_price", observed_at=cutoff)
    statement = conn.statements[-1]
    assert "source = 'bybit'" in statement
    assert "stream = 'linear'" in statement
    assert "instrument = 'BTCUSDT'" in statement
    assert "received_at <= :observed_at" in statement
    assert "LIMIT :limit" in statement
    assert conn.params[-1]["limit"] == 8
    assert conn.params[-1]["lower"] == cutoff - timedelta(seconds=12)
    assert "markPrice" not in CATEGORIES["ticker_last_price"]


def test_report_counts_inconclusive_separately(monkeypatch) -> None:
    from scripts import report_v4_linear_price_visibility as report

    monkeypatch.setattr(report.time, "sleep", lambda _: None)
    conn = _FakeConnection()
    result = build_report(conn, samples=2, interval_seconds=0.5)
    assert result["samples"] == 2
    assert result["price_eligibility_by_sample"]["neither_price_class_eligible"] == 2
    assert result["price_eligibility_by_sample"]["inconclusive_due_to_query_error"] == 0
    assert result["event_categories"]["ticker_any"]["no_usable_row_count"] == 2


def test_report_rejects_unsafe_sampling() -> None:
    conn = _FakeConnection()
    with pytest.raises(ValueError):
        build_report(conn, samples=0, interval_seconds=0.5)
    with pytest.raises(ValueError):
        build_report(conn, samples=2, interval_seconds=0.1)


def test_report_safety_and_no_price_substitution() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for marker in (
        "default_transaction_read_only=on",
        "statement_timeout=1500",
        "isolation_level=\"AUTOCOMMIT\"",
        "TradingMode.RESEARCH",
        "max_trade_size_usd",
        "max_daily_loss_usd",
        "RECORDER_BATCH_SIZE must be 100",
        "MAX_SOURCE_AGE_SECONDS",
        "MAX_FUTURE_SKEW_SECONDS",
        "PHASE14_V4_LINEAR_PRICE_VISIBILITY_STATUS=PASS",
        "_bybit_price",
    ):
        assert marker in source
    for forbidden in (
        "systemctl restart",
        "systemctl stop",
        "systemctl start",
        "markPrice\")",
    ):
        assert forbidden not in source


def test_distribution_returns_none_for_missing_rows() -> None:
    assert _distribution([]) is None
    assert _distribution([0.3, 0.7])["max"] == 0.7
