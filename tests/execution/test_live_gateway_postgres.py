from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, delete, func, insert, select

from bp_engine.execution.live import InterlockDecision, PolymarketLiveExecutionGateway
from bp_engine.execution.live_client import LiveClientCancelResult, LiveClientOrderResult
from bp_engine.execution.models import PaperExecutionConfig
from bp_engine.execution.paper import PaperOrderDraft, build_paper_order
from bp_engine.execution.protocol import ExecutionGateway
from bp_engine.live_prediction.repository import LivePredictionRepository
from bp_engine.live_readiness.models import LiveRiskPolicy
from bp_engine.live_readiness.repository import LiveReadinessRepository
from bp_engine.storage import schema
from tests.execution.test_service_postgres import BASE, DATABASE_URL, _prediction

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)

PREDICTION_ID = "f" * 64
PREDICTION_SHA = "d" * 64
CONDITION_ID = "phase14-live-gateway"
STATE_KEY = "polymarket:market:phase14-live-gateway:up"
NOW = BASE + timedelta(seconds=2)


@dataclass
class LiveCase:
    engine: object
    repository: LiveReadinessRepository
    request: object


class FakeLiveClient:
    def __init__(
        self,
        *,
        order_result: LiveClientOrderResult | None = None,
        order_error: Exception | None = None,
        cancel_result: LiveClientCancelResult | None = None,
    ) -> None:
        self.order_result = order_result
        self.order_error = order_error
        self.cancel_result = cancel_result
        self.submit_calls: list[dict[str, object]] = []
        self.cancel_calls: list[str] = []

    def submit_limit_buy(
        self,
        *,
        token_id: str,
        price: Decimal,
        size: Decimal,
    ) -> LiveClientOrderResult:
        self.submit_calls.append({"token_id": token_id, "price": price, "size": size})
        if self.order_error is not None:
            raise self.order_error
        assert self.order_result is not None
        return self.order_result

    def cancel(self, *, external_order_id: str) -> LiveClientCancelResult:
        self.cancel_calls.append(external_order_id)
        assert self.cancel_result is not None
        return self.cancel_result


def _policy(**overrides: object) -> LiveRiskPolicy:
    values: dict[str, object] = {
        "max_trade_size_usd": Decimal("10"),
        "max_total_exposure_usd": Decimal("25"),
        "max_daily_loss_usd": Decimal("10"),
        "max_consecutive_losses": 3,
        "min_edge": Decimal("0.02"),
        "min_probability": Decimal("0.60"),
        "min_liquidity_usd": Decimal("1"),
        "max_spread": Decimal("0.05"),
        "max_prediction_age_seconds": Decimal("10"),
        "min_time_to_expiry_seconds": Decimal("10"),
        "cooldown_seconds": Decimal("0"),
    }
    values.update(overrides)
    return LiveRiskPolicy(**values)


def _forbidden_factory():
    raise AssertionError("client factory must not be called")


def _cleanup(case: LiveCase | None = None) -> None:
    if DATABASE_URL is None:
        return
    engine = case.engine if case is not None else create_engine(DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(delete(schema.live_order_events))
        connection.execute(delete(schema.live_order_intents))
        connection.execute(delete(schema.live_risk_decisions))
        connection.execute(delete(schema.live_reconciliation_runs))
        connection.execute(
            delete(schema.market_state_1s).where(schema.market_state_1s.c.state_key == STATE_KEY)
        )
        connection.execute(
            delete(schema.live_prediction_evaluations).where(
                schema.live_prediction_evaluations.c.prediction_id == PREDICTION_ID
            )
        )
        connection.execute(
            delete(schema.live_predictions).where(
                schema.live_predictions.c.prediction_id == PREDICTION_ID
            )
        )


def _store_good_reconciliation(case: LiveCase) -> None:
    with case.engine.begin() as connection:
        case.repository.store_reconciliation_run(
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


@pytest.fixture
def live_case() -> LiveCase:
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)
    _cleanup()
    repository = LiveReadinessRepository()
    prediction_repository = LivePredictionRepository()
    prediction = _prediction(
        prediction_id=PREDICTION_ID,
        semantic_sha256=PREDICTION_SHA,
        condition_id=CONDITION_ID,
        trade=True,
    )
    config = PaperExecutionConfig(order_ttl_ms=30_000)
    draft = build_paper_order(asdict(prediction), config, Decimal("100"))
    assert isinstance(draft, PaperOrderDraft)

    with engine.begin() as connection:
        prediction_repository.store(connection, prediction)
        connection.execute(
            insert(schema.market_state_1s).values(
                bucket_at=NOW - timedelta(seconds=1),
                state_key=STATE_KEY,
                source="polymarket",
                stream="market",
                instrument=CONDITION_ID,
                market_id=CONDITION_ID,
                asset_id=f"{CONDITION_ID}-up",
                last_event_at=NOW - timedelta(milliseconds=250),
                state={
                    "best_bid": "0.59",
                    "best_ask": "0.60",
                    "bid_depth": "50",
                    "ask_depth": "50",
                },
            )
        )

    case = LiveCase(engine=engine, repository=repository, request=draft.request)
    _store_good_reconciliation(case)
    try:
        yield case
    finally:
        _cleanup(case)


def _gateway(
    case: LiveCase,
    *,
    client_factory=_forbidden_factory,
    interlock: InterlockDecision | None = None,
    policy: LiveRiskPolicy | None = None,
    api_healthy: bool = True,
    now=NOW,
) -> PolymarketLiveExecutionGateway:
    decision = interlock or InterlockDecision(eligible=True, reasons=())
    return PolymarketLiveExecutionGateway(
        engine=case.engine,
        repository=case.repository,
        policy=policy or _policy(),
        client_factory=client_factory,
        interlock=lambda _observed_at: decision,
        api_health=lambda: api_healthy,
        now=lambda: now,
    )


@pytest.mark.parametrize(
    "specific_reason",
    (
        "mode_not_live",
        "live_trading_disabled",
        "trade_size_limit_zero",
        "daily_loss_limit_zero",
        "activation_manifest_missing",
        "activation_manifest_invalid",
        "kill_switch_engaged",
        "geographic_eligibility_blocked",
        "geoblock_error",
    ),
)
def test_interlock_blockers_never_construct_client(
    live_case: LiveCase,
    specific_reason: str,
) -> None:
    gateway = _gateway(
        live_case,
        interlock=InterlockDecision(eligible=False, reasons=(specific_reason,)),
    )

    ack = gateway.submit_order(live_case.request)

    assert ack.accepted is False
    assert ack.reason == "live_interlock_blocked"
    with live_case.engine.begin() as connection:
        risk = connection.execute(
            select(schema.live_risk_decisions).order_by(schema.live_risk_decisions.c.id.desc())
        ).mappings().one()
    assert "live_interlock_blocked" in risk["reasons"]
    assert specific_reason in risk["reasons"]


def test_zero_live_policy_limits_never_construct_client(live_case: LiveCase) -> None:
    gateway = _gateway(
        live_case,
        policy=_policy(
            max_trade_size_usd=Decimal("0"),
            max_total_exposure_usd=Decimal("0"),
            max_daily_loss_usd=Decimal("0"),
            max_consecutive_losses=0,
        ),
    )

    ack = gateway.submit_order(live_case.request)

    assert ack.accepted is False
    assert ack.reason == "trade_size_limit_exceeded"


def test_duplicate_intent_never_constructs_client(live_case: LiveCase) -> None:
    with live_case.engine.begin() as connection:
        live_case.repository.store_order_intent(
            connection,
            prediction_id=PREDICTION_ID,
            policy_version="live-risk-v1",
            request_id="existing-request",
            risk_decision_id="existing-risk",
            token_id=live_case.request.token_id,
            side="BUY",
            size=live_case.request.requested_shares,
            limit_price=live_case.request.limit_price,
            pre_submit_at=NOW - timedelta(seconds=1),
            evidence={"source": "synthetic-existing-intent"},
        )

    ack = _gateway(live_case).submit_order(live_case.request)

    assert ack.accepted is False
    assert ack.reason == "duplicate_intent"


def test_stale_prediction_never_constructs_client(live_case: LiveCase) -> None:
    gateway = _gateway(
        live_case,
        policy=_policy(max_prediction_age_seconds=Decimal("1")),
    )

    ack = gateway.submit_order(live_case.request)

    assert ack.accepted is False
    assert ack.reason == "prediction_stale"


def test_probability_risk_failure_never_constructs_client(live_case: LiveCase) -> None:
    gateway = _gateway(live_case, policy=_policy(min_probability=Decimal("0.90")))

    ack = gateway.submit_order(live_case.request)

    assert ack.accepted is False
    assert ack.reason == "probability_below_minimum"


def test_api_health_failure_never_constructs_client(live_case: LiveCase) -> None:
    gateway = _gateway(live_case, api_healthy=False)

    ack = gateway.submit_order(live_case.request)

    assert ack.accepted is False
    assert ack.reason == "api_unhealthy"


def test_reconciliation_blocker_never_constructs_client(live_case: LiveCase) -> None:
    with live_case.engine.begin() as connection:
        connection.execute(delete(schema.live_reconciliation_runs))
        live_case.repository.store_reconciliation_run(
            connection,
            observed_at=NOW - timedelta(milliseconds=250),
            unresolved_count=1,
            critical_count=1,
            evidence={
                "account_snapshot": {
                    "realized_daily_pnl_usd": "0",
                    "consecutive_losses": 0,
                    "total_exposure_usd": "0",
                },
                "issues": ["intent_without_result"],
            },
        )

    ack = _gateway(live_case).submit_order(live_case.request)

    assert ack.accepted is False
    assert ack.reason == "reconciliation_blocked"


def test_source_identity_mismatch_fails_before_client(live_case: LiveCase) -> None:
    bad_request = type(live_case.request)(
        **{
            **live_case.request.as_mapping(raw=True),
            "prediction_semantic_sha256": "e" * 64,
        }
    )

    ack = _gateway(live_case).submit_order(bad_request)

    assert ack.accepted is False
    assert ack.reason == "source_prediction_mismatch"
    with live_case.engine.begin() as connection:
        risk_count = connection.scalar(select(func.count()).select_from(schema.live_risk_decisions))
        intent_count = connection.scalar(select(func.count()).select_from(schema.live_order_intents))
    assert risk_count == 0
    assert intent_count == 0


def test_eligible_submission_persists_pre_submit_evidence_before_factory(
    live_case: LiveCase,
) -> None:
    fake = FakeLiveClient(
        order_result=LiveClientOrderResult(
            accepted=True,
            external_order_id="external-order-123",
            status="live",
            code="accepted",
            message="",
        )
    )
    factory_calls = 0

    def factory() -> FakeLiveClient:
        nonlocal factory_calls
        factory_calls += 1
        with live_case.engine.begin() as connection:
            risk_count = connection.scalar(select(func.count()).select_from(schema.live_risk_decisions))
            intent_count = connection.scalar(select(func.count()).select_from(schema.live_order_intents))
        assert risk_count == 1
        assert intent_count == 1
        return fake

    gateway = _gateway(live_case, client_factory=factory)
    assert isinstance(gateway, ExecutionGateway)

    first = gateway.submit_order(live_case.request)
    second = gateway.submit_order(live_case.request)

    assert first.accepted is True
    assert first.order_id == "external-order-123"
    assert first.reason == "accepted"
    assert second.accepted is True
    assert second.order_id == "external-order-123"
    assert second.reason == "existing"
    assert factory_calls == 1
    assert fake.submit_calls == [
        {
            "token_id": live_case.request.token_id,
            "price": live_case.request.limit_price,
            "size": live_case.request.requested_shares,
        }
    ]

    with live_case.engine.begin() as connection:
        intent = connection.execute(select(schema.live_order_intents)).mappings().one()
        event = connection.execute(select(schema.live_order_events)).mappings().one()
        risk = connection.execute(select(schema.live_risk_decisions)).mappings().one()
    assert risk["eligible"] is True
    assert intent["risk_decision_id"] == risk["decision_id"]
    assert event["intent_id"] == intent["intent_id"]
    assert event["event_type"] == "accepted"
    assert event["external_order_id"] == "external-order-123"


def test_rejected_submission_is_idempotent_and_never_resubmits(live_case: LiveCase) -> None:
    fake = FakeLiveClient(
        order_result=LiveClientOrderResult(
            accepted=False,
            external_order_id=None,
            status="rejected",
            code="not_enough_balance",
            message="not enough balance / allowance",
        )
    )
    factory_calls = 0

    def factory() -> FakeLiveClient:
        nonlocal factory_calls
        factory_calls += 1
        return fake

    gateway = _gateway(live_case, client_factory=factory)
    first = gateway.submit_order(live_case.request)
    second = gateway.submit_order(live_case.request)

    assert first.accepted is False
    assert first.reason == "not_enough_balance"
    assert second.accepted is False
    assert second.reason == "not_enough_balance"
    assert first.order_id == second.order_id
    assert factory_calls == 1
    assert len(fake.submit_calls) == 1
    with live_case.engine.begin() as connection:
        event = connection.execute(select(schema.live_order_events)).mappings().one()
    assert event["event_type"] == "rejected"


def test_ambiguous_submission_is_recorded_unknown_and_retry_never_resubmits(
    live_case: LiveCase,
) -> None:
    fake = FakeLiveClient(order_error=RuntimeError("ambiguous transport failure"))
    factory_calls = 0

    def factory() -> FakeLiveClient:
        nonlocal factory_calls
        factory_calls += 1
        return fake

    gateway = _gateway(live_case, client_factory=factory)
    first = gateway.submit_order(live_case.request)
    second = gateway.submit_order(live_case.request)

    assert first.accepted is False
    assert first.reason == "submission_unknown"
    assert second.accepted is False
    assert second.reason == "submission_unknown"
    assert first.order_id == second.order_id
    assert factory_calls == 1
    assert len(fake.submit_calls) == 1
    with live_case.engine.begin() as connection:
        event = connection.execute(select(schema.live_order_events)).mappings().one()
    assert event["event_type"] == "submission_unknown"
    assert event["external_order_id"] is None
    assert "ambiguous transport failure" not in repr(event["evidence"])


def test_known_order_cancellation_ignores_submit_interlock_and_persists_event(
    live_case: LiveCase,
) -> None:
    submit_client = FakeLiveClient(
        order_result=LiveClientOrderResult(
            accepted=True,
            external_order_id="external-order-cancel",
            status="live",
            code="accepted",
            message="",
        )
    )
    submit_gateway = _gateway(live_case, client_factory=lambda: submit_client)
    submitted = submit_gateway.submit_order(live_case.request)
    assert submitted.accepted is True

    cancel_client = FakeLiveClient(
        cancel_result=LiveClientCancelResult(
            cancelled=True,
            external_order_id="external-order-cancel",
            status="cancelled",
            message="",
        )
    )

    def cancellation_interlock_must_not_run(_observed_at):
        raise AssertionError("kill switch/interlock must not block risk-reducing cancellation")

    gateway = PolymarketLiveExecutionGateway(
        engine=live_case.engine,
        repository=live_case.repository,
        policy=_policy(),
        client_factory=lambda: cancel_client,
        interlock=cancellation_interlock_must_not_run,
        api_health=lambda: False,
        now=lambda: NOW,
    )
    cancelled = gateway.cancel_order("external-order-cancel", NOW + timedelta(seconds=1))

    assert cancelled.cancelled is True
    assert cancelled.order_id == "external-order-cancel"
    assert cancelled.reason == "cancelled"
    assert cancel_client.cancel_calls == ["external-order-cancel"]
    with live_case.engine.begin() as connection:
        events = connection.execute(
            select(schema.live_order_events).order_by(schema.live_order_events.c.id)
        ).mappings().all()
    assert [event["event_type"] for event in events] == ["accepted", "cancelled"]


def test_unknown_order_cancellation_fails_closed_without_client(live_case: LiveCase) -> None:
    gateway = _gateway(live_case)

    ack = gateway.cancel_order("unknown-external-order", NOW)

    assert ack.cancelled is False
    assert ack.reason == "unknown_order_id"
