from __future__ import annotations

import argparse
import json
import os
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bp_engine.execution.telegram_origin_attestation import (
    OriginAttestationError,
    create_origin_attestation,
    load_origin_key_file,
    payload_sha256,
)

MAX_INPUT_BYTES = 256 * 1024


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a BP Telegram approval-origin attestation."
    )
    parser.add_argument("prepared_path", type=Path)
    parser.add_argument("approval_path", type=Path)
    parser.add_argument("output_path", type=Path)
    return parser.parse_args()


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise OriginAttestationError(f"{label} is not readable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise OriginAttestationError(f"{label} must be a regular non-symlink file")
    if info.st_size <= 0 or info.st_size > MAX_INPUT_BYTES:
        raise OriginAttestationError(f"{label} size invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OriginAttestationError(f"{label} JSON invalid") from exc
    if not isinstance(payload, Mapping):
        raise OriginAttestationError(f"{label} must contain a JSON object")
    return dict(payload)


def _validate_output_parent(path: Path) -> None:
    parent = path.parent
    try:
        info = parent.lstat()
    except OSError as exc:
        raise OriginAttestationError("origin attestation output directory missing") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise OriginAttestationError(
            "origin attestation output directory must be a non-symlink directory"
        )
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise OriginAttestationError(
            "origin attestation output directory must not grant group or other access"
        )


def _write_once(path: Path, payload: Mapping[str, Any]) -> None:
    _validate_output_parent(path)
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
        raise OriginAttestationError("origin attestation output already exists") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def attest_origin(
    *,
    prepared_path: Path,
    approval_path: Path,
    output_path: Path,
    key_path: Path,
    key_id: str,
    attested_at: datetime,
) -> dict[str, Any]:
    key = load_origin_key_file(key_path)
    prepared = _load_json(prepared_path, label="prepared file")
    approval = _load_json(approval_path, label="approval file")
    attestation = create_origin_attestation(
        prepared,
        approval=approval,
        key=key,
        key_id=key_id,
        attested_at=attested_at,
    )
    _write_once(output_path, attestation)
    return {
        "status": "origin_attested",
        "key_id": str(attestation["key_id"]),
        "intent_id": str(attestation["intent_id"]),
        "request_sha256": str(attestation["request_sha256"]),
        "prepared_sha256": str(attestation["prepared_sha256"]),
        "approval_sha256": str(attestation["approval_sha256"]),
        "approval_source_sha256": str(attestation["approval_source_sha256"]),
        "attestation_sha256": payload_sha256(attestation),
        "output_path": str(output_path),
        "network_action_performed": False,
        "executor_invoked": False,
        "real_order_submitted": False,
    }


def main() -> int:
    args = _parse_args()
    if os.environ.get("BP_TELEGRAM_ORIGIN_ATTEST_ENABLED", "no") != "yes":
        raise SystemExit("Telegram origin attestation is not enabled")
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
        "GOOGLE_APPLICATION_CREDENTIALS",
        "BP_TELEGRAM_TRANSPORT_HMAC_KEY",
    ):
        if os.environ.get(forbidden):
            raise SystemExit(f"{forbidden} must not be present in origin attester")

    key_path_raw = os.environ.get("BP_TELEGRAM_ORIGIN_KEY_FILE", "").strip()
    key_id = os.environ.get("BP_TELEGRAM_ORIGIN_KEY_ID", "").strip()
    if not key_path_raw or not key_id:
        raise SystemExit("Telegram origin attestation configuration incomplete")

    result = attest_origin(
        prepared_path=args.prepared_path,
        approval_path=args.approval_path,
        output_path=args.output_path,
        key_path=Path(key_path_raw),
        key_id=key_id,
        attested_at=_utc_now(),
    )
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
