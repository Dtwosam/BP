from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from google.cloud import pubsub_v1

from bp_engine.execution.telegram_pubsub import (
    MAX_ENVELOPE_BYTES,
    PubSubTransportError,
    envelope_attributes,
)
from bp_engine.execution.telegram_transport import (
    TransportError,
    load_transport_key_file,
    payload_sha256,
    verify_transport_envelope,
)


class StreamingMessage(Protocol):
    data: bytes
    attributes: Mapping[str, str]
    message_id: str

    def ack(self) -> None: ...

    def nack(self) -> None: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _ensure_private_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, mode=0o700)
    except FileExistsError:
        pass
    try:
        info = path.lstat()
    except OSError as exc:
        raise TransportError("streaming receiver directory is not accessible") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise TransportError("streaming receiver directory must be a non-symlink directory")
    os.chmod(path, 0o700)


def _write_new_json(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = (
        json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    )
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise TransportError(f"{path.name} already exists") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _load_json_file(path: Path, *, max_bytes: int) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise TransportError(f"{path.name} is not readable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise TransportError(f"{path.name} must be a regular non-symlink file")
    if info.st_size <= 0 or info.st_size > max_bytes:
        raise TransportError(f"{path.name} size invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransportError(f"{path.name} JSON invalid") from exc
    if not isinstance(payload, dict):
        raise TransportError(f"{path.name} must contain a JSON object")
    return payload


def _decode_message(message: StreamingMessage) -> dict[str, Any]:
    raw = bytes(message.data)
    if not raw or len(raw) > MAX_ENVELOPE_BYTES:
        raise PubSubTransportError("Pub/Sub streaming message data size invalid")
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PubSubTransportError(
            "Pub/Sub streaming message envelope JSON invalid"
        ) from exc
    if not isinstance(envelope, dict):
        raise PubSubTransportError("Pub/Sub streaming message envelope must be an object")
    expected_attributes = envelope_attributes(envelope)
    actual_attributes = {
        str(key): str(value) for key, value in dict(message.attributes).items()
    }
    if actual_attributes != expected_attributes:
        raise PubSubTransportError(
            "Pub/Sub streaming message attributes do not match envelope"
        )
    return envelope


def _message_key(message_id: str, data: bytes) -> str:
    if message_id:
        return hashlib.sha256(message_id.encode()).hexdigest()
    return hashlib.sha256(data).hexdigest()


def _write_rejection(
    *,
    rejection_dir: Path,
    message: StreamingMessage,
    error: Exception,
    observed_at: datetime,
) -> Path:
    _ensure_private_directory(rejection_dir)
    raw = bytes(message.data)
    path = rejection_dir / f"{_message_key(message.message_id, raw)}.json"
    payload = {
        "schema_version": 1,
        "status": "rejected",
        "pubsub_message_id": str(message.message_id or ""),
        "data_sha256": hashlib.sha256(raw).hexdigest(),
        "error_type": type(error).__name__,
        "error": str(error)[:512],
        "rejected_at": observed_at.astimezone(UTC).isoformat(),
        "executor_invoked": False,
        "real_order_submitted": False,
    }
    if path.exists():
        existing = _load_json_file(path, max_bytes=64 * 1024)
        if (
            existing.get("data_sha256") != payload["data_sha256"]
            or existing.get("status") != "rejected"
        ):
            raise TransportError("streaming rejection receipt conflict")
        return path
    _write_new_json(path, payload)
    return path


def _persist_verified_envelope(
    *,
    inbox_dir: Path,
    envelope: Mapping[str, Any],
    verified: Mapping[str, Any],
) -> tuple[Path, bool]:
    _ensure_private_directory(inbox_dir)
    identity = hashlib.sha256(
        f"{verified['intent_id']}\0{verified['request_sha256']}".encode()
    ).hexdigest()
    path = inbox_dir / f"{identity}.json"
    envelope_sha = payload_sha256(envelope)
    if path.exists():
        existing = _load_json_file(path, max_bytes=MAX_ENVELOPE_BYTES)
        if payload_sha256(existing) != envelope_sha:
            raise TransportError(
                "streaming inbox conflicts with existing exact-order envelope"
            )
        return path, True
    try:
        _write_new_json(path, envelope)
    except TransportError:
        if not path.exists():
            raise
        existing = _load_json_file(path, max_bytes=MAX_ENVELOPE_BYTES)
        if payload_sha256(existing) != envelope_sha:
            raise TransportError("streaming inbox write race conflict") from None
        return path, True
    return path, False


def process_message(
    message: StreamingMessage,
    *,
    key_path: Path,
    expected_key_id: str,
    inbox_dir: Path,
    rejection_dir: Path,
    observed_at: datetime,
) -> dict[str, Any]:
    try:
        envelope = _decode_message(message)
    except PubSubTransportError as exc:
        try:
            rejection_path = _write_rejection(
                rejection_dir=rejection_dir,
                message=message,
                error=exc,
                observed_at=observed_at,
            )
        except (OSError, TransportError):
            message.nack()
            return {
                "status": "rejection_not_persisted_nacked",
                "executor_invoked": False,
                "real_order_submitted": False,
            }
        message.ack()
        return {
            "status": "invalid_acknowledged",
            "rejection_path": str(rejection_path),
            "executor_invoked": False,
            "real_order_submitted": False,
        }

    try:
        key = load_transport_key_file(key_path)
    except TransportError:
        message.nack()
        return {
            "status": "local_key_unavailable_nacked",
            "executor_invoked": False,
            "real_order_submitted": False,
        }

    try:
        verified = verify_transport_envelope(
            envelope,
            key=key,
            expected_key_id=expected_key_id,
            observed_at=observed_at,
        )
    except TransportError as exc:
        if str(exc) == "transport key id mismatch":
            message.nack()
            return {
                "status": "key_id_mismatch_nacked",
                "executor_invoked": False,
                "real_order_submitted": False,
            }
        try:
            rejection_path = _write_rejection(
                rejection_dir=rejection_dir,
                message=message,
                error=exc,
                observed_at=observed_at,
            )
        except (OSError, TransportError):
            message.nack()
            return {
                "status": "rejection_not_persisted_nacked",
                "executor_invoked": False,
                "real_order_submitted": False,
            }
        message.ack()
        return {
            "status": "invalid_acknowledged",
            "rejection_path": str(rejection_path),
            "executor_invoked": False,
            "real_order_submitted": False,
        }

    try:
        envelope_path, duplicate = _persist_verified_envelope(
            inbox_dir=inbox_dir,
            envelope=envelope,
            verified=verified,
        )
    except (OSError, TransportError):
        message.nack()
        return {
            "status": "persistence_failed_nacked",
            "intent_id": verified["intent_id"],
            "request_sha256": verified["request_sha256"],
            "executor_invoked": False,
            "real_order_submitted": False,
        }

    message.ack()
    return {
        "status": "duplicate_acknowledged" if duplicate else "received_acknowledged",
        "key_id": verified["key_id"],
        "intent_id": verified["intent_id"],
        "request_sha256": verified["request_sha256"],
        "prepared_sha256": verified["prepared_sha256"],
        "envelope_path": str(envelope_path),
        "executor_invoked": False,
        "real_order_submitted": False,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Continuously receive BP Telegram envelopes using Pub/Sub StreamingPull."
    )
    parser.add_argument(
        "--inbox-dir",
        type=Path,
        default=Path("/var/lib/bp-canary/telegram-transport-inbox"),
    )
    parser.add_argument(
        "--rejection-dir",
        type=Path,
        default=Path("/var/lib/bp-canary/telegram-transport-rejections"),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if os.environ.get("BP_TELEGRAM_PUBSUB_STREAMING_RECEIVE_ENABLED", "no") != "yes":
        raise SystemExit("Telegram Pub/Sub StreamingPull receiving is not enabled")
    if os.environ.get("MODE") != "research":
        raise SystemExit("MODE must be research")
    if os.environ.get("LIVE_TRADING_ENABLED") != "false":
        raise SystemExit("LIVE_TRADING_ENABLED must be false")
    if os.environ.get("MAX_TRADE_SIZE_USD") != "0":
        raise SystemExit("MAX_TRADE_SIZE_USD must be 0")
    if os.environ.get("MAX_DAILY_LOSS_USD") != "0":
        raise SystemExit("MAX_DAILY_LOSS_USD must be 0")
    for forbidden in (
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
        "BP_TELEGRAM_BOT_TOKEN",
    ):
        if os.environ.get(forbidden):
            raise SystemExit(f"{forbidden} must not be present in StreamingPull receiver")

    key_path_raw = os.environ.get("BP_TELEGRAM_TRANSPORT_KEY_FILE", "").strip()
    key_id = os.environ.get("BP_TELEGRAM_TRANSPORT_KEY_ID", "").strip()
    project_id = os.environ.get("BP_TELEGRAM_PUBSUB_PROJECT_ID", "").strip()
    subscription_id = os.environ.get(
        "BP_TELEGRAM_PUBSUB_SUBSCRIPTION_ID", ""
    ).strip()
    if not key_path_raw or not key_id or not project_id or not subscription_id:
        raise SystemExit("Telegram Pub/Sub StreamingPull configuration incomplete")

    key_path = Path(key_path_raw)
    load_transport_key_file(key_path)
    _ensure_private_directory(args.inbox_dir)
    _ensure_private_directory(args.rejection_dir)

    subscriber = pubsub_v1.SubscriberClient()
    subscription_path = subscriber.subscription_path(project_id, subscription_id)
    flow_control = pubsub_v1.types.FlowControl(
        max_messages=1,
        max_bytes=MAX_ENVELOPE_BYTES,
    )

    def callback(message: StreamingMessage) -> None:
        result = process_message(
            message,
            key_path=key_path,
            expected_key_id=key_id,
            inbox_dir=args.inbox_dir,
            rejection_dir=args.rejection_dir,
            observed_at=_utc_now(),
        )
        print(json.dumps(result, sort_keys=True), flush=True)

    future = subscriber.subscribe(
        subscription_path,
        callback=callback,
        flow_control=flow_control,
    )
    try:
        future.result()
    except KeyboardInterrupt:
        future.cancel()
    finally:
        subscriber.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
