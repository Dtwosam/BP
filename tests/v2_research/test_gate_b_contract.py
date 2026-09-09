from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, insert

from bp_engine.storage import schema
from bp_engine.v2_research.config import (
    FROZEN_COVERAGE_INPUT_SHA256,
    FROZEN_FRESHNESS_CANDIDATES_SECONDS,
    FROZEN_INCLUDE_NO_TRADE,
    V2_DATASET_VERSION,
)
from bp_engine.v2_research.models import GateBPlanConfig, GateBResearchConfig
from bp_engine.v2_research.plan import build_gate_b_plan
from bp_engine.v2_research.policy import edge_decision_v2
from bp_engine.v2_research.service import prepare_gate_b

ROOT_START = datetime(2026, 9, 2, 12, 20, tzinfo=UTC)
OFFSETS = (60, 120, 180, 240)


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    schema.metadata.create_all(engine)
    return engine


def _feature(condition_index: int, offset: int) -> dict[str, object]:
    market_start = ROOT_START + timedelta(minutes=5 * condition_index)
    feature_at = market_start + timedelta(seconds=offset)
    up_price = 0.72 if condition_index % 2 else 0.28
    return {
        "condition_id": f"condition-{condition_index:03d}",
        "slug": f"btc-updown-5m-{condition_index:03d}",
        "horizon_seconds": 300,
        "market_start_at": market_start,
        "market_end_at": market_start + timedelta(minutes=5),
        "feature_at": feature_at,
        "feature_offset_seconds": offset,
        "feature_version": "core-v2-last-trade",
        "features": {
            "seconds_elapsed": offset,
            "seconds_remaining": 300 - offset,
            "fraction_elapsed": offset / 300,
            "horizon_seconds": 300,
            "pm_up_last_trade_price": up_price,
            "pm_up_last_trade_source_age_s": 1.2,
            "pm_up_last_trade_availability_age_s": 0.8,
            "pm_down_last_trade_price": 1.0 - up_price,
            "pm_down_last_trade_source_age_s": 1.4,
            "pm_down_last_trade_availability_age_s": 0.9,
            "pm_up_best_bid": max(up_price - 0.03, 0.01),
            "pm_up_best_ask": min(up_price + 0.01, 0.99),
            "pm_up_mid": up_price - 0.01,
            "pm_up_spread": 0.04,
            "pm_up_bid_depth": 10.0,
            "pm_up_ask_depth": 10.0,
            "pm_up_book_imbalance": 0.0,
            "pm_down_best_bid": max((1.0 - up_price) - 0.03, 0.01),
            "pm_down_best_ask": min((1.0 - up_price) + 0.01, 0.99),
            "pm_down_mid": (1.0 - up_price) - 0.01,
            "pm_down_spread": 0.04,
            "pm_down_bid_depth": 10.0,
            "pm_down_ask_depth": 10.0,
            "pm_down_book_imbalance": 0.0,
        },
        "missing_flags": {
            "pm_up_last_trade_missing": False,
            "pm_down_last_trade_missing": False,
            "pm_up_book_missing": False,
            "pm_up_book_stale": False,
            "pm_down_book_missing": False,
            "pm_down_book_stale": False,
        },
        "source_cutoffs": {
            "pm_up_last_trade_source": (feature_at - timedelta(seconds=1.2)).isoformat(),
            "pm_up_last_trade_received": (feature_at - timedelta(seconds=0.8)).isoformat(),
            "pm_down_last_trade_source": (feature_at - timedelta(seconds=1.4)).isoformat(),
            "pm_down_last_trade_received": (feature_at - timedelta(seconds=0.9)).isoformat(),
            "pm_up_book_state": (feature_at - timedelta(seconds=0.5)).isoformat(),
            "pm_down_book_state": (feature_at - timedelta(seconds=0.5)).isoformat(),
        },
        "input_fingerprint": f"{condition_index % 10}" * 64,
        "feature_hash": f"{(condition_index + offset) % 10}" * 64,
        "generated_at": market_start + timedelta(minutes=6),
    }


def _label(condition_index: int) -> dict[str, object]:
    market_start = ROOT_START + timedelta(minutes=5 * condition_index)
    outcome = "Up" if condition_index % 2 else "Down"
    return {
        "condition_id": f"condition-{condition_index:03d}",
        "gamma_market_id": f"gamma-{condition_index:03d}",
        "slug": f"btc-updown-5m-{condition_index:03d}",
        "horizon_seconds": 300,
        "market_start_at": market_start,
        "market_end_at": market_start + timedelta(minutes=5),
        "official_outcome": outcome,
        "start_reference": None,
        "end_reference": None,
        "resolution_source": "chainlink",
        "rules_hash": f"sha256:rules-{condition_index}",
        "label_source": "polymarket_gamma_snapshot",
        "label_version": "official-outcome-v1",
        "source_snapshot_sha256": f"sha256:snapshot-{condition_index}",
        "source_observed_at": market_start + timedelta(minutes=6),
        "generated_at": market_start + timedelta(minutes=7),
    }


def _plan_config() -> GateBPlanConfig:
    return GateBPlanConfig(
        min_initial_train_markets=4,
        validation_markets=2,
        test_markets=2,
        final_holdout_markets=2,
        embargo_markets=1,
    )


def test_preregistration_constants_match_immutable_evidence() -> None:
    path = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "docs"
        / "evidence"
        / "phase-14-v2-freshness-preregistration-20260909.json"
    )
    evidence = json.loads(path.read_text(encoding="utf-8"))
    prereg = evidence["freshness_preregistration"]

    assert FROZEN_COVERAGE_INPUT_SHA256 == evidence["coverage_input_sha256"]
    assert list(FROZEN_FRESHNESS_CANDIDATES_SECONDS) == prereg[
        "max_last_trade_age_seconds_candidates"
    ]
    assert FROZEN_INCLUDE_NO_TRADE is prereg["include_no_trade"]
    assert max(FROZEN_FRESHNESS_CANDIDATES_SECONDS) <= 10
    assert V2_DATASET_VERSION == "supervised-core-v2-last-trade-v1"


def test_gate_b_plan_is_feature_only_deterministic_and_holds_out_latest_markets() -> None:
    engine = _engine()
    rows = [_feature(index, offset) for index in range(16) for offset in OFFSETS]
    with engine.begin() as connection:
        connection.execute(insert(schema.market_features), rows)
        first = build_gate_b_plan(connection, _plan_config())
        second = build_gate_b_plan(connection, _plan_config())

    assert first == second
    assert first["feature_version"] == "core-v2-last-trade"
    assert first["market_count"] == 16
    assert len(first["folds"]) == 3
    assert first["final"]["holdout_condition_ids"] == [
        "condition-014",
        "condition-015",
    ]
    assert first["coverage_input_sha256"] == FROZEN_COVERAGE_INPUT_SHA256
    assert first["freshness_candidates_seconds"] == [1, 2, 5, 10]
    assert first["include_no_trade"] is True
    assert first["labels_read"] is False


def test_prepare_gate_b_does_not_require_final_holdout_labels() -> None:
    engine = _engine()
    rows = [_feature(index, offset) for index in range(16) for offset in OFFSETS]
    with engine.begin() as connection:
        connection.execute(insert(schema.market_features), rows)
        # Intentionally omit labels for the two final-holdout markets.
        connection.execute(insert(schema.market_labels), [_label(index) for index in range(14)])
        plan = build_gate_b_plan(connection, _plan_config())
        report = prepare_gate_b(
            connection,
            plan=plan,
            config=GateBResearchConfig(
                fee_rate=0.0,
                slippage_buffer=0.0,
                min_edge_grid=(0.0,),
                min_validation_trades=1,
                min_train_eligible_markets=2,
                min_validation_eligible_markets=1,
            ),
        )

    assert report["dataset_version"] == V2_DATASET_VERSION
    assert report["holdout_labels_read"] is False
    assert report["final"]["holdout_condition_ids"] == [
        "condition-014",
        "condition-015",
    ]
    assert "holdout_metrics" not in report["final"]
    assert report["coverage_input_sha256"] == FROZEN_COVERAGE_INPUT_SHA256
    assert report["freshness_candidates_seconds"] == [1, 2, 5, 10]


def test_v2_policy_stale_or_missing_last_trade_is_explicit_no_trade() -> None:
    row = __import__("bp_engine.modeling.models", fromlist=["SupervisedRow"]).SupervisedRow(
        condition_id="condition-x",
        slug="btc-updown-5m-x",
        horizon_seconds=300,
        market_start_at=ROOT_START,
        market_end_at=ROOT_START + timedelta(minutes=5),
        feature_at=ROOT_START + timedelta(seconds=60),
        feature_offset_seconds=60,
        predictors={
            "pm_up_last_trade_price": 0.70,
            "pm_up_last_trade_availability_age_s": 5.1,
            "missing__pm_up_last_trade_missing": 0.0,
            "pm_up_best_bid": 0.64,
            "pm_up_best_ask": 0.66,
            "missing__pm_up_book_missing": 0.0,
            "missing__pm_up_book_stale": 0.0,
            "pm_down_best_bid": 0.34,
            "pm_down_best_ask": 0.36,
            "missing__pm_down_book_missing": 0.0,
            "missing__pm_down_book_stale": 0.0,
        },
        target=1,
        feature_hash="a" * 64,
        input_fingerprint="b" * 64,
    )

    stale = edge_decision_v2(
        row,
        calibrated_probability_up=0.70,
        max_last_trade_age_seconds=5,
        fee_rate=0.07,
        slippage_buffer=0.01,
        min_edge=0.0,
    )
    assert stale.trade is False
    assert stale.executable is False
    assert stale.reason == "last_trade_stale"

    missing_predictors = dict(row.predictors)
    missing_predictors["pm_up_last_trade_price"] = None
    missing_predictors["missing__pm_up_last_trade_missing"] = 1.0
    missing = row.__class__(**{**row.__dict__, "predictors": missing_predictors})
    decision = edge_decision_v2(
        missing,
        calibrated_probability_up=None,
        max_last_trade_age_seconds=10,
        fee_rate=0.07,
        slippage_buffer=0.01,
        min_edge=0.0,
    )
    assert decision.trade is False
    assert decision.executable is False
    assert decision.reason == "last_trade_missing"
