from __future__ import annotations

import asyncio
import builtins
import importlib
import logging
import sys
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, insert, select

from bp_engine.calibration.models import CalibrationFit, EdgeConfig
from bp_engine.config import Settings, TradingMode
from bp_engine.live_prediction.models import (
    LIVE_INPUT_VERSION,
    LIVE_PREDICTION_VERSION,
    LivePolicySpec,
    LivePrediction,
)
from bp_engine.live_prediction.repository import LivePredictionRepository
from bp_engine.storage import schema


def _module():
    return importlib.import_module("bp_engine.live_prediction.service")


def _policy(*, horizon_seconds: int = 300, offset: int = 240) -> LivePolicySpec:
    return LivePolicySpec(
        source_calibration_run_id=f"phase9-{horizon_seconds}",
        source_calibration_semantic_sha256="1" * 64,
        source_backtest_run_id=f"phase8-{horizon_seconds}",
        source_backtest_semantic_sha256="2" * 64,
        source_training_run_id=f"phase7-{horizon_seconds}",
        source_training_semantic_sha256="3" * 64,
        calibration_version="platt-or-identity-v1",
        edge_policy_version="selected-ask-edge-v1",
        source_feature_version="core-v1",
        label_version="official-outcome-v1",
        horizon_seconds=horizon_seconds,
        selected_offset_seconds=offset,
        calibration_fit=CalibrationFit(
            method="identity",
            intercept=None,
            coefficient=None,
        ),
        edge_config=EdgeConfig(
            fee_rate=0.07,
            slippage_buffer=0.01,
            min_edge_grid=(0.02,),
            min_validation_trades=1,
        ),
        edge_policy="trade_threshold",
        min_edge=0.02,
        training_prior=0.48,
        policy_sha256="4" * 64,
    )


def _market_values(
    condition_id: str,
    *,
    start_at: datetime,
    horizon_seconds: int = 300,
    active: bool = True,
    closed: bool = False,
    resolved_outcome: str | None = None,
) -> dict[str, object]:
    return {
        "gamma_market_id": f"gamma-{condition_id}",
        "event_id": f"event-{condition_id}",
        "condition_id": condition_id,
        "slug": f"btc-updown-{condition_id}",
        "question": f"Will BTC be up for {condition_id}?",
        "horizon_seconds": horizon_seconds,
        "start_at": start_at,
        "end_at": start_at + timedelta(seconds=horizon_seconds),
        "up_token_id": f"up-{condition_id}",
        "down_token_id": f"down-{condition_id}",
        "resolution_source": "official-rules",
        "rules_text": "Up if final reference exceeds start reference.",
        "rules_hash": f"rules-{condition_id}",
        "active": active,
        "closed": closed,
        "accepting_orders": True,
        "resolved_outcome": resolved_outcome,
        "discovered_at": start_at - timedelta(minutes=5),
        "updated_at": start_at,
    }


def _prediction(
    condition_id: str,
    *,
    start_at: datetime,
    recorded_at: datetime,
    policy: LivePolicySpec,
) -> LivePrediction:
    scheduled_at = start_at + timedelta(seconds=policy.selected_offset_seconds)
    return LivePrediction(
        prediction_id=(condition_id.encode().hex() + "0" * 64)[:64],
        semantic_sha256="a" * 64,
        prediction_version=LIVE_PREDICTION_VERSION,
        live_input_version=LIVE_INPUT_VERSION,
        condition_id=condition_id,
        slug=f"btc-updown-{condition_id}",
        horizon_seconds=policy.horizon_seconds,
        market_start_at=start_at,
        market_end_at=start_at + timedelta(seconds=policy.horizon_seconds),
        scheduled_at=scheduled_at,
        recorded_at=recorded_at,
        lateness_ms=int((recorded_at - scheduled_at).total_seconds() * 1000),
        up_token_id=f"up-{condition_id}",
        down_token_id=f"down-{condition_id}",
        source_calibration_run_id=policy.source_calibration_run_id,
        source_calibration_semantic_sha256=policy.source_calibration_semantic_sha256,
        source_backtest_run_id=policy.source_backtest_run_id,
        source_backtest_semantic_sha256=policy.source_backtest_semantic_sha256,
        source_training_run_id=policy.source_training_run_id,
        source_training_semantic_sha256=policy.source_training_semantic_sha256,
        calibration_version=policy.calibration_version,
        edge_policy_version=policy.edge_policy_version,
        source_feature_version=policy.source_feature_version,
        source_label_version=policy.label_version,
        selected_offset_seconds=policy.selected_offset_seconds,
        policy_sha256=policy.policy_sha256,
        calibration_fit={"method": "identity", "intercept": None, "coefficient": None},
        calibration_fit_sha256="b" * 64,
        edge_config={
            "fee_rate": policy.edge_config.fee_rate,
            "slippage_buffer": policy.edge_config.slippage_buffer,
            "min_edge_grid": list(policy.edge_config.min_edge_grid),
            "min_validation_trades": policy.edge_config.min_validation_trades,
            "max_spread": policy.edge_config.max_spread,
        },
        edge_config_sha256="c" * 64,
        edge_policy=policy.edge_policy,
        min_edge=policy.min_edge,
        training_prior=policy.training_prior,
        raw_probability=0.62,
        calibrated_probability=0.62,
        predicted_target=1,
        predicted_side="up",
        market_probability_observed=True,
        market_probability=0.62,
        market_probability_observed_at=scheduled_at,
        market_probability_downloaded_at=scheduled_at,
        market_probability_source="polymarket_clob",
        market_probability_dataset="prices_history",
        market_probability_request_params={"market": f"up-{condition_id}"},
        market_probability_response_sha256="d" * 64,
        up_best_bid=0.54,
        up_best_ask=0.56,
        up_book_cutoff_at=scheduled_at,
        up_book_fresh=True,
        down_best_bid=0.44,
        down_best_ask=0.46,
        down_book_cutoff_at=scheduled_at,
        down_book_fresh=True,
        selected_side="up",
        executable=True,
        trade=True,
        decision_reason="trade",
        selected_ask=0.56,
        selected_bid=0.54,
        selected_spread=0.02,
        fee=0.017248,
        slippage_buffer=0.01,
        raw_edge=0.06,
        cost_adjusted_edge=0.032752,
        decision_min_edge=policy.min_edge,
        edge_decision={"side": "up", "trade": True},
        input_fingerprint="e" * 64,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"mode": TradingMode.PAPER},
        {"mode": TradingMode.LIVE},
        {"live_trading_enabled": True},
        {"max_trade_size_usd": 0.01},
        {"max_daily_loss_usd": 0.01},
    ],
)
def test_safety_interlock_rejects_money_or_nonresearch_configuration(
    overrides: dict[str, object],
) -> None:
    module = _module()
    with pytest.raises(module.LivePredictionSafetyError):
        module.ensure_live_prediction_safety(Settings(**overrides))


def test_safety_interlock_accepts_money_disabled_research_configuration() -> None:
    module = _module()
    settings = Settings(
        mode=TradingMode.RESEARCH,
        live_trading_enabled=False,
        max_trade_size_usd=0,
        max_daily_loss_usd=0,
    )
    module.ensure_live_prediction_safety(settings)


def test_load_due_markets_enforces_eligibility_window_and_idempotence() -> None:
    module = _module()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    schema.metadata.create_all(engine)
    start = datetime(2026, 8, 26, 18, 0, tzinfo=UTC)
    policy = _policy()
    scheduled = start + timedelta(seconds=policy.selected_offset_seconds)

    with engine.begin() as connection:
        for values in (
            _market_values("due", start_at=start),
            _market_values("future", start_at=start + timedelta(seconds=11)),
            _market_values("expired", start_at=start - timedelta(seconds=11)),
            _market_values("unsupported", start_at=start, horizon_seconds=600),
            _market_values("closed", start_at=start, closed=True),
            _market_values("resolved", start_at=start, resolved_outcome="Up"),
            _market_values("inactive", start_at=start, active=False),
            _market_values("existing", start_at=start),
        ):
            connection.execute(insert(schema.polymarket_markets).values(**values))
        LivePredictionRepository().store(
            connection,
            _prediction(
                "existing",
                start_at=start,
                recorded_at=scheduled + timedelta(seconds=1),
                policy=policy,
            ),
        )

        due = module.load_due_markets(
            connection,
            policies={300: policy},
            now=scheduled,
            max_lateness_seconds=10,
        )
        at_deadline = module.load_due_markets(
            connection,
            policies={300: policy},
            now=scheduled + timedelta(seconds=10),
            max_lateness_seconds=10,
        )

    assert [market.condition_id for market in due] == ["due"]
    assert [market.condition_id for market in at_deadline] == ["due"]
    assert due[0].scheduled_at == scheduled


def test_service_never_observes_before_scheduled_time() -> None:
    module = _module()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    schema.metadata.create_all(engine)
    start = datetime(2026, 8, 26, 19, 0, tzinfo=UTC)
    policy = _policy()
    current = start + timedelta(seconds=policy.selected_offset_seconds - 1)
    observed: list[str] = []

    with engine.begin() as connection:
        connection.execute(
            insert(schema.polymarket_markets).values(
                **_market_values("future", start_at=start)
            )
        )

    async def observer(connection, client, **kwargs):
        observed.append(kwargs["condition_id"])
        return object()

    service = module.LivePredictionService(
        engine=engine,
        policies={300: policy},
        client=object(),
        observer=observer,
        clock=lambda: current,
    )
    asyncio.run(service.run_once())

    assert observed == []
    with engine.begin() as connection:
        rows = connection.execute(select(schema.live_predictions)).mappings().all()
    assert rows == []


def test_service_rechecks_deadline_after_observation_and_does_not_backfill(caplog) -> None:
    module = _module()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    schema.metadata.create_all(engine)
    start = datetime(2026, 8, 26, 20, 0, tzinfo=UTC)
    policy = _policy()
    scheduled = start + timedelta(seconds=policy.selected_offset_seconds)
    current = {"value": scheduled}
    predictor_calls: list[str] = []

    with engine.begin() as connection:
        connection.execute(
            insert(schema.polymarket_markets).values(
                **_market_values("slow", start_at=start)
            )
        )

    async def observer(connection, client, **kwargs):
        current["value"] = scheduled + timedelta(seconds=11)
        return object()

    def predictor(*args, **kwargs):
        predictor_calls.append(kwargs["condition_id"])
        raise AssertionError("predictor must not run after the deadline")

    service = module.LivePredictionService(
        engine=engine,
        policies={300: policy},
        client=object(),
        observer=observer,
        predictor=predictor,
        clock=lambda: current["value"],
    )
    with caplog.at_level(logging.WARNING):
        asyncio.run(service.run_once())

    assert predictor_calls == []
    with engine.begin() as connection:
        rows = connection.execute(select(schema.live_predictions)).mappings().all()
    assert rows == []
    misses = [
        record for record in caplog.records if record.msg == "live_prediction_missed"
    ]
    assert len(misses) == 1
    assert misses[0].condition_id == "slow"
    assert misses[0].reason == "deadline_exceeded_after_observation"


def test_one_market_failure_is_isolated_and_evaluation_runs(caplog) -> None:
    module = _module()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    schema.metadata.create_all(engine)
    start = datetime(2026, 8, 26, 21, 0, tzinfo=UTC)
    policy = _policy()
    scheduled = start + timedelta(seconds=policy.selected_offset_seconds)
    evaluation_times: list[datetime] = []

    with engine.begin() as connection:
        for condition_id in ("bad", "good"):
            connection.execute(
                insert(schema.polymarket_markets).values(
                    **_market_values(condition_id, start_at=start)
                )
            )

    async def observer(connection, client, **kwargs):
        if kwargs["condition_id"] == "bad":
            raise RuntimeError("synthetic observation failure")
        return {"condition_id": kwargs["condition_id"]}

    def predictor(policy_arg, live_input, **kwargs):
        assert live_input["condition_id"] == kwargs["condition_id"]
        return _prediction(
            kwargs["condition_id"],
            start_at=kwargs["market_start_at"],
            recorded_at=scheduled + timedelta(seconds=1),
            policy=policy_arg,
        )

    def evaluator(connection, *, evaluated_at):
        evaluation_times.append(evaluated_at)
        return object()

    service = module.LivePredictionService(
        engine=engine,
        policies={300: policy},
        client=object(),
        observer=observer,
        predictor=predictor,
        evaluator=evaluator,
        clock=lambda: scheduled + timedelta(seconds=1),
    )
    with caplog.at_level(logging.ERROR):
        asyncio.run(service.run_once())

    with engine.begin() as connection:
        condition_ids = connection.execute(
            select(schema.live_predictions.c.condition_id)
        ).scalars().all()
    assert condition_ids == ["good"]
    assert evaluation_times == [scheduled + timedelta(seconds=1)]
    failures = [
        record
        for record in caplog.records
        if record.msg == "live_prediction_market_failed"
    ]
    assert len(failures) == 1
    assert failures[0].condition_id == "bad"
    assert failures[0].error_type == "RuntimeError"


def test_restart_is_idempotent_and_does_not_reobserve_existing_prediction() -> None:
    module = _module()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    schema.metadata.create_all(engine)
    start = datetime(2026, 8, 26, 22, 0, tzinfo=UTC)
    policy = _policy()
    scheduled = start + timedelta(seconds=policy.selected_offset_seconds)
    observations: list[str] = []

    with engine.begin() as connection:
        connection.execute(
            insert(schema.polymarket_markets).values(
                **_market_values("restart", start_at=start)
            )
        )

    async def observer(connection, client, **kwargs):
        observations.append(kwargs["condition_id"])
        return {"condition_id": kwargs["condition_id"]}

    def predictor(policy_arg, live_input, **kwargs):
        return _prediction(
            kwargs["condition_id"],
            start_at=kwargs["market_start_at"],
            recorded_at=scheduled + timedelta(seconds=1),
            policy=policy_arg,
        )

    service = module.LivePredictionService(
        engine=engine,
        policies={300: policy},
        client=object(),
        observer=observer,
        predictor=predictor,
        clock=lambda: scheduled + timedelta(seconds=1),
    )
    asyncio.run(service.run_once())
    asyncio.run(service.run_once())

    assert observations == ["restart"]
    with engine.begin() as connection:
        rows = connection.execute(select(schema.live_predictions)).mappings().all()
    assert len(rows) == 1


def test_service_module_never_imports_order_wallet_signing_or_auth(monkeypatch) -> None:
    sys.modules.pop("bp_engine.live_prediction.service", None)
    original_import = builtins.__import__
    forbidden = {"order", "orders", "wallet", "signing", "auth", "execution"}
    seen_forbidden: list[str] = []

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        parts = set(name.lower().split("."))
        if name.startswith("bp_engine") and parts.intersection(forbidden):
            seen_forbidden.append(name)
            raise AssertionError(f"forbidden trading/auth import: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    importlib.import_module("bp_engine.live_prediction.service")

    assert seen_forbidden == []
