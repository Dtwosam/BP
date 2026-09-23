from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import polymarket

GEOBLOCK_URL = "https://polymarket.com/api/geoblock"
MAX_NOTIONAL_USD = Decimal("10")
TTL_SECONDS = 2
ACTIVATION_PATH = Path("/etc/bp-canary/activation.json")
KILL_SWITCH_PATH = Path("/etc/bp-canary/KILL")
COLLATERAL_BASE_UNITS_PER_USD = Decimal("1000000")
TARGET_NOTIONAL_USD = Decimal("5")


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


def _executor_sha256() -> str:
    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError as exc:
        raise RuntimeError("executor_hash_failed") from exc


def _request_sha256(request: dict[str, Any]) -> str:
    encoded = json.dumps(
        request,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _account_preflight(client: object) -> dict[str, object]:
    try:
        balance_allowance = client.get_balance_allowance(asset_type="COLLATERAL")
        open_orders = tuple(client.list_open_orders().iter_items())
    except Exception as exc:
        raise RuntimeError("account_preflight_failed") from exc

    balance_base_units = int(balance_allowance.balance)
    balance_usd = Decimal(balance_base_units) / COLLATERAL_BASE_UNITS_PER_USD
    return {
        "collateral_balance_base_units": balance_base_units,
        "collateral_balance_usd": format(balance_usd, "f"),
        "open_order_count": len(open_orders),
        "clean_for_canary": len(open_orders) == 0 and balance_usd >= TARGET_NOTIONAL_USD,
    }


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


def _activation() -> dict[str, object]:
    try:
        payload = json.loads(ACTIVATION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("activation_missing_or_invalid") from exc
    if payload.get("authorized") is not True:
        raise RuntimeError("activation_not_authorized")
    if str(payload.get("executor_sha256") or "") != _executor_sha256():
        raise RuntimeError("activation_executor_sha256_mismatch")
    if payload.get("source_prediction_version") != "v3-frozen-paper-v1":
        raise RuntimeError("activation_prediction_version_mismatch")
    if payload.get("source_execution_version") != "paper-execution-v3-frozen-v1":
        raise RuntimeError("activation_execution_version_mismatch")
    activation_trade_limit = _decimal(
        payload.get("max_trade_size_usd"),
        "activation.max_trade_size_usd",
    )
    if activation_trade_limit != MAX_NOTIONAL_USD:
        raise RuntimeError("activation_trade_limit_mismatch")
    if int(payload.get("max_submission_attempts", 0)) != 1:
        raise RuntimeError("activation_attempt_limit_mismatch")
    for name in ("intent_id", "prediction_id", "paper_order_id", "request_sha256"):
        if not str(payload.get(name) or "").strip():
            raise RuntimeError(f"activation_{name}_missing")
    issued = datetime.fromisoformat(str(payload["issued_at"])).astimezone(UTC)
    expires = datetime.fromisoformat(str(payload["expires_at"])).astimezone(UTC)
    now = datetime.now(UTC)
    if issued > now or now >= expires:
        raise RuntimeError("activation_expired_or_future")
    return payload


def _kill_switch_engaged() -> bool:
    try:
        return KILL_SWITCH_PATH.exists()
    except OSError:
        return True


def _consume_one_shot_arm() -> None:
    if _kill_switch_engaged():
        raise RuntimeError("kill_switch_engaged")
    try:
        fd = os.open(
            KILL_SWITCH_PATH,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError as exc:
        raise RuntimeError("kill_switch_engaged") from exc
    try:
        os.write(fd, b"phase15-v3-live-canary one-shot arm consumed\n")
    finally:
        os.close(fd)


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
    client = _client()
    account = _account_preflight(client)
    activation_valid = False
    try:
        _activation()
        activation_valid = True
    except RuntimeError:
        activation_valid = False
    return {
        "status": "ok",
        "geoblock": geo,
        "private_key_configured": True,
        "wallet_configured": wallet_configured,
        "sdk_import_ok": True,
        "executor_sha256": _executor_sha256(),
        "account": account,
        "activation_valid": activation_valid,
        "kill_switch_engaged": _kill_switch_engaged(),
        "submission_ready": (
            activation_valid
            and not _kill_switch_engaged()
            and account["clean_for_canary"] is True
        ),
        "live_order_submitted": False,
    }


def _submit(payload: dict[str, Any]) -> dict[str, object]:
    geo = _geoblock()
    if geo["blocked"] is not False:
        raise RuntimeError("geoblock_blocked")
    activation = _activation()
    if str(payload.get("authorization_id") or "") != str(
        activation.get("authorization_id") or ""
    ):
        raise RuntimeError("authorization_id_mismatch")
    for name in ("intent_id", "prediction_id", "paper_order_id"):
        if str(payload.get(name) or "") != str(activation.get(name) or ""):
            raise RuntimeError(f"{name}_mismatch")

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
    if target != TARGET_NOTIONAL_USD:
        raise RuntimeError("canary_target_notional_changed")
    if target > MAX_NOTIONAL_USD or price * size > MAX_NOTIONAL_USD:
        raise RuntimeError("canary_notional_limit_exceeded")

    request_sha256 = _request_sha256(request)
    if request_sha256 != str(activation.get("request_sha256") or ""):
        raise RuntimeError("request_sha256_mismatch")

    client = _client()
    account = _account_preflight(client)
    if account["open_order_count"] != 0:
        raise RuntimeError("official_open_orders_present")
    if Decimal(str(account["collateral_balance_usd"])) < target:
        raise RuntimeError("insufficient_official_collateral")

    metadata = {
        "intent_id": str(payload["intent_id"]),
        "prediction_id": str(payload["prediction_id"]),
        "paper_order_id": str(payload["paper_order_id"]),
        "authorization_id": str(payload["authorization_id"]),
        "request_sha256": request_sha256,
        "executor_sha256": _executor_sha256(),
        "account_preflight": account,
    }

    _consume_one_shot_arm()
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
            **metadata,
        }

    if isinstance(response, polymarket.RejectedOrder):
        return {
            "accepted": False,
            "external_order_id": None,
            "status": "rejected",
            "code": str(response.code),
            "message": str(response.message),
            "geoblock": geo,
            **metadata,
        }
    if not isinstance(response, polymarket.AcceptedOrder):
        return {
            "accepted": False,
            "external_order_id": None,
            "status": "error",
            "code": "unexpected_sdk_response",
            "message": "Polymarket SDK returned an unexpected order response",
            "geoblock": geo,
            **metadata,
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
        **metadata,
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
