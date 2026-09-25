from __future__ import annotations

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


def ensure_private_directory(path: Path, *, label: str) -> None:
    try:
        path.mkdir(parents=True, mode=0o700)
    except FileExistsError:
        pass
    try:
        info = path.lstat()
    except OSError as exc:
        raise TransportError(f"{label} is not accessible") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise TransportError(f"{label} must be a non-symlink directory")
    os.chmod(path, 0o700)


def load_envelope_file(path: Path) -> dict[str, Any]:
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


def write_new_json(path: Path, payload: Mapping[str, Any], *, label: str) -> None:
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
        raise TransportError(f"{label} already exists") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _existing_receipt(path: Path, envelope_sha256: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = load_envelope_file(path)
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
    envelope = load_envelope_file(envelope_path)
    key = load_transport_key_file(key_path)
    verified = verify_transport_envelope(
        envelope,
        key=key,
        expected_key_id=expected_key_id,
        observed_at=observed_at,
    )
    envelope_sha = payload_sha256(envelope)
    ensure_private_directory(receipt_dir, label="publish receipt directory")
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
        write_new_json(receipt_path, receipt, label="publish receipt")
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
