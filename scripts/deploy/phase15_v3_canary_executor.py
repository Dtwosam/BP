from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import polymarket

GEOBLOCK_URL = "https://polymarket.com/api/geoblock"
MAX_NOTIONAL_USD = Decimal("10")
TTL_SECONDS = 2


def _fail(code: str) -> int:
    print(
        json.dumps(
            {
                "accepted": False,
                "external_order_id": None,
                "status": "error",
                "code": code,
                "message": "canary executor failed closed",
            },
            sort_keys=True,
        )
    )
    return 1


def _decimal(value: object, name: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _geoblock() -> dict[str, object]:
    request = urllib.request.Request(
        GEOBLOCK_URL,
        headers={"User-Agent": "BP-phase15-v3-live-canary/1"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError("geoblock_non_200")
        payload = json.loads(response.read().decode("utf-8"))
    if type(payload.get("blocked")) is not bool:
        raise RuntimeError("geoblock_invalid_blocked")
    if not isinstance(payload.get("country"), str):
        raise RuntimeError("geoblock_invalid_country")
    if not isinstance(payload.get("region"), str):
        raise RuntimeError("geoblock_invalid_region")
    return {
        "blocked": payload["blocked"],
        "country": payload["country"],
        "region": payload["region"],
        "direct_url": GEOBLOCK_URL,
    }


def _client() -> object:
    private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "").strip()
    wallet = os.environ.get("POLYMARKET_WALLET_ADDRESS", "").strip()
    if not private_key:
        raise RuntimeError("private_key_missing")
    kwargs: dict[str, str] = {"private_key": private_key}
    if wallet:
        kwargs["wallet"] = wallet
    try:
        return polymarket.SecureClient.create(**kwargs)
    except Exception as exc:
        raise RuntimeError("client_creation_failed") from exc


def _health() -> dict[str, object]:
    geo = _geoblock()
    if geo["blocked"] is not False:
        raise RuntimeError("geoblock_blocked")
    private_key_configured = bool(os.environ.get("POLYMARKET_PRIVATE_KEY", "").strip())
    wallet_configured = bool(os.environ.get("POLYMARKET_WALLET_ADDRESS", "").strip())
    if not private_key_configured:
        raise RuntimeError("private_key_missing")
    _client()
    return {
        "status": "ok",
        "geoblock": geo,
        "private_key_configured": True,
        "wallet_configured": wallet_configured,
        "sdk_import_ok": True,
        "live_order_submitted": False,
    }


def _submit(payload: dict[str, Any]) -> dict[str, object]:
    geo = _geoblock()
    if geo["blocked"] is not False:
        raise RuntimeError("geoblock_blocked")

    request = payload.get("request")
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    market_end_at = datetime.fromisoformat(str(payload["market_end_at"]))
    if market_end_at.tzinfo is None or market_end_at.utcoffset() is None:
        raise ValueError("market_end_at must be timezone-aware")
    market_end_at = market_end_at.astimezone(UTC)
    now = datetime.now(UTC)
    if (market_end_at - now).total_seconds() < 10:
        raise RuntimeError("too_close_to_market_end")

    token_id = str(request.get("token_id") or "")
    action = str(request.get("action") or "")
    price = _decimal(request.get("limit_price"), "limit_price")
    size = _decimal(request.get("requested_shares"), "requested_shares")
    target = _decimal(request.get("target_notional_usd"), "target_notional_usd")
    if not token_id:
        raise ValueError("token_id is required")
    if action != "BUY":
        raise ValueError("canary action must be BUY")
    if not Decimal("0") < price <= Decimal("1"):
        raise ValueError("limit_price must be within (0, 1]")
    if size <= 0 or target <= 0:
        raise ValueError("size and target_notional_usd must be positive")
    if target > MAX_NOTIONAL_USD or price * size > MAX_NOTIONAL_USD:
        raise RuntimeError("canary_notional_limit_exceeded")

    client = _client()
    try:
        signed_order = client.create_limit_order(
            token_id=token_id,
            price=price,
            size=size,
            side="BUY",
        )
        response = client.post_order(signed_order)
    except Exception:
        return {
            "accepted": False,
            "external_order_id": None,
            "status": "error",
            "code": "sdk_exception",
            "message": "Polymarket SDK order submission failed",
            "geoblock": geo,
        }

    if isinstance(response, polymarket.RejectedOrder):
        return {
            "accepted": False,
            "external_order_id": None,
            "status": "rejected",
            "code": str(response.code),
            "message": str(response.message),
            "geoblock": geo,
        }
    if not isinstance(response, polymarket.AcceptedOrder):
        return {
            "accepted": False,
            "external_order_id": None,
            "status": "error",
            "code": "unexpected_sdk_response",
            "message": "Polymarket SDK returned an unexpected order response",
            "geoblock": geo,
        }

    order_id = str(response.order_id)
    initial_status = str(response.status)
    time.sleep(TTL_SECONDS)
    cancellation: dict[str, object]
    try:
        cancel_response = client.cancel_order(order_id=order_id)
        if isinstance(cancel_response, polymarket.CancelOrdersResponse):
            if order_id in cancel_response.canceled:
                cancellation = {
                    "cancelled": True,
                    "status": "cancelled",
                    "message": "",
                }
            elif order_id in cancel_response.not_canceled:
                cancellation = {
                    "cancelled": False,
                    "status": "not_cancelled",
                    "message": str(cancel_response.not_canceled[order_id]),
                }
            else:
                cancellation = {
                    "cancelled": False,
                    "status": "unknown",
                    "message": "order absent from cancellation response",
                }
        else:
            cancellation = {
                "cancelled": False,
                "status": "unknown",
                "message": "unexpected cancellation response",
            }
    except Exception:
        cancellation = {
            "cancelled": False,
            "status": "error",
            "message": "Polymarket SDK cancellation failed",
        }

    return {
        "accepted": True,
        "external_order_id": order_id,
        "status": initial_status,
        "code": "accepted",
        "message": "",
        "geoblock": geo,
        "ttl_seconds": TTL_SECONDS,
        "cancellation": cancellation,
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("input must be a JSON object")
        action = str(payload.get("action") or "")
        if action == "health":
            result = _health()
        elif action == "submit":
            result = _submit(payload)
        else:
            raise ValueError("unsupported action")
    except Exception:
        return _fail("executor_preflight_failed")
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
