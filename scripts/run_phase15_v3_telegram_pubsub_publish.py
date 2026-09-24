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
    metadata_access_token,
    publish_envelope,
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
        raise TransportError("publish receipt directory must be a non-symlink directory")
    os.chmod(path, 0o700)


def _load_json_file(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise TransportError("transport envelope file is not readable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise TransportError("transport envelope must be a regular non-symlink file")
    if info.st_size <= 0 or info.st_size > MAX_ENVELOPE_FILE_BYTES:
        raise TransportError("transport envelope file size invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransportError("transport envelope JSON invalid") from exc
    if not isinstance(payload, Mapping):
        raise TransportError("transport envelope must contain a JSON object")
    return dict(payload)


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
        raise TransportError("publish receipt already exists") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _existing_receipt(path: Path, envelope_sha256: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = _load_json_file(path)
    if payload.get("status") != "published":
        raise TransportError("existing publish receipt has unexpected status")
    if str(payload.get("envelope_sha256") or "") != envelope_sha256:
        raise TransportError("existing publish receipt envelope mismatch")
    return payload


def publish_transport(
    *,
    client: httpx.Client,
    envelope_path: Path,
    key_path: Path,
    expected_key_id: str,
    project_id: str,
    topic_id: str,
    receipt_dir: Path,
    observed_at: datetime,
) -> dict[str, Any]:
    envelope = _load_json_file(envelope_path)
    key = load_transport_key_file(key_path)
    verified = verify_transport_envelope(
        envelope,
        key=key,
        expected_key_id=expected_key_id,
        observed_at=observed_at,
    )
    envelope_sha = payload_sha256(envelope)
    _ensure_private_directory(receipt_dir)
    identity = hashlib.sha256(
        f"{verified['intent_id']}\0{verified['request_sha256']}".encode()
    ).hexdigest()
    receipt_path = receipt_dir / f"{identity}.json"
    existing = _existing_receipt(receipt_path, envelope_sha)
    if existing is not None:
        return {
            **existing,
            "status": "already_published",
            "network_publish_attempted": False,
        }

    token = metadata_access_token(client)
    published = publish_envelope(
        client,
        project_id=project_id,
        topic_id=topic_id,
        access_token=token,
        envelope=envelope,
    )
    receipt = {
        "schema_version": 1,
        "status": "published",
        "key_id": verified["key_id"],
        "intent_id": verified["intent_id"],
        "request_sha256": verified["request_sha256"],
        "prepared_sha256": verified["prepared_sha256"],
        "envelope_sha256": published["envelope_sha256"],
        "pubsub_message_id": published["message_id"],
        "published_at": observed_at.astimezone(UTC).isoformat(),
        "delivery_retry_safe": True,
        "executor_invoked": False,
        "real_order_submitted": False,
    }
    try:
        _write_new_json(receipt_path, receipt)
    except TransportError:
        existing = _existing_receipt(receipt_path, envelope_sha)
        if existing is None:
            raise
        return {
            **existing,
            "status": "published_receipt_race",
            "network_publish_attempted": True,
        }
    return {
        **receipt,
        "receipt_path": str(receipt_path),
        "network_publish_attempted": True,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish one prebuilt BP Telegram transport envelope to Pub/Sub."
    )
    parser.add_argument("envelope_path", type=Path)
    parser.add_argument(
        "--receipt-dir",
        type=Path,
        default=Path("/var/lib/bp/phase15-canary-telegram-transport/published"),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if os.environ.get("BP_TELEGRAM_PUBSUB_PUBLISH_ENABLED", "no") != "yes":
        raise SystemExit("Telegram Pub/Sub publishing is not enabled")
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
            raise SystemExit(f"{forbidden} must not be present in Pub/Sub publisher")

    key_path_raw = os.environ.get("BP_TELEGRAM_TRANSPORT_KEY_FILE", "").strip()
    key_id = os.environ.get("BP_TELEGRAM_TRANSPORT_KEY_ID", "").strip()
    project_id = os.environ.get("BP_TELEGRAM_PUBSUB_PROJECT_ID", "").strip()
    topic_id = os.environ.get("BP_TELEGRAM_PUBSUB_TOPIC_ID", "").strip()
    if not key_path_raw or not key_id or not project_id or not topic_id:
        raise SystemExit("Telegram Pub/Sub publisher configuration incomplete")

    try:
        with httpx.Client() as client:
            result = publish_transport(
                client=client,
                envelope_path=args.envelope_path,
                key_path=Path(key_path_raw),
                expected_key_id=key_id,
                project_id=project_id,
                topic_id=topic_id,
                receipt_dir=args.receipt_dir,
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
