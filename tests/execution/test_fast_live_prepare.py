from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bp_engine.execution import fast_live_prepare as fast_live_prepare_module
from bp_engine.execution.fast_live_prepare import (
    FastLiveDraftUnavailable,
    _has_preview_arm_window,
    _unresolved_pending_intent_id,
    build_fast_live_draft,
    continuous_fast_live_policy,
    frozen_v3_paper_config,
)
from bp_engine.execution.paper import PaperOrderDraft, build_paper_order
from bp_engine.features.hashing import canonical_hash

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
    policy = continuous_fast_live_policy()
    assert policy.cooldown_seconds == Decimal("0")
    assert policy.max_trade_size_usd == Decimal("10")
    assert policy.max_total_exposure_usd == Decimal("10")
    assert policy.max_daily_loss_usd == Decimal("10")
    assert policy.max_consecutive_losses == 1
    assert policy.min_edge == Decimal("0.075")



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
        if self.calls == 1:
            pending = (
                {"intent_id": self.intent_id}
                if self.intent_id is not None
                else None
            )
            return _FakePendingResult(pending)
        if self.calls == 2:
            return _FakeScalarResult(self.terminal_event)
        raise AssertionError("unexpected execute call")


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
    assert connection.calls == 2


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
    assert connection.calls == 2
