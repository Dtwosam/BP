from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, insert

from bp_engine.storage.schema import market_labels, metadata, polymarket_markets

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "report_v3_fresh_book_pnl.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("v3_fresh_book_pnl_report", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _market_values(condition_id: str, start: datetime, outcome: str) -> dict[str, object]:
    return {
        "gamma_market_id": f"gamma-{condition_id}",
        "event_id": None,
        "condition_id": condition_id,
        "slug": f"slug-{condition_id}",
        "question": "BTC up or down?",
        "horizon_seconds": 300,
        "start_at": start,
        "end_at": start + timedelta(seconds=300),
        "up_token_id": f"up-{condition_id}",
        "down_token_id": f"down-{condition_id}",
        "resolution_source": "rules",
        "rules_text": "rules",
        "rules_hash": f"rules-{condition_id}",
        "active": False,
        "closed": True,
        "accepting_orders": False,
        "resolved_outcome": outcome,
        "discovered_at": start,
        "updated_at": start + timedelta(seconds=400),
    }


def _label_values(condition_id: str, start: datetime, outcome: str, *, version: str = "official-outcome-v1") -> dict[str, object]:
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
        "label_version": version,
        "source_snapshot_sha256": f"sha256:{'a' * 64}",
        "source_observed_at": start + timedelta(seconds=360),
        "generated_at": start + timedelta(seconds=400),
    }


def _write_epoch(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def _trade(prediction_id: str, condition_id: str, side: str, *, shares: str = "10", cost: str = "4") -> dict[str, object]:
    return {
        "event": "fresh_book_shadow_evaluated",
        "prediction_id": prediction_id,
        "condition_id": condition_id,
        "selected_side": side,
        "trade": True,
        "filled_shares": shares,
        "total_fill_cost": cost,
        "semantic_sha256": prediction_id.rjust(64, "0")[-64:],
    }


def test_report_uses_official_market_labels_not_market_table(tmp_path: Path) -> None:
    module = _load_module()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    start = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)
    epoch = tmp_path / "epoch.jsonl"
    _write_epoch(
        epoch,
        [
            _trade("p1", "condition-1", "up"),
            {
                "event": "fresh_book_shadow_quote_unavailable",
                "prediction_id": "q1",
            },
        ],
    )
    try:
        with engine.begin() as connection:
            connection.execute(
                insert(polymarket_markets).values(
                    **_market_values("condition-1", start, "Up")
                )
            )
            connection.execute(
                insert(market_labels).values(
                    **_label_values("condition-1", start, "Down")
                )
            )
        with engine.connect() as connection:
            report = module.build_report(connection, (epoch,))
        metrics = report["epochs"][0]
        assert report["settlement_source"] == "market_labels"
        assert report["label_version"] == "official-outcome-v1"
        assert metrics["settled"] == 1
        assert metrics["wins"] == 0
        assert metrics["losses"] == 1
        assert metrics["realized_pnl_usd"] == "-4"
        assert metrics["quote_unavailable"] == 1
    finally:
        engine.dispose()


def test_report_keeps_missing_official_label_pending(tmp_path: Path) -> None:
    module = _load_module()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    start = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)
    epoch = tmp_path / "epoch.jsonl"
    _write_epoch(epoch, [_trade("p2", "condition-2", "down")])
    try:
        with engine.begin() as connection:
            connection.execute(
                insert(market_labels).values(
                    **_label_values(
                        "condition-2",
                        start,
                        "Down",
                        version="other-label",
                    )
                )
            )
        with engine.connect() as connection:
            report = module.build_report(connection, (epoch,))
        metrics = report["cumulative"]
        assert metrics["paper_trades"] == 1
        assert metrics["settled"] == 0
        assert metrics["pending"] == 1
        assert metrics["realized_pnl_usd"] == "0"
    finally:
        engine.dispose()


def test_report_deduplicates_identical_prediction_across_epochs(tmp_path: Path) -> None:
    module = _load_module()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    start = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)
    first = tmp_path / "a.jsonl"
    second = tmp_path / "b.jsonl"
    record = _trade("p3", "condition-3", "up", shares="10", cost="4")
    _write_epoch(first, [record])
    _write_epoch(second, [record])
    try:
        with engine.begin() as connection:
            connection.execute(
                insert(market_labels).values(
                    **_label_values("condition-3", start, "Up")
                )
            )
        with engine.connect() as connection:
            report = module.build_report(connection, (first, second))
        cumulative = report["cumulative"]
        assert cumulative["paper_trades"] == 1
        assert cumulative["settled"] == 1
        assert cumulative["realized_pnl_usd"] == "6"
        assert cumulative["duplicate_prediction_count_across_epochs"] == 1
    finally:
        engine.dispose()
