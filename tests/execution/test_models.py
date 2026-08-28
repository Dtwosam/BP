from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from bp_engine.execution.models import (
    PAPER_EXECUTION_VERSION,
    ExecutionCancelAck,
    ExecutionOrderAck,
    ExecutionOrderRequest,
    PaperExecutionConfig,
)
from bp_engine.execution.protocol import ExecutionGateway


def _request() -> ExecutionOrderRequest:
    submitted_at = datetime(2026, 8, 28, 16, 0, tzinfo=UTC)
    return ExecutionOrderRequest(
        prediction_id="prediction-1",
        prediction_semantic_sha256="a" * 64,
        condition_id="condition-1",
        token_id="up-token",
        selected_side="up",
        action="BUY",
        requested_shares=Decimal("8.5"),
        target_notional_usd=Decimal("5.00"),
        submitted_at=submitted_at,
        arrival_at=datetime(2026, 8, 28, 16, 0, 0, 250000, tzinfo=UTC),
        expires_at=datetime(2026, 8, 28, 16, 0, 2, 250000, tzinfo=UTC),
        limit_price=Decimal("0.61"),
        execution_version=PAPER_EXECUTION_VERSION,
        execution_config_sha256="b" * 64,
    )


def test_default_paper_config_is_explicit_research_scenario() -> None:
    config = PaperExecutionConfig()

    assert config.starting_cash_usd == Decimal("100.00")
    assert config.target_notional_usd == Decimal("5.00")
    assert config.latency_ms == 250
    assert config.order_ttl_ms == 2000
    assert config.share_precision == 6
    assert config.execution_version == "paper-execution-v1"
    assert config.as_mapping() == {
        "execution_version": "paper-execution-v1",
        "starting_cash_usd": "100.00",
        "target_notional_usd": "5.00",
        "latency_ms": 250,
        "order_ttl_ms": 2000,
        "share_precision": 6,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("starting_cash_usd", Decimal("0")),
        ("target_notional_usd", Decimal("0")),
        ("latency_ms", 0),
        ("order_ttl_ms", 0),
        ("share_precision", -1),
    ],
)
def test_paper_config_rejects_invalid_values(field: str, value: object) -> None:
    kwargs = {field: value}
    with pytest.raises(ValueError):
        PaperExecutionConfig(**kwargs)


def test_order_request_is_fail_closed_and_timezone_aware() -> None:
    request = _request()
    assert request.action == "BUY"
    assert request.requested_shares == Decimal("8.5")

    with pytest.raises(ValueError, match="BUY"):
        ExecutionOrderRequest(**{**request.as_mapping(raw=True), "action": "SELL"})

    with pytest.raises(ValueError, match="timezone-aware"):
        ExecutionOrderRequest(
            **{
                **request.as_mapping(raw=True),
                "submitted_at": datetime(2026, 8, 28, 16, 0),
            }
        )

    with pytest.raises(ValueError, match="timestamp order"):
        ExecutionOrderRequest(
            **{
                **request.as_mapping(raw=True),
                "expires_at": request.arrival_at,
            }
        )


class _StubGateway:
    def submit_order(self, request: ExecutionOrderRequest) -> ExecutionOrderAck:
        return ExecutionOrderAck(
            order_id="paper-order-1",
            accepted=True,
            observed_at=request.submitted_at,
            reason="accepted",
        )

    def cancel_order(self, order_id: str, observed_at: datetime) -> ExecutionCancelAck:
        return ExecutionCancelAck(
            order_id=order_id,
            cancelled=True,
            observed_at=observed_at,
            reason="cancelled",
        )


def test_execution_gateway_contract_is_runtime_checkable() -> None:
    gateway = _StubGateway()
    assert isinstance(gateway, ExecutionGateway)
    assert gateway.submit_order(_request()).accepted is True


def test_phase12_does_not_ship_a_live_gateway() -> None:
    import bp_engine.execution as execution

    assert not hasattr(execution, "LiveExecutionGateway")
