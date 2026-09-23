from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODEL_SHA = "124627e15cab3997b8abe54ec5237450d976ab5682953f45a1399a76b6dae0e7"


def test_v3_live_gate_reassessment_source_truth_stays_fail_closed() -> None:
    state = json.loads((ROOT / "PROJECT_STATE.json").read_text(encoding="utf-8"))
    gate = state["phase_14_v3_live_gate_reassessment"]
    assert state["source_of_truth_version"] == "0.14.176"
    assert gate["explicit_user_live_authorization"] == "pass"
    assert gate["prediction_version"] == "v3-frozen-paper-v1"
    assert gate["execution_version"] == "paper-execution-v3-frozen-v1"
    assert gate["model_sha256"] == MODEL_SHA
    assert gate["v3_refit_allowed"] is False
    assert gate["threshold_tuning_allowed"] is False
    assert gate["v4_collection_continues"] is True
    assert gate["master_live_gate_mutated"] is False
    assert gate["phase15_permitted"] is False
    assert gate["live_trading_enabled"] is False
    assert gate["max_trade_size_usd"] == 0
    assert gate["max_daily_loss_usd"] == 0
    assert gate["production_run_performed"] is False
    assert gate["real_money_mutation_performed"] is False

    spec_path = (
        ROOT
        / "docs/superpowers/specs/2026-09-23-phase-14-v3-live-gate-reassessment.md"
    )
    spec = spec_path.read_text(encoding="utf-8")
    assert "explicitly authorized pursuing a controlled real-money transition" in spec
    assert "does not override any other Master live-gate row" in spec
    assert "V4 collection continues unchanged" in spec
