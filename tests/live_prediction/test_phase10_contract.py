from __future__ import annotations

import asyncio
import builtins
import copy
import importlib
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, insert, select, update

from bp_engine.backfill.polymarket_prices import PriceHistoryPoint, PriceHistoryResponse
from bp_engine.calibration.models import CalibrationFit, EdgeConfig
from bp_engine.live_prediction.inputs import LiveBookInput, LiveMarketInput
from bp_engine.live_prediction.models import LIVE_PREDICTION_VERSION, LivePolicySpec
from bp_engine.live_prediction.repository import LivePredictionRepository
from bp_engine.storage import schema

POLICY_START = datetime(2026, 8, 24, tzinfo=UTC)
POLICY_END = datetime(2026, 8, 25, tzinfo=UTC)
POLICY_RUN_ID = "phase9-300-phase10-contract"
BACKTEST_RUN_ID = "phase8-300-phase10-contract"
TRAINING_RUN_ID = "phase7-300-phase10-contract"
MEMBERSHIP_SHA256 = "2" * 64


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    schema.metadata.create_all(engine)
    return engine


def _edge_config() -> EdgeConfig:
    return EdgeConfig(
        fee_rate=0.07,
        slippage_buffer=0.01,
        min_edge_grid=(0.0, 0.02, 0.05),
        min_validation_trades=3,
        max_spread=None,
    )


def _policy(
    *,
    horizon_seconds: int = 300,
    offset_seconds: int = 240,
    edge_policy: str = "trade_threshold",
) -> LivePolicySpec:
    return LivePolicySpec(
        source_calibration_run_id=f"phase9-{horizon_seconds}-contract",
        source_calibration_semantic_sha256="1" * 64,
        source_backtest_run_id=f"phase8-{horizon_seconds}-contract",
        source_backtest_semantic_sha256="2" * 64,
        source_training_run_id=f"phase7-{horizon_seconds}-contract",
        source_training_semantic_sha256="3" * 64,
        calibration_version="platt-or-identity-v1",
        edge_policy_version="selected-ask-edge-v1",
        source_feature_version="core-v1",
        label_version="official-outcome-v1",
        horizon_seconds=horizon_seconds,
        selected_offset_seconds=offset_seconds,
        calibration_fit=CalibrationFit(
            method="identity",
            intercept=None,
            coefficient=None,
        ),
        edge_config=_edge_config(),
        edge_policy=edge_policy,
        min_edge=0.02 if edge_policy == "trade_threshold" else None,
        training_prior=0.48,
        policy_sha256="4" * 64,
    )


def _book(
    *,
    asset_id: str,
    condition_id: str,
    scheduled_at: datetime,
    bid: float,
    ask: float,
) -> LiveBookInput:
    return LiveBookInput(
        asset_id=asset_id,
        state_key=f"polymarket/market/{condition_id}/{asset_id}",
        bucket_at=scheduled_at,
        last_event_at=scheduled_at,
        fresh=True,
        age_seconds=0.0,
        state={"best_bid": bid, "best_ask": ask},
    )


def _live_input(
    *,
    condition_id: str,
    horizon_seconds: int,
    offset_seconds: int,
    probability: float = 0.72,
) -> LiveMarketInput:
    start = datetime(2026, 8, 26, 16, 0, tzinfo=UTC)
    end = start + timedelta(seconds=horizon_seconds)
    scheduled = start + timedelta(seconds=offset_seconds)
    up_token_id = f"up-{condition_id}"
    down_token_id = f"down-{condition_id}"
    return LiveMarketInput(
        condition_id=condition_id,
        up_token_id=up_token_id,
        down_token_id=down_token_id,
        market_start_at=start,
        market_end_at=end,
        scheduled_at=scheduled,
        downloaded_at=scheduled + timedelta(seconds=1),
        price_source="polymarket_clob",
        price_dataset="prices_history",
        price_request_params={
            "market": up_token_id,
            "startTs": str(int(start.timestamp())),
            "endTs": str(int(scheduled.timestamp())),
            "fidelity": "1",
        },
        price_response_sha256="5" * 64,
        price_response_payload={"history": []},
        market_probability_observed=True,
        market_probability=probability,
        market_probability_observed_at=scheduled,
        up_book=_book(
            asset_id=up_token_id,
            condition_id=condition_id,
            scheduled_at=scheduled,
            bid=0.58,
            ask=0.60,
        ),
        down_book=_book(
            asset_id=down_token_id,
            condition_id=condition_id,
            scheduled_at=scheduled,
            bid=0.40,
            ask=0.42,
        ),
        predictors={
            "pm_up_price": probability,
            "pm_up_best_bid": 0.58,
            "pm_up_best_ask": 0.60,
            "pm_down_best_bid": 0.40,
            "pm_down_best_ask": 0.42,
            "missing__pm_up_book_missing": 0.0,
            "missing__pm_up_book_stale": 0.0,
            "missing__pm_down_book_missing": 0.0,
            "missing__pm_down_book_stale": 0.0,
        },
        input_fingerprint="6" * 64,
    )


def _prediction(policy: LivePolicySpec, live_input: LiveMarketInput):
    module = importlib.import_module("bp_engine.live_prediction.predictor")
    return module.build_live_prediction(
        policy,
        live_input,
        condition_id=live_input.condition_id,
        slug=f"btc-updown-{live_input.condition_id}",
        horizon_seconds=policy.horizon_seconds,
        market_start_at=live_input.market_start_at,
        market_end_at=live_input.market_end_at,
        up_token_id=live_input.up_token_id,
        down_token_id=live_input.down_token_id,
        recorded_at=live_input.scheduled_at + timedelta(seconds=2),
    )


def _market_values(
    condition_id: str,
    *,
    start_at: datetime,
    horizon_seconds: int = 300,
) -> dict[str, object]:
    return {
        "gamma_market_id": f"gamma-{condition_id}",
        "event_id": f"event-{condition_id}",
        "condition_id": condition_id,
        "slug": f"btc-updown-{condition_id}",
        "question": "Will BTC be up?",
        "horizon_seconds": horizon_seconds,
        "start_at": start_at,
        "end_at": start_at + timedelta(seconds=horizon_seconds),
        "up_token_id": f"up-{condition_id}",
        "down_token_id": f"down-{condition_id}",
        "resolution_source": "official-rules",
        "rules_text": "Up if final reference exceeds start reference.",
        "rules_hash": f"rules-{condition_id}",
        "active": True,
        "closed": False,
        "accepting_orders": True,
        "resolved_outcome": None,
        "discovered_at": start_at - timedelta(minutes=5),
        "updated_at": start_at,
    }


def _insert_state(
    connection,
    *,
    condition_id: str,
    asset_id: str,
    bucket_at: datetime,
    last_event_at: datetime,
    best_bid: str,
    best_ask: str,
) -> None:
    connection.execute(
        insert(schema.market_state_1s).values(
            bucket_at=bucket_at,
            state_key=f"polymarket/market/{condition_id}/{asset_id}",
            source="polymarket",
            stream="market",
            instrument=condition_id,
            market_id=condition_id,
            asset_id=asset_id,
            last_event_at=last_event_at,
            state={
                "best_bid": best_bid,
                "best_ask": best_ask,
                "bid_depth": "100",
                "ask_depth": "120",
            },
        )
    )


def _backtest_report() -> dict[str, object]:
    return {
        "run_id": BACKTEST_RUN_ID,
        "backtest_version": "walk-forward-v1",
        "source_training_run_id": TRAINING_RUN_ID,
        "source_training_semantic_sha256": "a" * 64,
        "dataset_version": "supervised-core-v1",
        "feature_version": "core-v1",
        "label_version": "official-outcome-v1",
        "horizon_seconds": 300,
        "start": "2026-08-24T00:00:00Z",
        "end": "2026-08-25T00:00:00Z",
        "dataset_sha256": "c" * 64,
        "config": {"train_duration_seconds": 28800.0},
        "config_sha256": "e" * 64,
        "plan_sha256": "6" * 64,
        "fold_membership_sha256": [MEMBERSHIP_SHA256],
        "folds": [],
        "final_holdout": {
            "membership_sha256": MEMBERSHIP_SHA256,
            "train_condition_ids": ["train-0", "train-1", "train-2", "train-3"],
            "validation_condition_ids": ["val-0", "val-1"],
            "holdout_condition_ids": ["holdout-0", "holdout-1"],
            "selected_offset_seconds": 240,
        },
        "semantic_sha256": "b" * 64,
    }


def _calibration_report() -> dict[str, object]:
    return {
        "run_id": POLICY_RUN_ID,
        "calibration_version": "platt-or-identity-v1",
        "edge_policy_version": "selected-ask-edge-v1",
        "source_backtest_run_id": BACKTEST_RUN_ID,
        "source_backtest_version": "walk-forward-v1",
        "source_backtest_semantic_sha256": "b" * 64,
        "source_training_run_id": TRAINING_RUN_ID,
        "source_training_semantic_sha256": "a" * 64,
        "dataset_version": "supervised-core-v1",
        "feature_version": "core-v1",
        "label_version": "official-outcome-v1",
        "horizon_seconds": 300,
        "start": "2026-08-24T00:00:00Z",
        "end": "2026-08-25T00:00:00Z",
        "dataset_sha256": "c" * 64,
        "config": {
            "fee_rate": 0.07,
            "slippage_buffer": 0.01,
            "min_edge_grid": [0.0, 0.01, 0.02, 0.05],
            "min_validation_trades": 3,
            "max_spread": None,
        },
        "config_sha256": "d" * 64,
        "source_backtest_config_sha256": "e" * 64,
        "source_plan_sha256": "f" * 64,
        "source_fold_membership_sha256": [MEMBERSHIP_SHA256],
        "folds": [],
        "aggregate_oos": {"metrics": {"accuracy": 0.9}},
        "final_holdout": {
            "membership_sha256": MEMBERSHIP_SHA256,
            "train_condition_ids": ["train-0", "train-1", "train-2", "train-3"],
            "validation_condition_ids": ["val-0", "val-1"],
            "holdout_condition_ids": ["holdout-0", "holdout-1"],
            "selected_offset_seconds": 240,
            "calibration_selection_fit_partition": "train",
            "calibration_selection_partition": "validation",
            "edge_selection_partition": "validation",
            "evaluation_partition": "holdout",
            "calibration_selection": {
                "method": "identity",
                "fit": {
                    "method": "identity",
                    "intercept": None,
                    "coefficient": None,
                },
                "validation_metrics": {"accuracy": 1.0},
                "candidates": [],
            },
            "edge_policy_selection": {
                "policy": "trade_threshold",
                "min_edge": 0.05,
                "validation_metrics": {"trade_count": 3},
                "candidates": [],
            },
            "raw_metrics": {"accuracy": 0.5},
            "calibrated_metrics": {"accuracy": 0.5},
            "edge_metrics": {"realized_pnl_after_assumed_costs": -99.0},
            "predictions": [
                {
                    "condition_id": "holdout-0",
                    "target": 1,
                    "raw_probability": 0.99,
                }
            ],
        },
        "semantic_sha256": "9" * 64,
    }


def _seed_policy_source(connection) -> None:
    connection.execute(
        insert(schema.model_training_runs).values(
            run_id=TRAINING_RUN_ID,
            dataset_version="supervised-core-v1",
            split_version="chronological-market-v1",
            feature_version="core-v1",
            label_version="official-outcome-v1",
            horizon_seconds=300,
            requested_start=POLICY_START,
            requested_end=POLICY_END,
            dataset_sha256="3" * 64,
            split_sha256="4" * 64,
            predictor_names=["pm_up_price"],
            dropped_all_missing=[],
            model_configs={
                "market_price": {
                    "predictor": "pm_up_price",
                    "missing_fallback": "training_prior",
                    "clip_epsilon": 1e-6,
                }
            },
            validation_champion="market_price",
            report={},
            artifact_manifest={},
            semantic_sha256="a" * 64,
            created_at=POLICY_END,
        )
    )
    backtest_report = _backtest_report()
    connection.execute(
        insert(schema.backtest_runs).values(
            run_id=BACKTEST_RUN_ID,
            backtest_version="walk-forward-v1",
            source_training_run_id=TRAINING_RUN_ID,
            source_training_semantic_sha256="a" * 64,
            dataset_version="supervised-core-v1",
            feature_version="core-v1",
            label_version="official-outcome-v1",
            horizon_seconds=300,
            requested_start=POLICY_START,
            requested_end=POLICY_END,
            dataset_sha256="c" * 64,
            config=backtest_report["config"],
            config_sha256="e" * 64,
            plan_sha256="6" * 64,
            fold_membership_sha256=[MEMBERSHIP_SHA256],
            report=backtest_report,
            semantic_sha256="b" * 64,
            created_at=POLICY_END,
        )
    )
    calibration_report = _calibration_report()
    connection.execute(
        insert(schema.calibration_edge_runs).values(
            run_id=POLICY_RUN_ID,
            calibration_version="platt-or-identity-v1",
            edge_policy_version="selected-ask-edge-v1",
            source_backtest_run_id=BACKTEST_RUN_ID,
            source_backtest_semantic_sha256="b" * 64,
            source_training_run_id=TRAINING_RUN_ID,
            source_training_semantic_sha256="a" * 64,
            dataset_version="supervised-core-v1",
            feature_version="core-v1",
            label_version="official-outcome-v1",
            horizon_seconds=300,
            requested_start=POLICY_START,
            requested_end=POLICY_END,
            dataset_sha256="c" * 64,
            config=calibration_report["config"],
            config_sha256="d" * 64,
            source_plan_sha256="f" * 64,
            source_fold_membership_sha256=[MEMBERSHIP_SHA256],
            report=calibration_report,
            semantic_sha256="9" * 64,
            created_at=POLICY_END,
        )
    )
    for index, outcome in enumerate(("Down", "Down", "Up", "Up")):
        connection.execute(
            insert(schema.market_labels).values(
                condition_id=f"train-{index}",
                gamma_market_id=f"gamma-train-{index}",
                slug=f"train-{index}",
                horizon_seconds=300,
                market_start_at=POLICY_START,
                market_end_at=POLICY_START + timedelta(hours=1),
                official_outcome=outcome,
                start_reference=None,
                end_reference=None,
                resolution_source="official-rules",
                rules_hash="r" * 64,
                label_source="polymarket_gamma_snapshot",
                label_version="official-outcome-v1",
                source_snapshot_sha256=str(index) * 64,
                source_observed_at=POLICY_START + timedelta(hours=1, minutes=1),
                generated_at=POLICY_START + timedelta(hours=1, minutes=2),
            )
        )


def test_prediction_is_pre_label_and_later_evaluation_never_rewrites_parent() -> None:
    evaluation_module = importlib.import_module("bp_engine.live_prediction.evaluation")
    engine = _engine()
    policy = _policy()
    live_input = _live_input(
        condition_id="phase10-contract-ledger",
        horizon_seconds=300,
        offset_seconds=240,
    )
    prediction = _prediction(policy, live_input)

    with engine.begin() as connection:
        stored = LivePredictionRepository().store(connection, prediction)
        assert stored.created is True
        assert connection.execute(
            select(schema.market_labels).where(
                schema.market_labels.c.condition_id == prediction.condition_id
            )
        ).first() is None
        assert connection.execute(
            select(schema.live_prediction_evaluations).where(
                schema.live_prediction_evaluations.c.prediction_id
                == prediction.prediction_id
            )
        ).first() is None
        before = dict(
            LivePredictionRepository().get_by_id(connection, prediction.prediction_id)
        )

        label_observed_at = prediction.market_end_at + timedelta(seconds=20)
        connection.execute(
            insert(schema.market_labels).values(
                condition_id=prediction.condition_id,
                gamma_market_id="gamma-phase10-contract-ledger",
                slug=prediction.slug,
                horizon_seconds=prediction.horizon_seconds,
                market_start_at=prediction.market_start_at,
                market_end_at=prediction.market_end_at,
                official_outcome="Up",
                start_reference=None,
                end_reference=None,
                resolution_source="official-rules",
                rules_hash="rules-contract-ledger",
                label_source="polymarket_gamma_snapshot",
                label_version="official-outcome-v1",
                source_snapshot_sha256="a" * 64,
                source_observed_at=label_observed_at,
                generated_at=label_observed_at + timedelta(seconds=5),
            )
        )
        result = evaluation_module.append_available_evaluations(
            connection,
            evaluated_at=label_observed_at + timedelta(seconds=5),
        )
        after = dict(
            LivePredictionRepository().get_by_id(connection, prediction.prediction_id)
        )
        evaluation = connection.execute(
            select(schema.live_prediction_evaluations).where(
                schema.live_prediction_evaluations.c.prediction_id
                == prediction.prediction_id
            )
        ).mappings().one()

    assert result.created == 1
    assert before == after
    assert after["semantic_sha256"] == prediction.semantic_sha256
    assert after["source_calibration_semantic_sha256"] == "1" * 64
    assert after["source_backtest_semantic_sha256"] == "2" * 64
    assert after["source_training_semantic_sha256"] == "3" * 64
    assert after["policy_sha256"] == "4" * 64
    assert after["market_probability_response_sha256"] == "5" * 64
    assert after["input_fingerprint"] == "6" * 64
    assert prediction.trade is True
    assert evaluation["official_outcome"] == "Up"


def test_holdout_metrics_and_predictions_cannot_change_extracted_live_policy() -> None:
    policy_module = importlib.import_module("bp_engine.live_prediction.policy")
    engine = _engine()
    with engine.begin() as connection:
        _seed_policy_source(connection)
        before = policy_module.load_live_policy(connection, POLICY_RUN_ID)
        row = connection.execute(
            select(schema.calibration_edge_runs).where(
                schema.calibration_edge_runs.c.run_id == POLICY_RUN_ID
            )
        ).mappings().one()
        report = copy.deepcopy(row["report"])
        final_holdout = report["final_holdout"]
        final_holdout["raw_metrics"] = {"accuracy": 0.0, "log_loss": 99.0}
        final_holdout["calibrated_metrics"] = {"accuracy": 1.0, "log_loss": 0.0}
        final_holdout["edge_metrics"] = {"realized_pnl_after_assumed_costs": 999.0}
        final_holdout["predictions"] = [
            {
                "condition_id": "holdout-1",
                "target": 0,
                "raw_probability": 0.01,
            }
        ]
        connection.execute(
            update(schema.calibration_edge_runs)
            .where(schema.calibration_edge_runs.c.run_id == POLICY_RUN_ID)
            .values(report=report)
        )
        after = policy_module.load_live_policy(connection, POLICY_RUN_ID)

    assert before == after


def test_post_scheduled_price_and_state_observations_cannot_enter_prediction() -> None:
    inputs_module = importlib.import_module("bp_engine.live_prediction.inputs")
    engine = _engine()
    start = datetime(2026, 8, 26, 18, 0, tzinfo=UTC)
    end = start + timedelta(minutes=5)
    scheduled = start + timedelta(minutes=4)
    condition_id = "phase10-contract-input"
    up_token_id = f"up-{condition_id}"
    down_token_id = f"down-{condition_id}"
    points = (
        PriceHistoryPoint(scheduled, Decimal("0.61")),
        PriceHistoryPoint(scheduled + timedelta(seconds=1), Decimal("0.99")),
    )
    response = PriceHistoryResponse(
        points=points,
        request_params={
            "market": up_token_id,
            "startTs": str(int(start.timestamp())),
            "endTs": str(int(scheduled.timestamp())),
            "fidelity": "1",
        },
        raw_payload={
            "history": [
                {"t": int(point.observed_at.timestamp()), "p": str(point.price)}
                for point in points
            ]
        },
    )

    class Client:
        async def get_history(self, asset_id, *, start, end, fidelity_minutes):
            assert asset_id == up_token_id
            assert end == scheduled
            assert fidelity_minutes == 1
            return response

    with engine.begin() as connection:
        _insert_state(
            connection,
            condition_id=condition_id,
            asset_id=up_token_id,
            bucket_at=scheduled - timedelta(seconds=1),
            last_event_at=scheduled - timedelta(seconds=1),
            best_bid="0.57",
            best_ask="0.59",
        )
        _insert_state(
            connection,
            condition_id=condition_id,
            asset_id=up_token_id,
            bucket_at=scheduled,
            last_event_at=scheduled + timedelta(seconds=1),
            best_bid="0.01",
            best_ask="0.99",
        )
        _insert_state(
            connection,
            condition_id=condition_id,
            asset_id=down_token_id,
            bucket_at=scheduled,
            last_event_at=scheduled,
            best_bid="0.40",
            best_ask="0.42",
        )
        live_input = asyncio.run(
            inputs_module.observe_live_input(
                connection,
                Client(),
                condition_id=condition_id,
                up_token_id=up_token_id,
                down_token_id=down_token_id,
                market_start_at=start,
                market_end_at=end,
                scheduled_at=scheduled,
                clock=lambda: scheduled + timedelta(seconds=1),
            )
        )

    assert live_input.market_probability == pytest.approx(0.61)
    assert live_input.market_probability_observed_at == scheduled
    assert live_input.up_book is not None
    assert live_input.up_book.last_event_at == scheduled - timedelta(seconds=1)
    assert live_input.predictors["pm_up_best_ask"] == pytest.approx(0.59)


def test_deadline_miss_remains_absent_after_resolution_and_restart() -> None:
    service_module = importlib.import_module("bp_engine.live_prediction.service")
    engine = _engine()
    policy = _policy()
    start = datetime(2026, 8, 26, 20, 0, tzinfo=UTC)
    scheduled = start + timedelta(seconds=policy.selected_offset_seconds)
    current = {"value": scheduled}

    with engine.begin() as connection:
        connection.execute(
            insert(schema.polymarket_markets).values(
                **_market_values(
                    "phase10-contract-miss",
                    start_at=start,
                )
            )
        )

    async def observer(connection, client, **kwargs):
        current["value"] = scheduled + timedelta(seconds=11)
        return object()

    service = service_module.LivePredictionService(
        engine=engine,
        policies={300: policy},
        client=object(),
        observer=observer,
        clock=lambda: current["value"],
    )
    first = asyncio.run(service.run_once())

    current["value"] = start + timedelta(minutes=6)
    with engine.begin() as connection:
        connection.execute(
            update(schema.polymarket_markets)
            .where(
                schema.polymarket_markets.c.condition_id
                == "phase10-contract-miss"
            )
            .values(closed=True, active=False, resolved_outcome="Up")
        )
    second = asyncio.run(service.run_once())

    with engine.begin() as connection:
        predictions = connection.execute(
            select(schema.live_predictions).where(
                schema.live_predictions.c.condition_id == "phase10-contract-miss"
            )
        ).all()

    assert first.missed_predictions == 1
    assert second.due_markets == 0
    assert predictions == []


def test_15m_no_trade_stays_data_only_and_runtime_imports_no_order_path(
    monkeypatch,
) -> None:
    policy = _policy(
        horizon_seconds=900,
        offset_seconds=840,
        edge_policy="no_trade",
    )
    live_input = _live_input(
        condition_id="phase10-contract-15m-no-trade",
        horizon_seconds=900,
        offset_seconds=840,
        probability=0.30,
    )
    prediction = _prediction(policy, live_input)

    assert prediction.horizon_seconds == 900
    assert prediction.executable is True
    assert prediction.trade is False
    assert prediction.decision_reason == "policy_no_trade"
    assert prediction.decision_min_edge is None

    for name in (
        "bp_engine.live_prediction.predictor",
        "bp_engine.live_prediction.service",
    ):
        sys.modules.pop(name, None)
    original_import = builtins.__import__
    forbidden = {"order", "orders", "wallet", "signing", "auth", "execution"}
    seen: list[str] = []

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        parts = set(name.lower().split("."))
        if name.startswith("bp_engine") and parts.intersection(forbidden):
            seen.append(name)
            raise AssertionError(f"forbidden research-runtime import: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    importlib.import_module("bp_engine.live_prediction.predictor")
    importlib.import_module("bp_engine.live_prediction.service")
    assert seen == []
    assert prediction.prediction_version == LIVE_PREDICTION_VERSION
