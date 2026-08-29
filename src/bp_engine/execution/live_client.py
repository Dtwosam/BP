from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

import polymarket

from bp_engine.config import Settings
from bp_engine.live_readiness.secrets import load_private_key_for_sdk


@dataclass(frozen=True)
class LiveClientOrderResult:
    accepted: bool
    external_order_id: str | None
    status: str
    code: str
    message: str


@dataclass(frozen=True)
class LiveClientCancelResult:
    cancelled: bool
    external_order_id: str
    status: str
    message: str


@runtime_checkable
class LiveTradingClient(Protocol):
    def submit_limit_buy(
        self,
        *,
        token_id: str,
        price: Decimal,
        size: Decimal,
    ) -> LiveClientOrderResult: ...

    def cancel(self, *, external_order_id: str) -> LiveClientCancelResult: ...


class _SdkHandle:
    """Keep the concrete SDK client out of wrapper repr/debug state."""

    def __init__(self, client: object) -> None:
        self.client = client

    def __repr__(self) -> str:
        return "<official-polymarket-sdk-client>"


class OfficialPolymarketTradingClient:
    """Narrow, normalized boundary around the official Polymarket SDK."""

    def __init__(self, *, _sdk_client: object) -> None:
        self._sdk = _SdkHandle(_sdk_client)

    def __repr__(self) -> str:
        return "OfficialPolymarketTradingClient(<redacted>)"

    @classmethod
    def create_from_environment(
        cls,
        *,
        settings: Settings,
        environ: Mapping[str, str] | None = None,
    ) -> OfficialPolymarketTradingClient:
        values = os.environ if environ is None else environ
        try:
            private_key = load_private_key_for_sdk(
                private_key_env=settings.polymarket_private_key_env,
                environ=values,
            )
            wallet = values.get(settings.polymarket_wallet_address_env, "").strip()
            create_kwargs: dict[str, str] = {"private_key": private_key}
            if wallet:
                create_kwargs["wallet"] = wallet
            sdk_client = polymarket.SecureClient.create(**create_kwargs)
        except Exception:
            raise RuntimeError("failed to create official Polymarket trading client") from None
        return cls(_sdk_client=sdk_client)

    def submit_limit_buy(
        self,
        *,
        token_id: str,
        price: Decimal,
        size: Decimal,
    ) -> LiveClientOrderResult:
        try:
            signed_order = self._sdk.client.create_limit_order(
                token_id=token_id,
                price=price,
                size=size,
                side="BUY",
            )
            response = self._sdk.client.post_order(signed_order)
        except Exception:
            return LiveClientOrderResult(
                accepted=False,
                external_order_id=None,
                status="error",
                code="sdk_exception",
                message="Polymarket SDK order submission failed",
            )

        if isinstance(response, polymarket.AcceptedOrder):
            return LiveClientOrderResult(
                accepted=True,
                external_order_id=str(response.order_id),
                status=str(response.status),
                code="accepted",
                message="",
            )
        if isinstance(response, polymarket.RejectedOrder):
            return LiveClientOrderResult(
                accepted=False,
                external_order_id=None,
                status="rejected",
                code=str(response.code),
                message=str(response.message),
            )
        return LiveClientOrderResult(
            accepted=False,
            external_order_id=None,
            status="error",
            code="unexpected_sdk_response",
            message="Polymarket SDK returned an unexpected order response",
        )

    def cancel(self, *, external_order_id: str) -> LiveClientCancelResult:
        try:
            response = self._sdk.client.cancel_order(order_id=external_order_id)
        except Exception:
            return LiveClientCancelResult(
                cancelled=False,
                external_order_id=external_order_id,
                status="error",
                message="Polymarket SDK cancellation failed",
            )

        if isinstance(response, polymarket.CancelOrdersResponse):
            if external_order_id in response.canceled:
                return LiveClientCancelResult(
                    cancelled=True,
                    external_order_id=external_order_id,
                    status="cancelled",
                    message="",
                )
            if external_order_id in response.not_canceled:
                return LiveClientCancelResult(
                    cancelled=False,
                    external_order_id=external_order_id,
                    status="not_cancelled",
                    message=str(response.not_canceled[external_order_id]),
                )
        return LiveClientCancelResult(
            cancelled=False,
            external_order_id=external_order_id,
            status="error",
            message="Polymarket SDK returned an unexpected cancellation response",
        )
