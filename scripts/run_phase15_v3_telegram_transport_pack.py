from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bp_engine.execution.telegram_transport import (
    TransportError,
    create_transport_envelope,
    load_transport_key_file,
)

MAX_INPUT_BYTES = 128 * 1024


def _ensure_private_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, mode=0o700)
    except FileExistsError:
        pass
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise TransportError("transport output directory must be a non-symlink directory")
    os.chmod(path, 0o700)


def _load_json_file(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise TransportError(f"{path.name} is not readable") from exc
    if path.is_symlink() or not path.is_file():
        raise TransportError(f"{path.name} must be a regular non-symlink file")
    if info.st_size <= 0 or info.st_size > MAX_INPUT_BYTES:
        raise TransportError(f"{path.name} size invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransportError(f"{path.name} JSON invalid") from exc
    if not isinstance(payload, Mapping):
        raise TransportError(f"{path.name} must contain a JSON object")
    return dict(payload)


def _write_new_json(path: Path, payload: Mapping[str, Any]) -> None:
    _ensure_private_directory(path.parent)
    encoded = (
        json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise TransportError("transport envelope output already exists") from exc
    with os.fdopen(fd, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def pack_transport(
    *,
    prepared_path: Path,
    approval_path: Path,
    origin_attestation_path: Path,
    key_path: Path,
    key_id: str,
    output_path: Path,
    created_at: datetime,
    nonce: str,
) -> dict[str, Any]:
    prepared = _load_json_file(prepared_path)
    approval = _load_json_file(approval_path)
    origin_attestation = _load_json_file(origin_attestation_path)
    key = load_transport_key_file(key_path)
    envelope = create_transport_envelope(
        prepared,
        approval=approval,
        origin_attestation=origin_attestation,
        key=key,
        key_id=key_id,
        created_at=created_at,
        nonce=nonce,
    )
    _write_new_json(output_path, envelope)
    return {
        "status": "packed",
        "key_id": envelope["key_id"],
        "intent_id": envelope["intent_id"],
        "request_sha256": envelope["request_sha256"],
        "prepared_sha256": envelope["prepared_sha256"],
        "origin_attestation_sha256": envelope["origin_attestation_sha256"],
        "expires_at": envelope["expires_at"],
        "output_path": str(output_path),
        "network_action_performed": False,
        "real_order_submitted": False,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pack one approved Phase 15 intent into an authenticated transport envelope."
    )
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--origin-attestation", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        result = pack_transport(
            prepared_path=args.prepared,
            approval_path=args.approval,
            origin_attestation_path=args.origin_attestation,
            key_path=args.key_file,
            key_id=args.key_id,
            output_path=args.output,
            created_at=datetime.now(UTC),
            nonce=secrets.token_urlsafe(24),
        )
    except TransportError as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error": str(exc),
                    "network_action_performed": False,
                    "real_order_submitted": False,
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
