from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from bp_engine.execution.telegram_pubsub import (
    PubSubTransportError,
    acknowledge,
    metadata_access_token,
    pull_one,
)
from bp_engine.execution.telegram_transport import (
    TransportError,
    load_transport_key_file,
    payload_sha256,
    verify_transport_envelope,
)

MAX_ENVELOPE_FILE_BYTES = 256 * 1024


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _ensure_private_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, mode=0o700)
    except FileExistsError:
        pass
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise TransportError("Pub/Sub inbox directory must be a non-symlink directory")
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


def _load_existing_envelope(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise TransportError("existing inbox envelope is not readable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise TransportError("existing inbox envelope must be a regular non-symlink file")
    if info.st_size <= 0 or info.st_size > MAX_ENVELOPE_FILE_BYTES:
        raise TransportError("existing inbox envelope size invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransportError("existing inbox envelope JSON invalid") from exc
    if not isinstance(payload, dict):
        raise TransportError("existing inbox envelope must contain an object")
    return payload


def _persist_envelope(
    *,
    inbox_dir: Path,
    envelope: Mapping[str, Any],
    envelope_sha256: str,
    identity: str,
) -> tuple[Path, bool]:
    _ensure_private_directory(inbox_dir)
    envelope_path = inbox_dir / f"{identity}.json"
    if envelope_path.exists():
        existing = _load_existing_envelope(envelope_path)
        if payload_sha256(existing) != envelope_sha256:
            raise TransportError("existing inbox envelope conflicts with exact order")
        return envelope_path, True
    try:
        _write_new_json(envelope_path, envelope)
    except TransportError:
        if not envelope_path.exists():
            raise
        existing = _load_existing_envelope(envelope_path)
        if payload_sha256(existing) != envelope_sha256:
            raise TransportError("inbox envelope write race conflict") from None
        return envelope_path, True
    return envelope_path, False


def _write_ack_receipt(
    *,
    receipt_dir: Path,
    message_id: str,
    envelope_sha256: str,
    intent_id: str,
    request_sha256: str,
    acknowledged_at: datetime,
) -> Path:
    _ensure_private_directory(receipt_dir)
    message_key = hashlib.sha256(message_id.encode()).hexdigest()
    path = receipt_dir / f"{message_key}.json"
    payload = {
        "schema_version": 1,
        "status": "acknowledged",
        "pubsub_message_id": message_id,
        "envelope_sha256": envelope_sha256,
        "intent_id": intent_id,
        "request_sha256": request_sha256,
        "acknowledged_at": acknowledged_at.astimezone(UTC).isoformat(),
        "executor_invoked": False,
        "real_order_submitted": False,
    }
    if path.exists():
        existing = _load_existing_envelope(path)
        if (
            existing.get("pubsub_message_id") != message_id
            or existing.get("envelope_sha256") != envelope_sha256
        ):
            raise TransportError("Pub/Sub acknowledgement receipt conflict")
        return path
    _write_new_json(path, payload)
    return path


def receive_transport(
    *,
    client: httpx.Client,
    key_path: Path,
    expected_key_id: str,
    project_id: str,
    subscription_id: str,
    inbox_dir: Path,
    observed_at: datetime,
) -> dict[str, Any]:
    token = metadata_access_token(client)
    pulled = pull_one(
        client,
        project_id=project_id,
        subscription_id=subscription_id,
        access_token=token,
    )
    if pulled is None:
        return {
            "status": "no_message",
            "executor_invoked": False,
            "real_order_submitted": False,
        }

    key = load_transport_key_file(key_path)
    verified = verify_transport_envelope(
        pulled.envelope,
        key=key,
        expected_key_id=expected_key_id,
        observed_at=observed_at,
    )
    identity = hashlib.sha256(
        f"{verified['intent_id']}\0{verified['request_sha256']}".encode()
    ).hexdigest()
    envelope_path, duplicate = _persist_envelope(
        inbox_dir=inbox_dir,
        envelope=pulled.envelope,
        envelope_sha256=pulled.envelope_sha256,
        identity=identity,
    )

    acknowledge(
        client,
        project_id=project_id,
        subscription_id=subscription_id,
        access_token=token,
        ack_id=pulled.ack_id,
    )
    ack_path = _write_ack_receipt(
        receipt_dir=inbox_dir / "acks",
        message_id=pulled.message_id,
        envelope_sha256=pulled.envelope_sha256,
        intent_id=verified["intent_id"],
        request_sha256=verified["request_sha256"],
        acknowledged_at=observed_at,
    )
    return {
        "status": "duplicate_acknowledged" if duplicate else "received_acknowledged",
        "key_id": verified["key_id"],
        "intent_id": verified["intent_id"],
        "request_sha256": verified["request_sha256"],
        "prepared_sha256": verified["prepared_sha256"],
        "envelope_sha256": pulled.envelope_sha256,
        "pubsub_message_id": pulled.message_id,
        "envelope_path": str(envelope_path),
        "ack_receipt_path": str(ack_path),
        "executor_invoked": False,
        "real_order_submitted": False,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Receive one BP Telegram Pub/Sub envelope into the local execution-host inbox."
        )
    )
    parser.add_argument(
        "--inbox-dir",
        type=Path,
        default=Path("/var/lib/bp-canary/telegram-transport-inbox"),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if os.environ.get("BP_TELEGRAM_PUBSUB_RECEIVE_ENABLED", "no") != "yes":
        raise SystemExit("Telegram Pub/Sub receiving is not enabled")
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
            raise SystemExit(f"{forbidden} must not be present in Pub/Sub receiver")

    key_path_raw = os.environ.get("BP_TELEGRAM_TRANSPORT_KEY_FILE", "").strip()
    key_id = os.environ.get("BP_TELEGRAM_TRANSPORT_KEY_ID", "").strip()
    project_id = os.environ.get("BP_TELEGRAM_PUBSUB_PROJECT_ID", "").strip()
    subscription_id = os.environ.get(
        "BP_TELEGRAM_PUBSUB_SUBSCRIPTION_ID", ""
    ).strip()
    if not key_path_raw or not key_id or not project_id or not subscription_id:
        raise SystemExit("Telegram Pub/Sub receiver configuration incomplete")

    try:
        with httpx.Client(trust_env=False) as client:
            result = receive_transport(
                client=client,
                key_path=Path(key_path_raw),
                expected_key_id=key_id,
                project_id=project_id,
                subscription_id=subscription_id,
                inbox_dir=args.inbox_dir,
                observed_at=_utc_now(),
            )
    except (TransportError, PubSubTransportError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error": str(exc),
                    "executor_invoked": False,
                    "real_order_submitted": False,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 1

    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
