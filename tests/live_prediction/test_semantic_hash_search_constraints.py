from __future__ import annotations

import math
from decimal import Decimal

from bp_engine.features.hashing import canonical_hash
from bp_engine.live_prediction.cli import _ambiguous_prediction_hash_matches

LEDGER_QUANTUM = Decimal("0.000000000000000001")


def test_prediction_hash_search_keeps_correlated_market_probability_as_one_choice() -> None:
    original_probability = 9.99999999999995e-06
    stored_probability = Decimal(str(original_probability)).quantize(LEDGER_QUANTUM)
    stored_center = float(stored_probability)
    assert stored_center != original_probability

    original_values = {
        "training_prior": 0.48,
        "raw_probability": original_probability,
        "market_probability": original_probability,
    }
    row = {
        "semantic_sha256": canonical_hash(original_values),
        "edge_decision": {"side": "up"},
        "training_prior": Decimal("0.480000000000000000"),
        "raw_probability": stored_probability,
        "market_probability": stored_probability,
        "up_best_bid": Decimal("0.500000000000000000"),
        "up_best_ask": Decimal("0.510000000000000000"),
        "down_best_bid": None,
        "down_best_ask": None,
    }
    recovered_values = {
        "training_prior": 0.48,
        "raw_probability": stored_center,
        "market_probability": stored_center,
    }

    assert _ambiguous_prediction_hash_matches(row, recovered_values) is True

    tampered = dict(row)
    tampered_probability = stored_probability + LEDGER_QUANTUM
    tampered["raw_probability"] = tampered_probability
    tampered["market_probability"] = tampered_probability
    assert _ambiguous_prediction_hash_matches(tampered, recovered_values) is False


def test_prediction_hash_search_reaches_float_beyond_legacy_neighbor_window() -> None:
    stored_probability = Decimal("0.000010000000000000")
    stored_center = float(stored_probability)
    original_probability = stored_center
    for _ in range(100):
        original_probability = math.nextafter(original_probability, math.inf)

    assert Decimal(str(original_probability)).quantize(LEDGER_QUANTUM) == stored_probability
    assert original_probability != stored_center

    original_values = {
        "training_prior": 0.48,
        "raw_probability": original_probability,
        "market_probability": original_probability,
    }
    row = {
        "semantic_sha256": canonical_hash(original_values),
        "edge_decision": {"side": "up"},
        "training_prior": Decimal("0.480000000000000000"),
        "raw_probability": stored_probability,
        "market_probability": stored_probability,
        "up_best_bid": Decimal("0.500000000000000000"),
        "up_best_ask": Decimal("0.510000000000000000"),
        "down_best_bid": None,
        "down_best_ask": None,
    }
    recovered_values = {
        "training_prior": 0.48,
        "raw_probability": stored_center,
        "market_probability": stored_center,
    }

    assert _ambiguous_prediction_hash_matches(row, recovered_values) is True
