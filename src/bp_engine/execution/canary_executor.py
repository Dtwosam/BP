from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import polymarket

CANARY_MAX_NOTIONAL_USD = Decimal("1.00")
CANARY_FEE_RATE = Decimal("0.07")
CANARY_RECEIPT_PATH = "/var/lib/bp-exec/first-submit.json"
CANARY_RESERVATION_PATH = "/var/lib/bp-exec/first-submit.reserved"
CANARY_KILL_SWITCH_PATH = "/var/lib/bp-exec/KILL"
CANARY_ACTIVATION_PATH = "/var/lib/bp-exec/activation.json"


def _decimal(value: object, name: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not number.is_finite():
        raise ValueError(f"{name} must be finite")
    return number


def _env_path() -> str:
    return os.environ.get("BP_CANARY_ENV_FILE", "/etc/bp-exec/canary.env")


def _load_runtime_environment() -> None:
    path = Path(_env_path())
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            os.environ.setdefault(key, value.strip())


def _expected_git_sha() -> str:
    value = os.environ.get("BP_CANARY_EXPECTED_GIT_SHA", "").strip().lower()
    if len(value) != 40 or any(ch not in "0123456789abcdef" for ch in value):
        raise RuntimeError("BP_CANARY_EXPECTED_GIT_SHA is invalid")
    return value


def _activation_sha(git_sha: str) -> str:
    return hashlib.sha256(git_sha.encode("ascii")).hexdigest()


def _activation(expected_git_sha: str) -> dict[str, Any]:
    payload = json.loads(Path(CANARY_ACTIVATION_PATH).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("authorized") is not True:
        raise RuntimeError("canary activation manifest is invalid")
    expected = _activation_sha(expected_git_sha)
    if payload.get("git_sha") != expected:
        raise RuntimeError("canary activation manifest does not match code")
    issued_at = datetime.fromisoformat(str(payload["issued_at"]))
    expires_at = datetime.fromisoformat(str(payload["expires_at"]))
    if issued_at.tzinfo is None or expires_at.tzinfo is None:
        raise RuntimeError("canary activation timestamps must be timezone-aware")
    now = datetime.now(UTC)
    if issued_at.astimezone(UTC) > now or now >= expires_at.astimezone(UTC):
        raise RuntimeError("canary activation manifest is outside its validity window")
    authorization_id = str(payload.get("authorization_id") or "").strip()
    if not authorization_id:
        raise RuntimeError("canary activation authorization_id is missing")
    return {
        "authorized": True,
        "git_sha": expected,
        "authorization_id": authorization_id,
        "expires_at": expires_at.astimezone(UTC).isoformat(),
    }


def _geoblock() -> dict[str, Any]:
    url = os.environ.get(
        "POLYMARKET_GEOBLOCK_URL",
        "https://polymarket.com/api/geoblock",
    )
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "BP-phase15-one-dollar-canary/1"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError("direct geoblock check returned non-success")
        payload = json.loads(response.read().decode("utf-8"))
    if type(payload.get("blocked")) is not bool:
        raise RuntimeError("direct geoblock check returned invalid blocked")
    if not isinstance(payload.get("country"), str) or not isinstance(
        payload.get("region"), str
    ):
        raise RuntimeError("direct geoblock check returned invalid location")
    if payload["blocked"]:
        raise RuntimeError("execution host is geoblocked")
    return {
        "blocked": False,
        "country": payload["country"],
        "region": payload["region"],
        "direct_url": url,
    }


def _kill_switch_engaged() -> bool:
    try:
        return Path(CANARY_KILL_SWITCH_PATH).exists()
    except OSError:
        return True


def _wallet_configured() -> bool:
    return bool(os.environ.get("POLYMARKET_PRIVATE_KEY", "").strip())


def _create_client():
    private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "").strip()
    wallet = os.environ.get("POLYMARKET_WALLET_ADDRESS", "").strip()
    if not private_key:
        raise RuntimeError("Polymarket private key is not configured")
    kwargs: dict[str, str] = {"private_key": private_key}
    if wallet:
        kwargs["wallet"] = wallet
    try:
        return polymarket.SecureClient.create(**kwargs)
    except Exception as exc:
        raise RuntimeError("failed to create official Polymarket SDK client") from exc


def _base_health(*, expected_git_sha: str) -> dict[str, Any]:
    geo = _geoblock()
    activation = _activation(expected_git_sha)
    kill = _kill_switch_engaged()
    consumed = Path(CANARY_RESERVATION_PATH).exists() or Path(
        CANARY_RECEIPT_PATH
    ).exists()
    mode = os.environ.get("MODE", "").strip().lower()
    live = os.environ.get("LIVE_TRADING_ENABLED", "").strip().lower() == "true"
    wallet_configured = _wallet_configured()
    return {
        "ok": (
            mode == "live"
            and live
            and not kill
            and not consumed
            and wallet_configured
        ),
        "mode": mode,
        "live_trading_enabled": live,
        "kill_switch_engaged": kill,
        "canary_consumed": consumed,
        "wallet_configured": wallet_configured,
        "geoblock": geo,
        "activation": activation,
    }


def _reserve_once(payload: dict[str, Any]) -> None:
    path = Path(CANARY_RESERVATION_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise RuntimeError("one-dollar canary submit already consumed") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
        handle.write("\n")


def _write_receipt(payload: dict[str, Any]) -> None:
    target = Path(CANARY_RECEIPT_PATH)
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, target)


def _submit(payload: dict[str, Any], expected_git_sha: str) -> dict[str, Any]:
    health = _base_health(expected_git_sha=expected_git_sha)
    if not health["ok"]:
        raise RuntimeError("remote executor is not eligible")

    token_id = str(payload.get("token_id") or "").strip()
    price = _decimal(payload.get("price"), "price")
    size = _decimal(payload.get("size"), "size")
    if not token_id:
        raise ValueError("token_id must not be blank")
    if not Decimal("0") < price <= Decimal("1"):
        raise ValueError("price must be within (0, 1]")
    if size <= 0:
        raise ValueError("size must be positive")
    notional = price * size
    fee = CANARY_FEE_RATE * price * (Decimal("1") - price) * size
    total_cost = notional + fee
    if total_cost > CANARY_MAX_NOTIONAL_USD:
        raise RuntimeError("one-dollar canary total cost exceeded")

    reservation = {
        "reserved_at": datetime.now(UTC).isoformat(),
        "token_id": token_id,
        "price": str(price),
        "size": str(size),
        "notional": str(notional),
        "fee_cap_cost": str(fee),
        "total_cost_cap": str(total_cost),
    }
    _reserve_once(reservation)

    client = _create_client()
    try:
        signed_order = client.create_limit_order(
            token_id=token_id,
            price=price,
            size=size,
            side="BUY",
        )
        response = client.post_order(signed_order)
    except Exception:
        result = {
            "accepted": False,
            "external_order_id": None,
            "status": "error",
            "code": "sdk_exception",
            "message": "Polymarket SDK order submission failed",
        }
    else:
        if isinstance(response, polymarket.AcceptedOrder):
            result = {
                "accepted": True,
                "external_order_id": str(response.order_id),
                "status": str(response.status),
                "code": "accepted",
                "message": "",
            }
        elif isinstance(response, polymarket.RejectedOrder):
            result = {
                "accepted": False,
                "external_order_id": None,
                "status": "rejected",
                "code": str(response.code),
                "message": str(response.message),
            }
        else:
            result = {
                "accepted": False,
                "external_order_id": None,
                "status": "error",
                "code": "unexpected_sdk_response",
                "message": "Polymarket SDK returned an unexpected order response",
            }

    receipt = {
        **reservation,
        **result,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    _write_receipt(receipt)
    return receipt


def _cancel(payload: dict[str, Any]) -> dict[str, Any]:
    receipt_path = Path(CANARY_RECEIPT_PATH)
    if not receipt_path.is_file():
        raise RuntimeError("canary receipt is missing")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    external_order_id = str(payload.get("external_order_id") or "").strip()
    if not external_order_id or external_order_id != receipt.get("external_order_id"):
        raise RuntimeError("cancel order id does not match canary receipt")

    _geoblock()
    client = _create_client()
    try:
        response = client.cancel_order(order_id=external_order_id)
    except Exception:
        return {
            "cancelled": False,
            "external_order_id": external_order_id,
            "status": "error",
            "message": "Polymarket SDK cancellation failed",
        }
    if isinstance(response, polymarket.CancelOrdersResponse):
        if external_order_id in response.canceled:
            return {
                "cancelled": True,
                "external_order_id": external_order_id,
                "status": "cancelled",
                "message": "",
            }
        if external_order_id in response.not_canceled:
            return {
                "cancelled": False,
                "external_order_id": external_order_id,
                "status": "not_cancelled",
                "message": str(response.not_canceled[external_order_id]),
            }
    return {
        "cancelled": False,
        "external_order_id": external_order_id,
        "status": "error",
        "message": "Polymarket SDK returned unexpected cancellation response",
    }


def execute(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("request must be an object")
    _load_runtime_environment()
    expected_git_sha = _expected_git_sha()
    action = str(payload.get("action") or "").strip()
    if action == "health":
        return _base_health(expected_git_sha=expected_git_sha)
    if action == "submit":
        return _submit(payload, expected_git_sha)
    if action == "cancel":
        return _cancel(payload)
    raise ValueError("unsupported canary action")


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        result = execute(payload)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "error": type(exc).__name__,
                    "message": str(exc),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
