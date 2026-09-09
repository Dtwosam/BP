import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = (
    ROOT
    / "docs"
    / "evidence"
    / "phase-14-v2-freshness-preregistration-20260909.json"
)
STATE = ROOT / "PROJECT_STATE.json"
MASTER = ROOT / "docs" / "MASTER-SOURCE-OF-TRUTH.md"

EXPECTED_COVERAGE_HASH = (
    "aab75574aa7faf18e65358353403e5ec1a2b89dd42424eb7b0e3329bf683b099"
)
EXPECTED_CANDIDATES = [1, 2, 5, 10]


def test_v2_freshness_preregistration_is_coverage_only_and_frozen() -> None:
    payload = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    assert payload["evidence_type"] == "v2_freshness_coverage_only_preregistration"
    assert payload["verdict"] == "PREREGISTERED_NOT_GATE_B"
    assert payload["feature_version"] == "core-v2-last-trade"
    assert payload["selection_basis"] == "coverage_only_no_labels_no_outcomes"
    assert payload["coverage_input_sha256"] == EXPECTED_COVERAGE_HASH

    coverage = payload["coverage"]
    assert coverage["market_count"] == 426
    assert coverage["row_count"] == 1704
    assert coverage["offsets"] == [60, 120, 180, 240]
    assert coverage["future_cutoff_violation_count"] == 0
    assert coverage["invalid_nonfinite_value_count"] == 0

    prereg = payload["freshness_preregistration"]
    assert prereg["max_selected_book_age_seconds"] == 10
    assert prereg["max_last_trade_age_seconds_candidates"] == EXPECTED_CANDIDATES
    assert max(prereg["max_last_trade_age_seconds_candidates"]) <= 10
    assert prereg["include_no_trade"] is True
    assert prereg["validation_selection_required"] is True
    assert prereg["final_holdout_may_not_rewrite_candidates"] is True

    safety = payload["safety"]
    assert safety["policy_selected"] is False
    assert safety["automatic_promotion"] is False
    assert safety["gate_b_authorized"] is False
    assert safety["phase15_permitted"] is False
    assert safety["live_trading_enabled"] is False
    assert safety["max_trade_size_usd"] == 0
    assert safety["max_daily_loss_usd"] == 0

    text = EVIDENCE.read_text(encoding="utf-8").lower()
    for forbidden in (
        '"official_outcome"',
        '"accuracy"',
        '"pnl"',
        '"profit"',
        '"calibration_metric"',
        '"edge_threshold"',
        '"selected_candidate"',
    ):
        assert forbidden not in text


def test_v2_freshness_preregistration_is_bound_into_source_of_truth() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    followup = state["phase_14_market_price_v2_followup"]
    master = MASTER.read_text(encoding="utf-8")

    assert followup["v2_freshness_preregistration_status"] == "FROZEN_COVERAGE_ONLY"
    assert followup["v2_freshness_preregistration_coverage_input_sha256"] == (
        EXPECTED_COVERAGE_HASH
    )
    assert followup["v2_freshness_preregistration_candidates_seconds"] == (
        EXPECTED_CANDIDATES
    )
    assert followup["v2_freshness_preregistration_include_no_trade"] is True
    assert followup["v2_freshness_preregistration_gate_b_authorized"] is False

    assert EXPECTED_COVERAGE_HASH in master
    assert "1, 2, 5, 10" in master
    assert "Gate B remains unauthorized" in master
