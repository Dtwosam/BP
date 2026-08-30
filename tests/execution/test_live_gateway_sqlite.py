from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, insert

from bp_engine.execution.live import InterlockDecision, PolymarketLiveExecutionGateway
from bp_engine.execution.models import PaperExecutionConfig
from bp_engine.execution.paper import PaperOrderDraft, build_paper_order
from bp_engine.live_prediction.models import LivePrediction
from bp_engine.live_prediction.repository import LivePredictionRepository
from bp_engine.live_readiness.models import LiveRiskPolicy
from bp_engine.live_readiness.repository import LiveReadinessRepository
from bp_engine.storage import schema

NOW = datetime(2026, 8, 30, 12, 28, 44, tzinfo=UTC)


def _prediction(seed: str, condition_id: str) -> LivePrediction:
    return LivePrediction(
        prediction_id=seed * 64,
        semantic_sha256=("f" if seed != "f" else "e") * 64,
        prediction_version="live-prediction-v1",
        live_input_version="phase10-live-market-input-v1",
        condition_id=condition_id,
        slug=f"btc-updown-5m-{condition_id}",
        horizon_seconds=300,
        market_start_at=NOW - timedelta(minutes=4),
        market_end_at=NOW + timedelta(minutes=1),
        scheduled_at=NOW,
        recorded_at=NOW,
        lateness_ms=0,
        up_token_id=f"{condition_id}-up",
        down_token_id=f"{condition_id}-down",
        source_calibration_run_id="phase9-source",
        source_calibration_semantic_sha256="3" * 64,
        source_backtest_run_id="phase8-source",
        source_backtest_semantic_sha256="4" * 64,
        source_training_run_id="phase7-source",
        source_training_semantic_sha256="5" * 64,
        calibration_version="platt-or-identity-v1",
        edge_policy_version="selected-ask-edge-v1",
        source_feature_version="core-v1",
        source_label_version="official-outcome-v1",
        selected_offset_seconds=240,
        policy_sha256="6" * 64,
        calibration_fit={"method": "identity", "intercept": None, "coefficient": None},
        calibration_fit_sha256="7" * 64,
        edge_config={
            "fee_rate": 0.07,
            "slippage_buffer": 0.01,
            "min_edge_grid": [0.0, 0.02],
            "min_validation_trades": 3,
            "max_spread": None,
        },
        edge_config_sha256="8" * 64,
        edge_policy="trade_threshold",
        min_edge=0.02,
        training_prior=0.48,
        raw_probability=0.72,
        calibrated_probability=0.72,
        predicted_target=1,
        predicted_side="up",
        market_probability_observed=True,
        market_probability=0.60,
        market_probability_observed_at=NOW,
        market_probability_downloaded_at=NOW,
        market_probability_source="polymarket_clob",
        market_probability_dataset="prices_history",
        market_probability_request_params={"market": f"{condition_id}-up", "fidelity": "1"},
        market_probability_response_sha256="9" * 64,
        up_best_bid=0.59,
        up_best_ask=0.60,
        up_book_cutoff_at=NOW,
        up_book_fresh=True,
        down_best_bid=0.39,
        down_best_ask=0.41,
        down_book_cutoff_at=NOW,
        down_book_fresh=True,
        selected_side="up",
        executable=True,
        trade=True,
        decision_reason="trade",
        selected_ask=0.60,
        selected_bid=0.59,
        selected_spread=0.01,
        fee=0.0168,
        slippage_buffer=0.01,
        raw_edge=0.12,
        cost_adjusted_edge=0.0932,
        decision_min_edge=0.02,
        edge_decision={"side": "up", "trade": True, "reason": "trade"},
        input_fingerprint="a" * 64,
    )


def _policy(*, zero_limits: bool = False) -> LiveRiskPolicy:
    return LiveRiskPolicy(
        max_trade_size_usd=Decimal("0" if zero_limits else "10"),
        max_total_exposure_usd=Decimal("0" if zero_limits else "25"),
        max_daily_loss_usd=Decimal("0" if zero_limits else "10"),
        max_consecutive_losses=0 if zero_limits else 3,
        min_edge=Decimal("0.02"),
        min_probability=Decimal("0.60"),
        min_liquidity_usd=Decimal("1"),
        max_spread=Decimal("0.05"),
        max_prediction_age_seconds=Decimal("10"),
        min_time_to_expiry_seconds=Decimal("10"),
        cooldown_seconds=Decimal("0"),
    )


def _case(seed: str, condition_id: str):
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    repository = LiveReadinessRepository()
    prediction = _prediction(seed, condition_id)
    draft = build_paper_order(
        asdict(prediction),
        PaperExecutionConfig(order_ttl_ms=30_000),
        Decimal("100"),
    )
    assert isinstance(draft, PaperOrderDraft)
    with engine.begin() as connection:
        LivePredictionRepository().store(connection, prediction)
        repository.store_reconciliation_run(
            connection,
            observed_at=NOW - timedelta(milliseconds=500),
            unresolved_count=0,
            critical_count=0,
            evidence={
                "account_snapshot": {
                    "realized_daily_pnl_usd": "0",
                    "consecutive_losses": 0,
                    "total_exposure_usd": "0",
                }
            },
        )
        connection.execute(
            insert(schema.market_state_1s).values(
                bucket_at=NOW - timedelta(seconds=1),
                state_key=f"polymarket:market:{condition_id}:up",
                source="polymarket",
                stream="market",
                instrument=condition_id,
                market_id=condition_id,
                asset_id=f"{condition_id}-up",
                last_event_at=NOW - timedelta(milliseconds=250),
                state={
                    "best_bid": "0.59",
                    "best_ask": "0.60",
                    "bid_depth": "50",
                    "ask_depth": "50",
                },
            )
        )
    return engine, repository, draft.request


def _forbidden_factory():
    raise AssertionError("client factory must not be called")


def test_sqlite_roundtrip_reaches_interlock_without_source_mismatch() -> None:
    engine, repository, request = _case("b", "phase14-sqlite-interlock")
    gateway = PolymarketLiveExecutionGateway(
        engine=engine,
        repository=repository,
        policy=_policy(),
        client_factory=_forbidden_factory,
        interlock=lambda _now: InterlockDecision(
            eligible=False,
            reasons=("live_trading_disabled",),
        ),
        api_health=lambda: True,
        now=lambda: NOW,
    )

    ack = gateway.submit_order(request)

    assert ack.accepted is False
    assert ack.reason == "live_interlock_blocked"


def test_sqlite_roundtrip_reaches_zero_limit_risk_without_source_mismatch() -> None:
    engine, repository, request = _case("c", "phase14-sqlite-risk")
    gateway = PolymarketLiveExecutionGateway(
        engine=engine,
        repository=repository,
        policy=_policy(zero_limits=True),
        client_factory=_forbidden_factory,
        interlock=lambda _now: InterlockDecision(eligible=True),
        api_health=lambda: True,
        now=lambda: NOW,
    )

    ack = gateway.submit_order(request)

    assert ack.accepted is False
    assert ack.reason == "trade_size_limit_exceeded"
