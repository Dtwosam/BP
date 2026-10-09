from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.report_v4_source_freshness_margin import (
    load_decisions,
    nearest_price_bearing_event,
)

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "scripts" / "report_v4_source_freshness_margin.py"
LATENCY = ROOT / "scripts" / "report_v4_feature_inference_latency.py"


def _write_evidence(path: Path, *, completed: int) -> None:
    events = [
        {
            "event": "v4_fresh_book_shadow_source_ineligible",
            "condition_id": "miss",
            "decision_at": "2026-10-08T23:59:00+00:00",
            "missing_flags": {"bybit_linear_current_missing": True},
            "source_ineligible_reasons": ["bybit_linear_current_missing"],
        },
        {
            "event": "v4_source_time_prediction",
            "condition_id": "predict",
            "decision_at": "2026-10-08T23:44:00+00:00",
        },
        {"event": "v4_fresh_book_shadow_completed", "seen_market_count": completed},
    ]
    path.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )


def test_audit_loads_complete_decisions_and_original_flags(tmp_path: Path) -> None:
    path = tmp_path / "shadow.jsonl"
    _write_evidence(path, completed=2)
    records, count = load_decisions(path)
    assert count == 2
    assert [record["condition_id"] for record in records] == ["predict", "miss"]
    assert records[1]["original_missing_flags"]["bybit_linear_current_missing"] is True


def test_audit_rejects_incomplete_evidence(tmp_path: Path) -> None:
    path = tmp_path / "shadow.jsonl"
    _write_evidence(path, completed=3)
    with pytest.raises(SystemExit, match="incomplete evidence"):
        load_decisions(path)


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""
        self.params = {}

    def execute(self, statement, params):
        self.sql = str(statement)
        self.params = params
        return _Rows(self.rows)


def test_freshness_margin_does_not_use_mark_price_or_bypass_two_seconds() -> None:
    cutoff = datetime(2026, 10, 8, 23, 59, tzinfo=UTC)
    def row(id, age, data):
        at = cutoff - timedelta(seconds=age)
        return {
            "id": id,
            "source_timestamp": at,
            "received_at": at + timedelta(milliseconds=100),
            "event_type": "ticker",
            "payload": {"data": data},
        }

    connection = _Connection([
        row(1, 0.4, {"markPrice": "100.0"}),
        row(2, 2.062, {"lastPrice": "100.0"}),
        row(3, 4.217, {"lastPrice": "99.0"}),
    ])
    report = nearest_price_bearing_event(
        connection,
        source="bybit",
        stream="linear",
        instrument="BTCUSDT",
        cutoff=cutoff,
    )
    best = report["nearest_valid_price_event"]
    assert best["row_id"] == 2
    assert best["source_age_seconds"] == 2.062
    assert best["source_freshness_margin_seconds"] == -0.062
    assert best["passes_timestamp_policy"] is False
    assert best["received_age_seconds"] == 1.962
    assert "markPrice" not in str(report)
    assert "100.0" not in str(report)
    assert connection.params["row_limit"] == 32


def test_audit_is_fail_closed_and_read_only() -> None:
    source = AUDIT.read_text(encoding="utf-8")
    for required in (
        "default_transaction_read_only=on",
        "statement_timeout=3000",
        "mode is not TradingMode.RESEARCH",
        "live_trading_enabled",
        "max_trade_size_usd",
        "max_daily_loss_usd",
        "RECORDER_BATCH_SIZE must be 100",
        "PHASE14_V4_SOURCE_FRESHNESS_AUDIT_STATUS=PASS",
        "database_writes_performed",
        "holdout_labels_read",
        "probe_core_source_time_v4_readiness",
        "candidate_limit_reached",
    ):
        assert required in source
    for forbidden in ("systemctl restart", "systemctl stop", "systemctl start"):
        assert forbidden not in source


def test_latency_has_explicit_shared_and_independent_reader_modes() -> None:
    source = LATENCY.read_text(encoding="utf-8")
    for required in (
        'V4SourceTimeReader() if source_reader_mode == "shared" else None',
        "reader=readiness_reader",
        "reader=feature_reader",
        'choices=("shared", "independent")',
        "default=\"shared\"",
        "shared_reader_matches_shadow_lifecycle",
        "default_transaction_read_only=on",
    ):
        assert required in source
