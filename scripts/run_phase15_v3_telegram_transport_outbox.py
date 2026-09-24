#!/opt/bp/.venv/bin/python
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bp_engine.execution.telegram_transport import (
    TransportError,
    create_transport_envelope,
    load_transport_key_file,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a local BP Telegram transport envelope")
    parser.add_argument("prepared_path", type=Path)
    parser.add_argument("approval_path", type=Path)
    parser.add_argument(
        "--outbox-dir",
        type=Path,
        default=Path("/var/lib/bp/phase15-canary-telegram-transport/outbox"),
    )
    return parser.parse_args()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise TransportError(f"{path.name} is not readable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise TransportError(f"{path.name} must be a regular non-symlink file")
    if info.st_size <= 0 or info.st_size > 1_048_576:
        raise TransportError(f"{path.name} size invalid")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TransportError(f"{path.name} must contain a JSON object")
    return payload


def _ensure_private_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, mode=0o700)
    except FileExistsError:
        pass
    try:
        info = path.lstat()
    except OSError as exc:
        raise TransportError("transport outbox directory is not accessible") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise TransportError("transport outbox must be a non-symlink directory")
    os.chmod(path, 0o700)


def _write_once(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise TransportError("transport envelope already exists for exact order") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _utc_now() -> datetime:
    return datetime.now(UTC)


def main() -> int:
    args = _parse_args()
    if os.environ.get("BP_TELEGRAM_TRANSPORT_OUTBOX_ENABLED", "no") != "yes":
        raise SystemExit("Telegram transport outbox is not enabled")
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
            raise SystemExit(f"{forbidden} must not be present in transport outbox")

    key_path_raw = os.environ.get("BP_TELEGRAM_TRANSPORT_KEY_FILE", "").strip()
    if not key_path_raw:
        raise SystemExit("BP_TELEGRAM_TRANSPORT_KEY_FILE is required")
    key_id = os.environ.get("BP_TELEGRAM_TRANSPORT_KEY_ID", "").strip()
    if not key_id:
        raise SystemExit("BP_TELEGRAM_TRANSPORT_KEY_ID is required")
    key = load_transport_key_file(Path(key_path_raw))
    prepared = _load_json(args.prepared_path)
    approval = _load_json(args.approval_path)
    envelope = create_transport_envelope(
        prepared,
        approval=approval,
        key=key,
        key_id=key_id,
        created_at=_utc_now(),
        nonce=secrets.token_urlsafe(18),
    )

    _ensure_private_directory(args.outbox_dir)
    identity = hashlib.sha256(
        f"{envelope['intent_id']}\0{envelope['request_sha256']}".encode()
    ).hexdigest()
    envelope_path = args.outbox_dir / f"{identity}.json"
    _write_once(envelope_path, envelope)

    print(
        json.dumps(
            {
                "status": "transport_envelope_written",
                "key_id": envelope["key_id"],
                "intent_id": envelope["intent_id"],
                "request_sha256": envelope["request_sha256"],
                "prepared_sha256": envelope["prepared_sha256"],
                "approval_source_sha256": envelope["approval_source_sha256"],
                "expires_at": envelope["expires_at"],
                "envelope_path": str(envelope_path),
                "network_send_attempted": False,
                "real_order_submitted": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
