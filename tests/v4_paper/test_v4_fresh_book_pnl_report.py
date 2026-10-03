from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "report_v4_fresh_book_pnl.py"


def _load():
    spec = importlib.util.spec_from_file_location("v4_pnl", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_v4_pnl_load_epoch_tracks_extreme_and_source_ineligible(tmp_path: Path) -> None:
    module = _load()
    path = tmp_path / "v4.jsonl"
    records = [
        {
            "event": "v4_fresh_book_shadow_source_ineligible",
            "condition_id": "blocked-1",
            "source_ineligible_reasons": [
                "bybit_linear_current_missing",
                "bybit_linear_market_start_stale",
            ],
        },
        {"event": "v4_fresh_book_shadow_quote_unavailable"},
        {
            "event": "v4_fresh_book_shadow_evaluated",
            "trade": True,
            "prediction_id": "p1",
            "condition_id": "c1",
            "selected_side": "up",
            "filled_shares": "10",
            "total_fill_cost": "4",
            "cost_adjusted_edge": "0.60",
            "extreme_edge_observation": True,
            "semantic_sha256": "abc",
        },
    ]
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")

    epoch = module.load_epoch(path)

    assert epoch["source_ineligible"] == 1
    breakdown = epoch["source_ineligible_breakdown"]
    assert breakdown["reason_counts"] == {
        "bybit_linear_current_missing": 1,
        "bybit_linear_market_start_stale": 1,
    }
    assert breakdown["venue_counts"] == {"bybit_linear": 2}
    assert breakdown["anchor_counts"] == {"current": 1, "market_start": 1}
    assert breakdown["failure_type_counts"] == {"missing": 1, "stale": 1}
    assert breakdown["reason_combination_counts"] == {
        "bybit_linear_current_missing + bybit_linear_market_start_stale": 1
    }
    assert epoch["quote_unavailable"] == 1
    assert epoch["evaluated"] == 1
    trade = epoch["trades"]["p1"]
    assert trade.extreme_edge_observation is True
    assert trade.cost_adjusted_edge == module.Decimal("0.60")


def test_v4_pnl_metrics_use_binary_payout_math() -> None:
    module = _load()
    trades = {
        "win": module.PaperTrade(
            "win", "c1", "up", module.Decimal("10"), module.Decimal("4"),
            module.Decimal("0.2"), False, None
        ),
        "loss": module.PaperTrade(
            "loss", "c2", "down", module.Decimal("5"), module.Decimal("3"),
            module.Decimal("0.6"), True, None
        ),
    }
    metrics = module._metrics(trades, {"c1": "Up", "c2": "Up"})

    assert metrics["settled"] == 2
    assert metrics["wins"] == 1
    assert metrics["losses"] == 1
    assert metrics["realized_pnl_usd"] == "3"
    assert metrics["settled_fill_cost_usd"] == "7"
    assert metrics["profit_factor"] == "2"


def test_v4_pnl_source_uses_official_labels_and_read_only_db() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for marker in (
        "official-outcome-v1",
        "schema.market_labels",
        "default_transaction_read_only=on",
        "SHOW default_transaction_read_only",
        "v4_fresh_book_shadow_evaluated",
        "v4_fresh_book_shadow_source_ineligible",
        "edge_gt_0_50",
        "edge_le_0_50",
        "statement_timeout=5000",
        "SOURCE_TIMING_SAMPLE_LIMIT_PER_REASON",
        "SOURCE_TIMING_ROW_LIMIT_PER_SIDE",
    ):
        assert marker in source
    assert "resolved_outcome" not in source


def test_source_ineligible_summary_counts_reasons_across_venues() -> None:
    module = _load()
    summary = module._source_ineligible_summary(
        [
            {
                "condition_id": "c1",
                "source_ineligible_reasons": [
                    "coinbase_current_missing",
                    "bybit_spot_current_missing",
                ],
            },
            {
                "condition_id": "c2",
                "source_ineligible_reasons": [
                    "coinbase_current_missing",
                    "bybit_linear_current_stale",
                ],
            },
        ]
    )

    assert summary["record_count"] == 2
    assert summary["reason_counts"]["coinbase_current_missing"] == 2
    assert summary["venue_counts"] == {
        "bybit_linear": 1,
        "bybit_spot": 1,
        "coinbase": 2,
    }
    assert summary["anchor_counts"] == {"current": 4}
    assert summary["failure_type_counts"] == {"missing": 3, "stale": 1}
    assert summary["sample_condition_ids_by_reason"]["coinbase_current_missing"] == [
        "c1",
        "c2",
    ]


def test_classify_timing_rows_detects_late_received_event() -> None:
    module = _load()
    requested = datetime(2026, 10, 3, 12, 4, tzinfo=UTC)
    row = {
        "id": 1,
        "source": "bybit",
        "stream": "spot",
        "instrument": "BTCUSDT",
        "event_type": "ticker",
        "source_timestamp": requested - timedelta(milliseconds=200),
        "received_at": requested + timedelta(milliseconds=350),
        "payload": {"data": {"lastPrice": "60000"}},
    }

    diagnostic = module._classify_timing_rows([row], requested)

    assert diagnostic["classification"] == "late_received_after_cutoff"
    assert diagnostic["nearest"]["in_source_window"] is True
    assert diagnostic["nearest"]["metadata_eligible"] is False
    assert diagnostic["nearest"]["received_delta_seconds"] == 0.35


def test_classify_timing_rows_detects_eventually_metadata_eligible() -> None:
    module = _load()
    requested = datetime(2026, 10, 3, 12, 4, tzinfo=UTC)
    row = {
        "id": 2,
        "source": "coinbase",
        "stream": "spot",
        "instrument": "BTC-USD",
        "event_type": "ticker_update",
        "source_timestamp": requested - timedelta(milliseconds=500),
        "received_at": requested - timedelta(milliseconds=100),
        "payload": {
            "events": [
                {
                    "tickers": [
                        {"price": "60000"},
                    ]
                }
            ]
        },
    }

    diagnostic = module._classify_timing_rows([row], requested)

    assert diagnostic["classification"] == "eventually_metadata_eligible"
    assert diagnostic["nearest"]["metadata_eligible"] is True
    assert diagnostic["nearest"]["received_delta_seconds"] == -0.1


def test_cutoff_timing_rows_use_narrow_received_windows_and_limits() -> None:
    module = _load()
    requested = datetime(2026, 10, 3, 12, 4, tzinfo=UTC)

    class _Rows:
        def mappings(self):
            return self

        def __iter__(self):
            return iter(())

    class _Connection:
        statements = []

        def execute(self, statement):
            self.statements.append(statement)
            return _Rows()

    connection = _Connection()
    rows = module._cutoff_timing_rows(
        connection,
        venue="bybit_spot",
        requested_at=requested,
    )

    assert rows == []
    assert len(connection.statements) == 2
    rendered = [str(statement) for statement in connection.statements]
    assert all("raw_market_events.received_at" in statement for statement in rendered)
    assert all("raw_market_events.source_timestamp >=" not in statement for statement in rendered)
    assert all(" LIMIT " in statement for statement in rendered)
    assert module.SOURCE_TIMING_RECEIVED_WINDOW_SECONDS == 4.0
    assert module.SOURCE_TIMING_ROW_LIMIT_PER_SIDE == 200


def test_source_timing_diagnostic_samples_large_reason_sets() -> None:
    module = _load()
    requested = datetime(2026, 10, 3, 12, 4, tzinfo=UTC)

    class _Rows:
        def mappings(self):
            return self

        def __iter__(self):
            return iter(())

    class _Connection:
        execute_count = 0

        def execute(self, statement):
            self.execute_count += 1
            return _Rows()

    records = [
        {
            "decision_at": (requested + timedelta(minutes=index)).isoformat(),
            "source_ineligible_reasons": ["coinbase_current_missing"],
        }
        for index in range(10)
    ]
    connection = _Connection()

    diagnostic = module._source_timing_diagnostic(connection, records)

    reason = diagnostic["by_reason"]["coinbase_current_missing"]
    assert diagnostic["sample_limit_per_reason"] == 3
    assert reason["missing_count"] == 10
    assert reason["diagnosed_count"] == 3
    assert reason["classification_counts"] == {
        "no_usable_event_near_cutoff": 3
    }
    assert connection.execute_count == 6


def test_source_timing_diagnostic_recovers_from_query_timeout() -> None:
    module = _load()
    requested = datetime(2026, 10, 3, 12, 4, tzinfo=UTC)

    class _Connection:
        rolled_back = False

        def execute(self, statement):
            raise module.OperationalError(
                "select",
                {},
                Exception("canceling statement due to statement timeout"),
            )

        def rollback(self):
            self.rolled_back = True

    connection = _Connection()
    diagnostic = module._source_timing_diagnostic(
        connection,
        [
            {
                "decision_at": requested.isoformat(),
                "source_ineligible_reasons": ["bybit_linear_current_missing"],
            }
        ],
    )

    reason = diagnostic["by_reason"]["bybit_linear_current_missing"]
    assert reason["classification_counts"] == {"query_timeout": 1}
    assert connection.rolled_back is True


def test_classify_timing_rows_detects_no_event_in_source_window() -> None:
    module = _load()
    requested = datetime(2026, 10, 3, 12, 4, tzinfo=UTC)
    row = {
        "id": 3,
        "source": "bybit",
        "stream": "linear",
        "instrument": "BTCUSDT",
        "event_type": "trade",
        "source_timestamp": requested - timedelta(seconds=5),
        "received_at": requested - timedelta(seconds=4.9),
        "payload": {"data": [{"p": "60000"}]},
    }

    diagnostic = module._classify_timing_rows([row], requested)

    assert diagnostic["classification"] == "no_usable_event_in_source_window"
    assert diagnostic["nearest"]["source_delta_seconds"] == -5.0
