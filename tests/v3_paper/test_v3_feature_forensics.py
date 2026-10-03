from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "report_v3_feature_forensics.py"


def _load_module():
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location("v3_feature_forensics", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_support_summary_distinguishes_cross_venue_confirmation() -> None:
    module = _load_module()
    features = {
        "coinbase_return_from_market_start": 0.002,
        "coinbase_return_30s": 0.001,
        "coinbase_return_60s": -0.001,
        "coinbase_return_120s": 0.003,
        "bybit_spot_return_from_market_start": -0.001,
        "bybit_spot_return_30s": 0.001,
        "bybit_spot_return_60s": 0.001,
        "bybit_spot_return_120s": 0.002,
        "bybit_linear_return_from_market_start": 0.002,
        "bybit_linear_return_30s": 0.001,
        "bybit_linear_return_60s": 0.001,
        "bybit_linear_return_120s": 0.002,
    }

    summary = module._support_summary(features, selected_side="up")

    assert summary["market_start_venue_votes"] == {
        "support": 2,
        "oppose": 1,
        "zero": 0,
        "present": 3,
    }
    assert summary["all_return_votes"]["support"] == 10
    assert summary["all_return_votes"]["oppose"] == 2
    assert summary["coinbase_selected_side_signed_bps"] == 20.0
    assert summary["bybit_spot_selected_side_signed_bps"] == -10.0


def test_cohort_summary_reports_signed_btc_support_and_pnl() -> None:
    module = _load_module()
    rows = [
        {
            "correct": True,
            "realized_pnl_usd": "4.0",
            "feature_support": {
                "coinbase_selected_side_signed_bps": 20.0,
                "bybit_spot_selected_side_signed_bps": 18.0,
                "bybit_linear_selected_side_signed_bps": 19.0,
                "coinbase_abs_move_bps": 20.0,
                "market_start_venue_votes": {"support": 3},
                "all_return_votes": {"support": 10},
            },
        },
        {
            "correct": False,
            "realized_pnl_usd": "-3.0",
            "feature_support": {
                "coinbase_selected_side_signed_bps": 5.0,
                "bybit_spot_selected_side_signed_bps": -4.0,
                "bybit_linear_selected_side_signed_bps": -3.0,
                "coinbase_abs_move_bps": 5.0,
                "market_start_venue_votes": {"support": 1},
                "all_return_votes": {"support": 4},
            },
        },
    ]

    summary = module._cohort_summary(rows)

    assert summary["trade_count"] == 2
    assert summary["wins"] == 1
    assert summary["losses"] == 1
    assert summary["realized_pnl_usd"] == "1.0"
    assert summary["coinbase_selected_side_signed_bps"]["median"] == 12.5
    assert summary["market_start_support_votes"]["mean"] == 2.0


def test_forensics_source_requires_exact_input_replay() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for marker in (
        "FROZEN_CANDIDATE",
        "FROZEN_MODEL_SHA256",
        "FROZEN_OFFSET_SECONDS",
        "build_v3_feature",
        "_books",
        "_book_descriptor",
        "input_fingerprint",
        "market_probability_response_sha256",
        "fingerprint_match",
        "book_hash_match",
        "provider_source_time_claimed",
        "v4_holdout_labels_read",
    ):
        assert marker in source

    assert "schema.live_predictions" in source
    assert "schema.market_features" not in source
    assert "official-outcome-v1" not in source
