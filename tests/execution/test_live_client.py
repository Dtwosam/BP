from __future__ import annotations

import importlib
from decimal import Decimal
from typing import Any

import polymarket
import pytest

from bp_engine.config import Settings


try:
    live_client = importlib.import_module("bp_engine.execution.live_client")
except ModuleNotFoundError:
    live_client = None


if live_client is None:

    def test_live_client_adapter_exists() -> None:
        pytest.fail("bp_engine.execution.live_client is not implemented")

else:
    LiveClientCancelResult = live_client.LiveClientCancelResult
    LiveClientOrderResult = live_client.LiveClientOrderResult
    OfficialPolymarketTradingClient = live_client.OfficialPolymarketTradingClient

    class FakeSdkClient:
        def __init__(
            self,
            *,
            order_response: object | None = None,
            cancel_response: object | None = None,
            order_error: Exception | None = None,
            cancel_error: Exception | None = None,
        ) -> None:
            self.order_response = order_response
            self.cancel_response = cancel_response
            self.order_error = order_error
            self.cancel_error = cancel_error
            self.limit_order_calls: list[dict[str, object]] = []
            self.posted_orders: list[object] = []
            self.cancel_calls: list[str] = []
            self.signed_order = object()

        def create_limit_order(self, **kwargs: object) -> object:
            self.limit_order_calls.append(kwargs)
            return self.signed_order

        def post_order(self, signed_order: object) -> object:
            self.posted_orders.append(signed_order)
            if self.order_error is not None:
                raise self.order_error
            return self.order_response

        def cancel_order(self, *, order_id: str) -> object:
            self.cancel_calls.append(order_id)
            if self.cancel_error is not None:
                raise self.cancel_error
            return self.cancel_response

    def test_submit_limit_buy_normalizes_accepted_order_and_preserves_exact_args() -> None:
        response = polymarket.AcceptedOrder(
            order_id="order-123",
            status="live",
            making_amount=Decimal("4.2"),
            taking_amount=Decimal("10"),
            trade_ids=(),
            transactions_hashes=(),
        )
        sdk = FakeSdkClient(order_response=response)
        client = OfficialPolymarketTradingClient(_sdk_client=sdk)

        result = client.submit_limit_buy(
            token_id="token-yes",
            price=Decimal("0.42"),
            size=Decimal("10"),
        )

        assert result == LiveClientOrderResult(
            accepted=True,
            external_order_id="order-123",
            status="live",
            code="accepted",
            message="",
        )
        assert sdk.limit_order_calls == [
            {
                "token_id": "token-yes",
                "price": Decimal("0.42"),
                "size": Decimal("10"),
                "side": polymarket.OrderSide.BUY,
            }
        ]
        assert sdk.posted_orders == [sdk.signed_order]

    def test_submit_limit_buy_normalizes_rejected_order() -> None:
        sdk = FakeSdkClient(
            order_response=polymarket.RejectedOrder(
                code="not_enough_balance",
                message="not enough balance / allowance",
            )
        )
        client = OfficialPolymarketTradingClient(_sdk_client=sdk)

        result = client.submit_limit_buy(
            token_id="token-yes",
            price=Decimal("0.42"),
            size=Decimal("10"),
        )

        assert result == LiveClientOrderResult(
            accepted=False,
            external_order_id=None,
            status="rejected",
            code="not_enough_balance",
            message="not enough balance / allowance",
        )

    def test_submit_limit_buy_normalizes_sdk_exception_without_leaking_details() -> None:
        sensitive_detail = "transport failed with secret-like-value"
        sdk = FakeSdkClient(order_error=RuntimeError(sensitive_detail))
        client = OfficialPolymarketTradingClient(_sdk_client=sdk)

        result = client.submit_limit_buy(
            token_id="token-yes",
            price=Decimal("0.42"),
            size=Decimal("10"),
        )

        assert result == LiveClientOrderResult(
            accepted=False,
            external_order_id=None,
            status="error",
            code="sdk_exception",
            message="Polymarket SDK order submission failed",
        )
        assert sensitive_detail not in repr(result)

    def test_cancel_normalizes_cancelled_order() -> None:
        sdk = FakeSdkClient(
            cancel_response=polymarket.CancelOrdersResponse(
                canceled=("order-123",),
                not_canceled={},
            )
        )
        client = OfficialPolymarketTradingClient(_sdk_client=sdk)

        result = client.cancel(external_order_id="order-123")

        assert result == LiveClientCancelResult(
            cancelled=True,
            external_order_id="order-123",
            status="cancelled",
            message="",
        )
        assert sdk.cancel_calls == ["order-123"]

    def test_cancel_normalizes_rejected_cancellation() -> None:
        sdk = FakeSdkClient(
            cancel_response=polymarket.CancelOrdersResponse(
                canceled=(),
                not_canceled={"order-123": "order already matched"},
            )
        )
        client = OfficialPolymarketTradingClient(_sdk_client=sdk)

        result = client.cancel(external_order_id="order-123")

        assert result == LiveClientCancelResult(
            cancelled=False,
            external_order_id="order-123",
            status="not_cancelled",
            message="order already matched",
        )

    def test_cancel_normalizes_sdk_exception_without_leaking_details() -> None:
        sensitive_detail = "cancel failed with secret-like-value"
        sdk = FakeSdkClient(cancel_error=RuntimeError(sensitive_detail))
        client = OfficialPolymarketTradingClient(_sdk_client=sdk)

        result = client.cancel(external_order_id="order-123")

        assert result == LiveClientCancelResult(
            cancelled=False,
            external_order_id="order-123",
            status="error",
            message="Polymarket SDK cancellation failed",
        )
        assert sensitive_detail not in repr(result)

    def test_create_from_environment_loads_secret_only_at_factory_boundary(
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        secret = "test-private-key-that-must-not-leak"
        wallet = "0x00000000000000000000000000000000000000AA"
        calls: list[dict[str, Any]] = []
        fake_sdk = FakeSdkClient()

        def fake_create(**kwargs: Any) -> FakeSdkClient:
            calls.append(kwargs)
            return fake_sdk

        monkeypatch.setattr(
            live_client.polymarket.SecureClient,
            "create",
            staticmethod(fake_create),
        )
        settings = Settings(
            polymarket_private_key_env="TEST_POLYMARKET_PRIVATE_KEY",
            polymarket_wallet_address_env="TEST_POLYMARKET_WALLET_ADDRESS",
        )
        environ = {
            "TEST_POLYMARKET_PRIVATE_KEY": secret,
            "TEST_POLYMARKET_WALLET_ADDRESS": wallet,
        }

        assert calls == []
        client = OfficialPolymarketTradingClient.create_from_environment(
            settings=settings,
            environ=environ,
        )

        assert calls == [{"private_key": secret, "wallet": wallet}]
        assert secret not in repr(client)
        assert secret not in repr(client.__dict__)

    def test_create_from_environment_sanitizes_factory_errors(
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        secret = "test-private-key-that-must-not-leak"

        def fake_create(**kwargs: Any) -> FakeSdkClient:
            raise RuntimeError(f"invalid key: {kwargs['private_key']}")

        monkeypatch.setattr(
            live_client.polymarket.SecureClient,
            "create",
            staticmethod(fake_create),
        )
        settings = Settings(polymarket_private_key_env="TEST_POLYMARKET_PRIVATE_KEY")

        with pytest.raises(RuntimeError) as exc_info:
            OfficialPolymarketTradingClient.create_from_environment(
                settings=settings,
                environ={"TEST_POLYMARKET_PRIVATE_KEY": secret},
            )

        assert str(exc_info.value) == "failed to create official Polymarket trading client"
        assert secret not in str(exc_info.value)
        assert secret not in repr(exc_info.value)
