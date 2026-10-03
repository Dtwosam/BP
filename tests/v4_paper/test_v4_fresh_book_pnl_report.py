from __future__ import annotations

import importlib.util
import json
import sys
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
        {"event": "v4_fresh_book_shadow_source_ineligible"},
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
    ):
        assert marker in source
    assert "resolved_outcome" not in source
