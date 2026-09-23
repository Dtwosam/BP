from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from bp_engine.config import Settings, TradingMode
from bp_engine.execution.live_client import OfficialPolymarketTradingClient
from bp_engine.live_readiness.geoblock import GeoblockClient, GeoblockError
from bp_engine.live_readiness.interlock import (
    ActivationManifestError,
    kill_switch_engaged,
    load_activation_manifest,
)

CANARY_MAX_NOTIONAL_USD = Decimal("1.00")
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
        if not key:
            continue
        os.environ.setdefault(key, value.strip())


def _settings() -> Settings:
    path = _env_path()
    _load_runtime_environment()
    return Settings(_env_file=path)


def _expected_git_sha() -> str:
    value = os.environ.get("BP_CANARY_EXPECTED_GIT_SHA", "").strip().lower()
    if len(value) != 40:
        raise RuntimeError("BP_CANARY_EXPECTED_GIT_SHA is invalid")
    return value


def _geoblock(settings: Settings) -> dict[str, Any]:
    try:
        result = GeoblockClient(url=settings.polymarket_geoblock_url).check()
    except GeoblockError as exc:
        raise RuntimeError("direct geoblock check failed") from exc
    if result.blocked:
        raise RuntimeError("execution host is geoblocked")
    return {
        "blocked": False,
        "country": result.country,
        "region": result.region,
    }


def _activation(expected_git_sha: str) -> dict[str, Any]:
    try:
        manifest = load_activation_manifest(
            CANARY_ACTIVATION_PATH,
            expected_git_sha=expected_git_sha,
            observed_at=datetime.now(UTC),
        )
    except ActivationManifestError as exc:
        raise RuntimeError("canary activation manifest is invalid") from exc
    return {
        "authorized": manifest.authorized,
        "git_sha": manifest.git_sha,
        "authorization_id": manifest.authorization_id,
        "expires_at": manifest.expires_at.isoformat(),
    }


def _wallet_configured(settings: Settings) -> bool:
    private_key = os.environ.get(settings.polymarket_private_key_env, "").strip()
    return bool(private_key)


def _base_health(*, settings: Settings, expected_git_sha: str) -> dict[str, Any]:
    geo = _geoblock(settings)
    activation = _activation(expected_git_sha)
    kill = kill_switch_engaged(CANARY_KILL_SWITCH_PATH)
    consumed = Path(CANARY_RESERVATION_PATH).exists() or Path(
        CANARY_RECEIPT_PATH
    ).exists()
    return {
        "ok": (
            settings.mode is TradingMode.LIVE
            and settings.live_trading_enabled
            and not kill
            and not consumed
            and _wallet_configured(settings)
        ),
        "mode": settings.mode.value,
        "live_trading_enabled": settings.live_trading_enabled,
        "kill_switch_engaged": kill,
        "canary_consumed": consumed,
        "wallet_configured": _wallet_configured(settings),
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


def _submit(payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    health = _base_health(settings=settings, expected_git_sha=_expected_git_sha())
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
    if notional > CANARY_MAX_NOTIONAL_USD:
        raise RuntimeError("one-dollar canary notional exceeded")

    reservation = {
        "reserved_at": datetime.now(UTC).isoformat(),
        "token_id": token_id,
        "price": str(price),
        "size": str(size),
        "notional": str(notional),
    }
    _reserve_once(reservation)

    client = OfficialPolymarketTradingClient.create_from_environment(
        settings=settings
    )
    result = client.submit_limit_buy(
        token_id=token_id,
        price=price,
        size=size,
    )
    receipt = {
        **reservation,
        "accepted": result.accepted,
        "external_order_id": result.external_order_id,
        "status": result.status,
        "code": result.code,
        "message": result.message,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    _write_receipt(receipt)
    return receipt


def _cancel(payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    if kill_switch_engaged(CANARY_KILL_SWITCH_PATH):
        raise RuntimeError("canary kill switch is engaged")
    receipt_path = Path(CANARY_RECEIPT_PATH)
    if not receipt_path.is_file():
        raise RuntimeError("canary receipt is missing")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    external_order_id = str(payload.get("external_order_id") or "").strip()
    if not external_order_id or external_order_id != receipt.get("external_order_id"):
        raise RuntimeError("cancel order id does not match canary receipt")

    _geoblock(settings)
    client = OfficialPolymarketTradingClient.create_from_environment(
        settings=settings
    )
    result = client.cancel(external_order_id=external_order_id)
    return {
        "cancelled": result.cancelled,
        "external_order_id": result.external_order_id,
        "status": result.status,
        "message": result.message,
    }


def execute(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("request must be an object")
    settings = _settings()
    action = str(payload.get("action") or "").strip()
    if action == "health":
        return _base_health(settings=settings, expected_git_sha=_expected_git_sha())
    if action == "submit":
        return _submit(payload, settings)
    if action == "cancel":
        return _cancel(payload, settings)
    raise ValueError("unsupported canary action")


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
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
