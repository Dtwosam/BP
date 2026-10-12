"""Offline contract tests for unapproved Stage B0 read-only classification."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.report_v4_linear_trade_storage_b0 import (
    ROW_CAP_PER_CLASS,
    build_report,
)

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "report_v4_linear_trade_storage_b0.py"
)


class _FakeResult:
    def __init__(self, values):
        self.values = values

    def scalar_one(self):
        return self.values

    def mappings(self):
        return self

    def one(self):
        return self.values


class _FakeConnection:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self, *, readonly=True, rows=None):
        self.readonly = readonly
        self.rows = iter(rows or [])
        self.sql = []
        self.parameters = []

    def execute(self, statement, parameters=None):
        query = str(statement)
        self.sql.append(query)
        self.parameters.append(parameters)
        if "SHOW default_transaction_read_only" in query:
            return _FakeResult("on" if self.readonly else "off")
        return _FakeResult(next(self.rows))


def _summary(*, count: int, null_count: int, array_count=0, price_count=0):
    return {
        "rows_sampled": count,
        "null_source_ts": null_count,
        "nonnull_source_ts": count - null_count,
        "array_data": array_count,
        "last_trade_price_field": price_count,
        "source_time_eligible_at_observed": 0,
    }


def test_reviewed_b0_selects_two_bounded_event_classes_and_no_raw_output() -> None:
    fixed = datetime(2026, 10, 12, 21, 8, tzinfo=UTC)
    connection = _FakeConnection(
        rows=[
            _summary(count=2, null_count=1, array_count=2, price_count=2),
            _summary(count=4, null_count=0),
        ]
    )
    result = build_report(connection, observed_at=fixed)
    assert len(connection.sql) == 3
    assert "SHOW default_transaction_read_only" in connection.sql[0]
    assert "event_type = 'trade'" in connection.sql[1]
    assert "event_type IN ('orderbook_snapshot', 'orderbook_delta')" in connection.sql[2]
    for query, parameters in zip(
        connection.sql[1:], connection.parameters[1:], strict=True
    ):
        assert "LIMIT :row_cap" in query
        assert "received_at >= :lower" in query
        assert "received_at <= :observed_at" in query
        assert "source = 'bybit'" in query
        assert "stream = 'linear'" in query
        assert "instrument = 'BTCUSDT'" in query
        assert parameters["row_cap"] == ROW_CAP_PER_CLASS
        assert parameters["lower"].isoformat() == "2026-10-12T21:07:00+00:00"
        assert parameters["observed_at"] == fixed
        assert "DELETE FROM" not in query
        assert "INSERT INTO" not in query
        assert "UPDATE " not in query

    assert result["max_data_select_queries"] == 2
    assert result["classes"]["trade"]["rows_sampled"] == 2
    assert result["classes"]["trade"]["null_source_timestamp_rows"] == 1
    assert result["classes"]["trade"]["last_trade_price_field_present_rows"] == 2
    assert result["classes"]["orderbook"]["rows_sampled"] == 4
    assert not result["classes"]["trade"]["capped_inconclusive"]
    assert "payload" not in result["classes"]["trade"]
    assert "row_ids" not in result["classes"]["trade"]


def test_b0_capped_sample_flagged_and_not_automatically_retried() -> None:
    fixed = datetime(2026, 10, 12, 21, 8, tzinfo=UTC)
    connection = _FakeConnection(
        rows=[
            _summary(count=ROW_CAP_PER_CLASS, null_count=0),
            _summary(count=0, null_count=0),
        ]
    )
    report = build_report(connection, observed_at=fixed)
    assert report["classes"]["trade"]["capped_inconclusive"] is True
    assert report["classes"]["orderbook"]["capped_inconclusive"] is False
    assert len(connection.sql) == 3


def test_b0_rejects_nonreadonly_database_before_selecting() -> None:
    connection = _FakeConnection(readonly=False)
    with pytest.raises(SystemExit, match="not read-only"):
        build_report(connection, observed_at=datetime(2026, 10, 12, tzinfo=UTC))
    assert len(connection.sql) == 1


def test_b0_source_has_research_zero_money_and_readonly_execution_gates() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for required in (
        "TradingMode.RESEARCH",
        "live_trading_enabled",
        "max_trade_size_usd",
        "max_daily_loss_usd",
        "recorder_batch_size != 100",
        "default_transaction_read_only=on",
        "statement_timeout=1500",
        'isolation_level="AUTOCOMMIT"',
        '--approval-ack',
        'AUTHORIZED_ONE_TIME_STAGE_B0',
    ):
        assert required in source
    assert "CREATE TABLE" not in source
    assert "systemctl restart" not in source
    assert "gcloud compute ssh" not in source
