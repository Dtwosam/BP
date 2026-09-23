from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOST_EVIDENCE = (
    ROOT
    / "docs/evidence/phase-15-v3-canary-execution-host-geoblock-20260923.json"
)


def test_phase15_one_dollar_canary_source_truth_is_narrow_and_not_activated() -> None:
    state = json.loads((ROOT / "PROJECT_STATE.json").read_text(encoding="utf-8"))
    gate = state["phase_15_v3_canary_readiness"]
    master = state["phase_14_checkpoint"]["master_live_gate"]

    assert state["source_of_truth_version"] == "0.14.180"
    assert state["current_phase"] == 15
    assert state["status"] == "PHASE_15_ONE_DOLLAR_CANARY_AUTHORIZED_NOT_ACTIVATED"
    assert all(value == "pass" for value in master.values())
    assert state["phase_14_checkpoint"]["overall_live_gate"] == "pass"
    assert state["phase_14_checkpoint"]["phase15_permitted"] is True

    assert gate["status"] == "MASTER_LIVE_GATE_PASS_ONE_DOLLAR_CANARY_CODE_READY_NOT_DEPLOYED"
    assert gate["phase15_permitted"] is True
    assert gate["live_trading_enabled"] is False
    assert gate["real_money_mutation_performed"] is False

    contract = gate["canary_contract"]
    assert contract["version"] == "phase15-v3-one-dollar-canary-v1"
    assert contract["max_live_order_intents"] == 1
    assert contract["max_trade_size_usd"] == "1.00"
    assert contract["max_total_exposure_usd"] == "1.00"
    assert contract["max_daily_loss_usd"] == "2.00"
    assert contract["max_consecutive_losses"] == 2
    assert contract["min_edge"] == "0.075"
    assert contract["strategy_identity_unchanged"] is True
    assert contract["credential_provisioning_automated"] is False
    assert contract["live_activation_automated"] is False
    assert contract["wallet_or_private_key_in_repository"] is False
    assert contract["real_order_submission_requires_external_operator_activation"] is True

    host = gate["execution_host_geoblock"]
    assert host["status"] == "pass"
    assert host["blocked"] is False
    assert host["country"] == "ZA"
    assert host["region"] == "GP"

    evidence = json.loads(HOST_EVIDENCE.read_text(encoding="utf-8"))
    assert evidence["helper_result"] == "PASS"
    assert evidence["geographic_eligibility"]["blocked"] is False
    assert evidence["probe_boundary"]["trading_software_installed"] is False
    assert evidence["probe_boundary"]["wallet_or_signing_material_present"] is False
    assert evidence["probe_boundary"]["live_trading_enabled"] is False
