from __future__ import annotations

import fcntl
import json
import os
import sys
import time
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
from bp_engine.live_readiness.secrets import secret_metadata

CANARY_MAX_NOTIONAL_USD = Decimal("5.00")
CANARY_FEE_RATE = Decimal("0.07")
CANARY_MIN_SHARES = Decimal("5")
CANARY_TTL_MS = 2000


def _write(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _decimal(value: object, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _required_path(name: str) -> Path:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return Path(value)


def _expected_git_sha() -> str:
    value = os.environ.get("BP_CANARY_EXPECTED_GIT_SHA", "").strip().lower()
    if len(value) != 40 or any(ch not in "0123456789abcdef" for ch in value):
        raise RuntimeError("BP_CANARY_EXPECTED_GIT_SHA is invalid")
    return value


def _base_guard(
    settings: Settings,
    *,
    require_activation: bool,
) -> dict[str, object]:
    if settings.mode != TradingMode.LIVE:
        raise RuntimeError("mode_not_live")
    if settings.live_trading_enabled is not True:
        raise RuntimeError("live_trading_disabled")
    expected = {
        "max_trade_size_usd": Decimal("5"),
        "max_total_exposure_usd": Decimal("5"),
        "max_daily_loss_usd": Decimal("5"),
        "max_consecutive_losses": 1,
        "live_min_edge": Decimal("0.075"),
    }
    if Decimal(str(settings.max_trade_size_usd)) != expected["max_trade_size_usd"]:
        raise RuntimeError("trade_size_limit_drift")
    if Decimal(str(settings.max_total_exposure_usd)) != expected["max_total_exposure_usd"]:
        raise RuntimeError("total_exposure_limit_drift")
    if Decimal(str(settings.max_daily_loss_usd)) != expected["max_daily_loss_usd"]:
        raise RuntimeError("daily_loss_limit_drift")
    if int(settings.max_consecutive_losses) != expected["max_consecutive_losses"]:
        raise RuntimeError("consecutive_loss_limit_drift")
    if Decimal(str(settings.live_min_edge)) != expected["live_min_edge"]:
        raise RuntimeError("min_edge_drift")

    if require_activation:
        try:
            manifest = load_activation_manifest(
                settings.live_activation_manifest_path,
                expected_git_sha=_expected_git_sha(),
                observed_at=datetime.now(UTC),
            )
        except ActivationManifestError as exc:
            raise RuntimeError("activation_manifest_invalid") from exc
        authorization_id = manifest.authorization_id
    else:
        authorization_id = None

    try:
        geoblock = GeoblockClient(url=settings.polymarket_geoblock_url).check()
    except GeoblockError as exc:
        raise RuntimeError("geoblock_check_failed") from exc
    if geoblock.blocked:
        raise RuntimeError("geoblock_blocked")

    metadata = secret_metadata(
        private_key_env=settings.polymarket_private_key_env,
        wallet_env=settings.polymarket_wallet_address_env,
    )
    if not metadata.private_key_configured:
        raise RuntimeError("private_key_not_configured")
    try:
        OfficialPolymarketTradingClient.create_from_environment(settings=settings)
    except RuntimeError as exc:
        raise RuntimeError("sdk_client_not_ready") from exc

    return {
        "authorization_id": authorization_id,
        "blocked": False,
        "country": geoblock.country,
        "region": geoblock.region,
        "private_key_configured": True,
        "sdk_client_ready": True,
        "wallet_configured": metadata.wallet_configured,
        "wallet_fingerprint": metadata.wallet_fingerprint,
    }


def _atomic_marker(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def _touch_kill(path: Path, reason: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(reason + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def _health(settings: Settings) -> dict[str, object]:
    guard = _base_guard(settings, require_activation=True)
    attempted = _required_path("BP_CANARY_ATTEMPTED_PATH").exists()
    kill = kill_switch_engaged(settings.live_kill_switch_path)
    return {
        "ok": not attempted and not kill,
        **guard,
        "canary_attempted": attempted,
        "kill_switch_engaged": kill,
    }


def _submit(settings: Settings, payload: dict[str, Any]) -> dict[str, object]:
    lock_path = _required_path("BP_CANARY_EXECUTOR_LOCK_PATH")
    attempted_path = _required_path("BP_CANARY_ATTEMPTED_PATH")
    kill_path = Path(settings.live_kill_switch_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        guard = _base_guard(settings, require_activation=True)
        if kill_switch_engaged(str(kill_path)):
            raise RuntimeError("kill_switch_engaged")
        if attempted_path.exists():
            raise RuntimeError("canary_already_attempted")

        token_id = str(payload.get("token_id") or "").strip()
        if not token_id:
            raise ValueError("token_id is required")
        price = _decimal(payload.get("price"), "price")
        size = _decimal(payload.get("size"), "size")
        ttl_ms = int(payload.get("ttl_ms", -1))
        if not Decimal("0") < price <= Decimal("1"):
            raise ValueError("price must be within (0, 1]")
        if size < CANARY_MIN_SHARES:
            raise ValueError("size is below frozen canary minimum")
        if ttl_ms != CANARY_TTL_MS:
            raise ValueError("ttl_ms changed")
        worst_fee_per_share = CANARY_FEE_RATE * price * (Decimal("1") - price)
        worst_total = size * (price + worst_fee_per_share)
        if worst_total > CANARY_MAX_NOTIONAL_USD:
            raise ValueError("canary notional exceeds $5 worst-case limit")

        attempted_at = datetime.now(UTC)
        _atomic_marker(
            attempted_path,
            {
                "attempted_at": attempted_at.isoformat(),
                "authorization_id": guard["authorization_id"],
                "worst_total_usd": str(worst_total),
            },
        )
        # Engage the remote kill switch before the external side effect. The
        # current locked invocation is the only allowed submission attempt.
        _touch_kill(kill_path, "single-order canary attempt consumed")

        client = OfficialPolymarketTradingClient.create_from_environment(settings=settings)
        result = client.submit_limit_buy(token_id=token_id, price=price, size=size)
        cancel_payload: dict[str, object] | None = None
        if result.accepted and result.external_order_id:
            time.sleep(CANARY_TTL_MS / 1000)
            cancelled = client.cancel(external_order_id=result.external_order_id)
            cancel_payload = {
                "cancelled": cancelled.cancelled,
                "status": cancelled.status,
                "message": cancelled.message,
            }
        message = result.message
        if cancel_payload is not None:
            message = json.dumps(
                {
                    "submit_message": result.message,
                    "post_submit_cancel": cancel_payload,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        return {
            "ok": True,
            "accepted": result.accepted,
            "external_order_id": result.external_order_id,
            "status": result.status,
            "code": result.code,
            "message": message,
        }


def _cancel(settings: Settings, payload: dict[str, Any]) -> dict[str, object]:
    # Cancellation is risk-reducing and remains available after the canary
    # submission kill switch is engaged.
    _base_guard(settings, require_activation=False)
    external_order_id = str(payload.get("external_order_id") or "").strip()
    if not external_order_id:
        raise ValueError("external_order_id is required")
    client = OfficialPolymarketTradingClient.create_from_environment(settings=settings)
    result = client.cancel(external_order_id=external_order_id)
    return {
        "ok": True,
        "cancelled": result.cancelled,
        "status": result.status,
        "message": result.message,
    }


def main() -> int:
    try:
        raw = sys.stdin.read(16_385)
        if len(raw) > 16_384:
            raise ValueError("request too large")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("request must be an object")
        settings = Settings(_env_file=None)
        operation = payload.get("operation")
        if operation == "health":
            result = _health(settings)
        elif operation == "submit_limit_buy":
            result = _submit(settings, payload)
        elif operation == "cancel":
            result = _cancel(settings, payload)
        else:
            raise ValueError("unsupported operation")
        _write(result)
        return 0
    except Exception as exc:
        _write(
            {
                "ok": False,
                "error": str(exc)[:160],
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
