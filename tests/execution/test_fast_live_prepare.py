from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event

from bp_engine.execution import fast_live_prepare as fast_live_prepare_module
from bp_engine.execution.fast_live_prepare import (
    FastLiveDraftUnavailable,
    FrozenPaperCashTracker,
    _has_preview_arm_window,
    _request_from_preview,
    _unresolved_pending_intent_id,
    build_fast_live_draft,
    continuous_fast_live_policy,
    frozen_v3_paper_config,
)
from bp_engine.execution.paper import PaperOrderDraft, build_paper_order
from bp_engine.features.hashing import canonical_hash
from bp_engine.live_readiness.hashing import derive_id, semantic_sha256
from bp_engine.storage import schema

BASE = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)


def _prediction() -> dict[str, object]:
    return {
        "prediction_id": "prediction-fast-parity",
        "semantic_sha256": "a" * 64,
        "condition_id": "condition-fast-parity",
        "recorded_at": BASE,
        "market_end_at": BASE + timedelta(seconds=60),
        "up_token_id": "up-token-fast",
        "down_token_id": "down-token-fast",
        "selected_side": "up",
        "trade": True,
        "executable": True,
        "selected_ask": Decimal("0.58"),
        "slippage_buffer": Decimal("0.01"),
        "edge_config": {"fee_rate": Decimal("0.07")},
    }



def _insert_cash_test_order(
    connection,
    *,
    paper_order_id: str,
    prediction_id: str,
    execution_version: str,
) -> None:
    connection.execute(
        schema.paper_orders.insert().values(
            paper_order_id=paper_order_id,
            prediction_id=prediction_id,
            prediction_semantic_sha256="1" * 64,
            execution_version=execution_version,
            execution_config_sha256="2" * 64,
            condition_id=f"condition-{paper_order_id}",
            token_id=f"token-{paper_order_id}",
            selected_side="up",
            requested_shares=Decimal("5"),
            target_notional_usd=Decimal("5"),
            submitted_at=BASE,
            arrival_at=BASE + timedelta(milliseconds=250),
            expires_at=BASE + timedelta(seconds=2),
            limit_price=Decimal("0.50"),
            signal_selected_ask=Decimal("0.49"),
            signal_fee_rate=Decimal("0.01"),
            signal_slippage_buffer=Decimal("0.01"),
            execution_config={"test": True},
            semantic_sha256="3" * 64,
            created_at=BASE,
        )
    )


def _insert_cash_test_fill(
    connection,
    *,
    paper_order_id: str,
    fill_key: str,
    total_cost: Decimal,
) -> None:
    connection.execute(
        schema.paper_fills.insert().values(
            paper_order_id=paper_order_id,
            fill_key=fill_key,
            fill_at=BASE,
            shares=Decimal("1"),
            price=Decimal("0.50"),
            gross_cost=total_cost,
            fee=Decimal("0"),
            total_cost=total_cost,
            signal_ask_slippage=Decimal("0"),
            book_anchor_event_id=1,
            book_anchor_dedupe_key=f"anchor-{fill_key}",
            book_applied_event_ids=[],
            book_applied_dedupe_keys=[],
            replay_cutoff_at=BASE,
            semantic_sha256="4" * 64,
            created_at=BASE,
        )
    )


def _insert_cash_test_settlement(
    connection,
    *,
    paper_order_id: str,
    label_version: str,
    payout: Decimal,
) -> None:
    connection.execute(
        schema.paper_settlements.insert().values(
            paper_order_id=paper_order_id,
            label_version=label_version,
            official_outcome="Up",
            official_target=1,
            label_source="test",
            label_source_snapshot_sha256="5" * 64,
            label_source_observed_at=BASE,
            filled_shares=Decimal("1"),
            total_fill_cost=Decimal("1"),
            total_fees=Decimal("0"),
            payout=payout,
            realized_pnl=payout - Decimal("1"),
            settled_at=BASE,
            semantic_sha256="6" * 64,
            created_at=BASE,
        )
    )


def test_frozen_paper_cash_tracker_is_incremental_and_one_query() -> None:
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    frozen_order = "paper-order-frozen-cash"
    other_order = "paper-order-other-cash"
    with engine.begin() as connection:
        _insert_cash_test_order(
            connection,
            paper_order_id=frozen_order,
            prediction_id="a" * 64,
            execution_version="paper-execution-v3-frozen-v1",
        )
        _insert_cash_test_order(
            connection,
            paper_order_id=other_order,
            prediction_id="b" * 64,
            execution_version="paper-execution-v1",
        )
        _insert_cash_test_fill(
            connection,
            paper_order_id=frozen_order,
            fill_key="frozen-fill-1",
            total_cost=Decimal("10"),
        )
        _insert_cash_test_fill(
            connection,
            paper_order_id=other_order,
            fill_key="other-fill-1",
            total_cost=Decimal("50"),
        )
        _insert_cash_test_settlement(
            connection,
            paper_order_id=frozen_order,
            label_version="label-1",
            payout=Decimal("3"),
        )
        _insert_cash_test_settlement(
            connection,
            paper_order_id=other_order,
            label_version="label-other",
            payout=Decimal("50"),
        )

    statements: list[str] = []

    def before_cursor_execute(
        _conn,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    tracker = FrozenPaperCashTracker()
    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        with engine.connect() as connection:
            assert tracker.refresh(connection) == Decimal("93")
            assert tracker.last_refresh_was_incremental is False

        with engine.begin() as connection:
            _insert_cash_test_fill(
                connection,
                paper_order_id=frozen_order,
                fill_key="frozen-fill-2",
                total_cost=Decimal("2"),
            )
            _insert_cash_test_settlement(
                connection,
                paper_order_id=frozen_order,
                label_version="label-2",
                payout=Decimal("1"),
            )

        with engine.connect() as connection:
            assert tracker.refresh(connection) == Decimal("92")
            assert tracker.last_refresh_was_incremental is True
            assert tracker.last_fill_cost_delta == Decimal("2")
            assert tracker.last_settlement_payout_delta == Decimal("1")
            assert tracker.refresh(connection) == Decimal("92")
            assert tracker.last_fill_cost_delta == Decimal("0")
            assert tracker.last_settlement_payout_delta == Decimal("0")
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)

    assert len(statements) == 3


def test_fast_live_draft_is_exact_frozen_paper_formula() -> None:
    prediction = _prediction()
    config = frozen_v3_paper_config()
    cash = Decimal("100")

    ordinary = build_paper_order(prediction, config, cash)
    assert isinstance(ordinary, PaperOrderDraft)

    paper_order_id, fast = build_fast_live_draft(
        prediction,
        available_paper_cash=cash,
    )

    assert fast.request == ordinary.request
    assert fast.signal_selected_ask == ordinary.signal_selected_ask
    assert fast.signal_fee_rate == ordinary.signal_fee_rate
    assert fast.signal_slippage_buffer == ordinary.signal_slippage_buffer
    assert fast.execution_config == ordinary.execution_config
    assert fast.request.limit_price == Decimal("0.59")
    assert fast.request.submitted_at == BASE
    assert fast.request.arrival_at == BASE + timedelta(milliseconds=250)
    assert fast.request.expires_at == BASE + timedelta(milliseconds=2250)
    assert paper_order_id == canonical_hash(
        {
            "prediction_id": fast.request.prediction_id,
            "execution_version": fast.request.execution_version,
        }
    )



def _preview() -> tuple[dict[str, object], PaperOrderDraft]:
    prediction = _prediction()
    paper_order_id, draft = build_fast_live_draft(
        prediction,
        available_paper_cash=Decimal("100"),
    )
    request_id = derive_id(
        "live-request",
        semantic_sha256(draft.request.as_mapping(raw=True)),
    )
    preview = {
        "status": "prepared",
        "risk_status": "pending",
        "intent_id": "fast-live-candidate-test",
        "request_id": request_id,
        "prediction_id": draft.request.prediction_id,
        "paper_order_id": paper_order_id,
        "market_end_at": prediction["market_end_at"].isoformat(),
        "request": draft.request.as_mapping(),
    }
    return preview, draft


def test_preview_request_round_trips_without_rebuilding_order() -> None:
    preview, draft = _preview()

    paper_order_id, request = _request_from_preview(preview)

    assert paper_order_id == preview["paper_order_id"]
    assert request == draft.request


def test_preview_request_tamper_fails_closed() -> None:
    preview, _draft = _preview()
    tampered = copy.deepcopy(preview)
    tampered_request = dict(tampered["request"])
    tampered_request["limit_price"] = "0.60"
    tampered["request"] = tampered_request

    with pytest.raises(
        RuntimeError,
        match="fast live preview request id changed",
    ):
        _request_from_preview(tampered)

def test_fast_live_frozen_config_remains_exact() -> None:
    config = frozen_v3_paper_config()
    assert config.starting_cash_usd == Decimal("100.00")
    assert config.target_notional_usd == Decimal("5.00")
    assert config.latency_ms == 250
    assert config.order_ttl_ms == 2000
    assert config.share_precision == 6
    assert config.execution_version == "paper-execution-v3-frozen-v1"
    assert config.prediction_version == "v3-frozen-paper-v1"


def test_continuous_fast_live_policy_has_no_canary_cooldown() -> None:
    policy = continuous_fast_live_policy(max_consecutive_losses=0)
    assert policy.cooldown_seconds == Decimal("0")
    assert policy.max_trade_size_usd == Decimal("10")
    assert policy.max_total_exposure_usd == Decimal("10")
    assert policy.max_daily_loss_usd == Decimal("10")
    assert policy.max_consecutive_losses == 0
    assert policy.min_edge == Decimal("0.075")


def test_continuous_fast_live_policy_can_preserve_legacy_v1_loss_limit() -> None:
    policy = continuous_fast_live_policy(max_consecutive_losses=1)
    assert policy.max_consecutive_losses == 1



def test_preview_arm_window_rejects_too_late_signal() -> None:
    prediction = _prediction()

    assert _has_preview_arm_window(
        prediction,
        observed_at=BASE + timedelta(seconds=30),
    ) is True
    assert _has_preview_arm_window(
        prediction,
        observed_at=BASE + timedelta(seconds=31),
    ) is False



def test_fast_live_draft_terminal_is_classified_for_continuous_blocking() -> None:
    with pytest.raises(FastLiveDraftUnavailable) as exc_info:
        build_fast_live_draft(
            _prediction(),
            available_paper_cash=Decimal("0"),
        )

    assert exc_info.value.status == "INSUFFICIENT_PAPER_CASH"
    assert "available paper cash is not positive" in exc_info.value.reason


class _FakePendingResult:
    def __init__(self, pending: dict[str, object] | None) -> None:
        self._pending = pending

    def mappings(self):
        return self

    def one_or_none(self):
        return self._pending


class _FakeScalarResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakePendingConnection:
    def __init__(
        self,
        *,
        intent_id: str | None,
        terminal_event: str | None = None,
    ) -> None:
        self.intent_id = intent_id
        self.terminal_event = terminal_event
        self.calls = 0

    def execute(self, _statement):
        self.calls += 1
        if self.calls != 1:
            raise AssertionError("unexpected execute call")
        pending = (
            {
                "intent_id": self.intent_id,
                "terminal_event": self.terminal_event,
            }
            if self.intent_id is not None
            else None
        )
        return _FakePendingResult(pending)


def test_pending_gate_accepts_official_zero_fill_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intent_id = "live-intent-zero-fill-reconciled"
    connection = _FakePendingConnection(intent_id=intent_id)
    monkeypatch.setattr(
        fast_live_prepare_module,
        "_official_zero_fill_reconciled_intents",
        lambda _connection, *, observed_at: {intent_id},
    )

    unresolved = _unresolved_pending_intent_id(
        connection,
        observed_at=BASE,
    )

    assert unresolved is None
    assert connection.calls == 1


def test_pending_gate_blocks_truly_unresolved_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intent_id = "live-intent-unresolved"
    connection = _FakePendingConnection(
        intent_id=intent_id,
        terminal_event=None,
    )
    monkeypatch.setattr(
        fast_live_prepare_module,
        "_official_zero_fill_reconciled_intents",
        lambda _connection, *, observed_at: set(),
    )

    unresolved = _unresolved_pending_intent_id(
        connection,
        observed_at=BASE,
    )

    assert unresolved == intent_id
    assert connection.calls == 1


def test_pending_gate_accepts_terminal_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intent_id = "live-intent-terminal"
    connection = _FakePendingConnection(
        intent_id=intent_id,
        terminal_event="closed_before_submission",
    )
    monkeypatch.setattr(
        fast_live_prepare_module,
        "_official_zero_fill_reconciled_intents",
        lambda _connection, *, observed_at: set(),
    )

    unresolved = _unresolved_pending_intent_id(
        connection,
        observed_at=BASE,
    )

    assert unresolved is None
    assert connection.calls == 1


def test_pending_gate_terminal_common_case_skips_reconciliation_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakePendingConnection(
        intent_id="live-intent-terminal-fast-path",
        terminal_event="closed_before_submission",
    )

    def unexpected_reconciliation_scan(_connection, *, observed_at):
        raise AssertionError("terminal intent must not scan reconciliation history")

    monkeypatch.setattr(
        fast_live_prepare_module,
        "_official_zero_fill_reconciled_intents",
        unexpected_reconciliation_scan,
    )

    assert _unresolved_pending_intent_id(
        connection,
        observed_at=BASE,
    ) is None
    assert connection.calls == 1



def _insert_polymarket_source_time_event(
    connection,
    *,
    received_at: datetime,
    source_timestamp: datetime | None,
) -> None:
    connection.execute(
        schema.raw_market_events.insert().values(
            source="polymarket",
            stream="market",
            instrument="condition-source-time-health",
            event_type="price_change",
            source_timestamp=source_timestamp,
            received_at=received_at,
            sequence=None,
            market_id="market-source-time-health",
            asset_id=None,
            payload={
                "event_type": "price_change",
                "market": "condition-source-time-health",
                "price_changes": [
                    {
                        "asset_id": "token-source-time-health",
                        "best_bid": "0.52",
                        "best_ask": "0.53",
                        "price": "0.53",
                        "side": "SELL",
                        "size": "10",
                    }
                ],
            },
            dedupe_key=canonical_hash(
                {
                    "received_at": received_at.isoformat(),
                    "source_timestamp": (
                        source_timestamp.isoformat()
                        if source_timestamp is not None
                        else None
                    ),
                }
            ),
        )
    )


def test_polymarket_source_time_health_accepts_recent_exchange_time() -> None:
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    with engine.begin() as connection:
        _insert_polymarket_source_time_event(
            connection,
            received_at=BASE - timedelta(milliseconds=100),
            source_timestamp=BASE - timedelta(milliseconds=400),
        )

    with engine.connect() as connection:
        health = fast_live_prepare_module._polymarket_source_time_health(
            connection,
            condition_id="condition-source-time-health",
            token_id="token-source-time-health",
            observed_at=BASE,
        )

    assert health.eligible is True
    assert health.reason is None
    assert health.source_age_seconds == Decimal("0.4")
    assert health.transport_lag_seconds == Decimal("0.3")


def test_polymarket_source_time_health_rejects_recently_received_backlog() -> None:
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    with engine.begin() as connection:
        _insert_polymarket_source_time_event(
            connection,
            received_at=BASE - timedelta(milliseconds=100),
            source_timestamp=BASE - timedelta(seconds=20),
        )

    with engine.connect() as connection:
        health = fast_live_prepare_module._polymarket_source_time_health(
            connection,
            condition_id="condition-source-time-health",
            token_id="token-source-time-health",
            observed_at=BASE,
        )

    assert health.eligible is False
    assert health.reason == "polymarket_source_lag"
    assert health.source_age_seconds == Decimal("20.0")
    assert health.transport_lag_seconds == Decimal("19.9")


def test_polymarket_source_time_health_fails_closed_without_source_time() -> None:
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    with engine.begin() as connection:
        _insert_polymarket_source_time_event(
            connection,
            received_at=BASE - timedelta(milliseconds=100),
            source_timestamp=None,
        )

    with engine.connect() as connection:
        health = fast_live_prepare_module._polymarket_source_time_health(
            connection,
            condition_id="condition-source-time-health",
            token_id="token-source-time-health",
            observed_at=BASE,
        )

    assert health.eligible is False
    assert health.reason == "polymarket_source_time_unavailable"
    assert health.source_age_seconds is None


def test_polymarket_source_time_health_rejects_material_clock_ahead() -> None:
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    with engine.begin() as connection:
        _insert_polymarket_source_time_event(
            connection,
            received_at=BASE - timedelta(milliseconds=100),
            source_timestamp=BASE + timedelta(seconds=2),
        )

    with engine.connect() as connection:
        health = fast_live_prepare_module._polymarket_source_time_health(
            connection,
            condition_id="condition-source-time-health",
            token_id="token-source-time-health",
            observed_at=BASE,
        )

    assert health.eligible is False
    assert health.reason == "polymarket_source_clock_ahead"
    assert health.source_age_seconds == Decimal("-2.0")


def test_polymarket_source_time_failures_are_retryable() -> None:
    for reason in (
        "polymarket_source_time_unavailable",
        "polymarket_source_lag",
        "polymarket_source_clock_ahead",
    ):
        assert fast_live_prepare_module._retryable_risk_reasons((reason,)) is True
        assert (
            fast_live_prepare_module._retryable_risk_reasons(
                ("live_interlock_blocked", reason)
            )
            is True
        )

    assert (
        fast_live_prepare_module._retryable_risk_reasons(
            ("live_interlock_blocked",)
        )
        is False
    )
