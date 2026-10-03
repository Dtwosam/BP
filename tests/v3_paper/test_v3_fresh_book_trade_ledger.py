from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, insert

from bp_engine.storage.schema import market_labels, metadata

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "report_v3_fresh_book_trades.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("v3_trade_ledger", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _label(condition_id: str, start: datetime, outcome: str) -> dict[str, object]:
    return {
        "condition_id": condition_id,
        "gamma_market_id": f"gamma-{condition_id}",
        "slug": f"slug-{condition_id}",
        "horizon_seconds": 300,
        "market_start_at": start,
        "market_end_at": start + timedelta(seconds=300),
        "official_outcome": outcome,
        "start_reference": None,
        "end_reference": None,
        "resolution_source": "rules",
        "rules_hash": f"rules-{condition_id}",
        "label_source": "polymarket_gamma_snapshot",
        "label_version": "official-outcome-v1",
        "source_snapshot_sha256": f"sha256:{'a' * 64}",
        "source_observed_at": start + timedelta(seconds=360),
        "generated_at": start + timedelta(seconds=400),
    }


def test_trade_ledger_computes_one_settled_trade(tmp_path: Path) -> None:
    module = _load_module()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    start = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)
    epoch = tmp_path / "epoch.jsonl"
    record = {
        "event": "fresh_book_shadow_evaluated",
        "prediction_id": "p1",
        "condition_id": "c1",
        "prediction_recorded_at": (start + timedelta(seconds=240)).isoformat(),
        "quote_observed_at": (
            start + timedelta(seconds=240, milliseconds=100)
        ).isoformat(),
        "selected_side": "up",
        "calibrated_probability_up": "0.80",
        "side_probability": "0.80",
        "best_ask": "0.40",
        "limit_price": "0.41",
        "raw_edge": "0.40",
        "cost_adjusted_edge": "0.36",
        "trade": True,
        "requested_shares": "10",
        "filled_shares": "10",
        "gross_fill_cost": "4.05",
        "total_fees": "0.16",
        "total_fill_cost": "4.21",
        "full_fill": True,
        "displayed_ask_levels": [["0.40", "5"], ["0.41", "10"]],
    }
    epoch.write_text(json.dumps(record) + "\n", encoding="utf-8")
    try:
        with engine.begin() as connection:
            connection.execute(insert(market_labels).values(**_label("c1", start, "Up")))
        with engine.connect() as connection:
            rows = module.build_trade_ledger(connection, (epoch,))
        assert len(rows) == 1
        row = rows[0]
        assert row["correct"] is True
        assert row["realized_pnl_usd"] == "5.79"
        assert row["quote_delay_ms"] == 100
        assert row["top_level_depth"] == "5"
        assert row["executable_depth"] == "15"
        assert row["avg_fill_price"] == "0.405"
        assert row["slippage_from_best"] == "0.005"
        assert row["fill_ratio"] == "1"
    finally:
        engine.dispose()
