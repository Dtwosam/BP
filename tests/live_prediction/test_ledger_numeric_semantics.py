from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bp_engine.features.hashing import canonical_hash
from bp_engine.live_prediction.cli import _semantic_hash_matches
from bp_engine.live_prediction.models import LivePrediction
from bp_engine.live_prediction.repository import _semantically_equal

LEDGER_QUANTUM = Decimal("0.000000000000000001")
NUMERIC_FIELDS = (
    "min_edge",
    "training_prior",
    "raw_probability",
    "calibrated_probability",
    "market_probability",
    "up_best_bid",
    "up_best_ask",
    "down_best_bid",
    "down_best_ask",
    "selected_ask",
    "selected_bid",
    "selected_spread",
    "fee",
    "slippage_buffer",
    "raw_edge",
    "cost_adjusted_edge",
    "decision_min_edge",
)


def _prediction() -> LivePrediction:
    start = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    scheduled = start + timedelta(minutes=4)
    calibrated_probability = 0.5788541926934843
    ask = 0.58
    bid = 0.57
    spread = ask - bid
    fee = 0.07 * ask * (1.0 - ask)
    raw_edge = calibrated_probability - ask
    cost_adjusted_edge = raw_edge - fee - 0.01
    edge_decision = {
        "side": "up",
        "predicted_target": 1,
        "side_probability": calibrated_probability,
        "market_probability_observed": True,
        "executable": True,
        "trade": False,
        "reason": "edge_below_minimum",
        "ask": ask,
        "bid": bid,
        "spread": spread,
        "fee": fee,
        "slippage_buffer": 0.01,
        "raw_edge": raw_edge,
        "cost_adjusted_edge": cost_adjusted_edge,
        "min_edge": 0.02,
    }
    prediction = LivePrediction(
        prediction_id="a" * 64,
        semantic_sha256="0" * 64,
        prediction_version="live-prediction-v1",
        live_input_version="phase10-live-market-input-v1",
        condition_id="ledger-roundtrip",
        slug="btc-updown-5m-ledger-roundtrip",
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=start + timedelta(minutes=5),
        scheduled_at=scheduled,
        recorded_at=scheduled + timedelta(seconds=1),
        lateness_ms=1000,
        up_token_id="up-ledger",
        down_token_id="down-ledger",
        source_calibration_run_id="phase9-ledger",
        source_calibration_semantic_sha256="1" * 64,
        source_backtest_run_id="phase8-ledger",
        source_backtest_semantic_sha256="2" * 64,
        source_training_run_id="phase7-ledger",
        source_training_semantic_sha256="3" * 64,
        calibration_version="platt-or-identity-v1",
        edge_policy_version="selected-ask-edge-v1",
        source_feature_version="core-v1",
        source_label_version="official-outcome-v1",
        selected_offset_seconds=240,
        policy_sha256="4" * 64,
        calibration_fit={"method": "identity", "intercept": None, "coefficient": None},
        calibration_fit_sha256="5" * 64,
        edge_config={
            "fee_rate": 0.07,
            "slippage_buffer": 0.01,
            "min_edge_grid": [0.0, 0.02, 0.05],
            "min_validation_trades": 3,
            "max_spread": None,
        },
        edge_config_sha256="6" * 64,
        edge_policy="trade_threshold",
        min_edge=0.02,
        training_prior=0.48,
        raw_probability=calibrated_probability,
        calibrated_probability=calibrated_probability,
        predicted_target=1,
        predicted_side="up",
        market_probability_observed=True,
        market_probability=calibrated_probability,
        market_probability_observed_at=scheduled,
        market_probability_downloaded_at=scheduled + timedelta(milliseconds=500),
        market_probability_source="polymarket_clob",
        market_probability_dataset="prices_history",
        market_probability_request_params={
            "market": "up-ledger",
            "startTs": str(int(start.timestamp())),
            "endTs": str(int(scheduled.timestamp())),
            "fidelity": "1",
        },
        market_probability_response_sha256="7" * 64,
        up_best_bid=bid,
        up_best_ask=ask,
        up_book_cutoff_at=scheduled,
        up_book_fresh=True,
        down_best_bid=0.42,
        down_best_ask=0.43,
        down_book_cutoff_at=scheduled,
        down_book_fresh=True,
        selected_side="up",
        executable=True,
        trade=False,
        decision_reason="edge_below_minimum",
        selected_ask=ask,
        selected_bid=bid,
        selected_spread=spread,
        fee=fee,
        slippage_buffer=0.01,
        raw_edge=raw_edge,
        cost_adjusted_edge=cost_adjusted_edge,
        decision_min_edge=0.02,
        edge_decision=edge_decision,
        input_fingerprint="8" * 64,
    )
    values = asdict(prediction)
    values.pop("semantic_sha256")
    return replace(prediction, semantic_sha256=canonical_hash(values))


def _postgres_row(prediction: LivePrediction) -> dict[str, object]:
    row: dict[str, object] = asdict(prediction)
    for name in NUMERIC_FIELDS:
        value = row[name]
        if value is not None:
            row[name] = Decimal(str(value)).quantize(LEDGER_QUANTUM)
    return row


def test_prediction_semantic_hash_accepts_exact_postgres_numeric_roundtrip() -> None:
    prediction = _prediction()
    row = _postgres_row(prediction)

    assert row["raw_edge"] != Decimal(str(prediction.raw_edge))
    assert _semantic_hash_matches(row, LivePrediction) is True

    tampered = dict(row)
    tampered["raw_edge"] = row["raw_edge"] + LEDGER_QUANTUM
    assert _semantic_hash_matches(tampered, LivePrediction) is False


def test_repository_idempotency_accepts_only_exact_ledger_quantization() -> None:
    prediction = _prediction()
    values = asdict(prediction)
    row = _postgres_row(prediction)

    assert _semantically_equal(row, values) is True

    tampered = dict(row)
    tampered["raw_edge"] = row["raw_edge"] + LEDGER_QUANTUM
    assert _semantically_equal(tampered, values) is False


def test_prediction_semantic_hash_recovers_quantized_nonselected_book_quote() -> None:
    prediction = _prediction()
    original_quote = 0.0012345678901234567
    prediction = replace(prediction, down_best_bid=original_quote)
    values = asdict(prediction)
    values.pop("semantic_sha256")
    prediction = replace(prediction, semantic_sha256=canonical_hash(values))
    row = _postgres_row(prediction)

    assert row["down_best_bid"] != Decimal(str(original_quote))
    assert _semantic_hash_matches(row, LivePrediction) is True

    tampered = dict(row)
    tampered["down_best_bid"] = row["down_best_bid"] + LEDGER_QUANTUM
    assert _semantic_hash_matches(tampered, LivePrediction) is False


def test_prediction_semantic_hash_recovers_down_probability_without_double_rounding() -> None:
    prediction = _prediction()
    probability_up = 0.4211458073065157
    side_probability = 1.0 - probability_up
    ask = 0.58
    bid = 0.57
    spread = ask - bid
    fee = 0.07 * ask * (1.0 - ask)
    raw_edge = side_probability - ask
    cost_adjusted_edge = raw_edge - fee - 0.01
    decision = dict(prediction.edge_decision)
    decision.update(
        {
            "side": "down",
            "predicted_target": 0,
            "side_probability": side_probability,
            "ask": ask,
            "bid": bid,
            "spread": spread,
            "fee": fee,
            "raw_edge": raw_edge,
            "cost_adjusted_edge": cost_adjusted_edge,
        }
    )
    prediction = replace(
        prediction,
        raw_probability=probability_up,
        calibrated_probability=probability_up,
        market_probability=probability_up,
        predicted_target=0,
        predicted_side="down",
        down_best_bid=bid,
        down_best_ask=ask,
        selected_side="down",
        selected_ask=ask,
        selected_bid=bid,
        selected_spread=spread,
        fee=fee,
        raw_edge=raw_edge,
        cost_adjusted_edge=cost_adjusted_edge,
        edge_decision=decision,
    )
    values = asdict(prediction)
    values.pop("semantic_sha256")
    prediction = replace(prediction, semantic_sha256=canonical_hash(values))
    row = _postgres_row(prediction)

    assert row["raw_edge"] != Decimal(str(raw_edge))
    assert 1.0 - side_probability != probability_up
    assert _semantic_hash_matches(row, LivePrediction) is True
