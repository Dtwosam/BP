from __future__ import annotations

import argparse
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bp_engine.execution.telegram_transport import (
    TransportError,
    claim_transport_envelope,
    load_transport_key_file,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and claim a BP Telegram transport envelope"
    )
    parser.add_argument("envelope_path", type=Path)
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path("/var/lib/bp-canary/telegram-transport-claims"),
    )
    return parser.parse_args()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise TransportError("transport envelope is not readable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise TransportError("transport envelope must be a regular non-symlink file")
    if info.st_size <= 0 or info.st_size > 1_048_576:
        raise TransportError("transport envelope size invalid")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TransportError("transport envelope must contain a JSON object")
    return payload


def _ensure_private_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, mode=0o700)
    except FileExistsError:
        pass
    try:
        info = path.lstat()
    except OSError as exc:
        raise TransportError("transport receipt directory is not accessible") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise TransportError("transport receipt must be a non-symlink directory")
    os.chmod(path, 0o700)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _utc_now() -> datetime:
    return datetime.now(UTC)


def main() -> int:
    args = _parse_args()
    if os.environ.get("BP_TELEGRAM_TRANSPORT_INTAKE_ENABLED", "no") != "yes":
        raise SystemExit("Telegram transport intake is not enabled")
    for forbidden in (
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
        "BP_TELEGRAM_BOT_TOKEN",
    ):
        if os.environ.get(forbidden):
            raise SystemExit(f"{forbidden} must not be present in transport intake")

    key_path_raw = os.environ.get("BP_TELEGRAM_TRANSPORT_KEY_FILE", "").strip()
    if not key_path_raw:
        raise SystemExit("BP_TELEGRAM_TRANSPORT_KEY_FILE is required")
    key_id = os.environ.get("BP_TELEGRAM_TRANSPORT_KEY_ID", "").strip()
    if not key_id:
        raise SystemExit("BP_TELEGRAM_TRANSPORT_KEY_ID is required")
    key = load_transport_key_file(Path(key_path_raw))
    envelope = _load_json(args.envelope_path)
    claimed = claim_transport_envelope(
        envelope,
        key=key,
        expected_key_id=key_id,
        observed_at=_utc_now(),
        state_dir=args.state_dir,
    )

    receipts_root = args.state_dir / "receipts"
    _ensure_private_directory(receipts_root)
    receipt_dir = receipts_root / str(claimed["claim_id"])
    _ensure_private_directory(receipt_dir)
    prepared_path = receipt_dir / "prepared.json"
    approval_path = receipt_dir / "approval.json"
    origin_attestation_path = receipt_dir / "origin-attestation.json"
    if (
        prepared_path.exists()
        or approval_path.exists()
        or origin_attestation_path.exists()
    ):
        raise TransportError("transport receipt payload already exists")
    _atomic_json(prepared_path, claimed["prepared"])
    _atomic_json(approval_path, claimed["approval"])
    _atomic_json(origin_attestation_path, claimed["origin_attestation"])

    print(
        json.dumps(
            {
                "status": "transport_envelope_claimed",
                "key_id": claimed["key_id"],
                "intent_id": claimed["intent_id"],
                "request_sha256": claimed["request_sha256"],
                "prepared_sha256": claimed["prepared_sha256"],
                "approval_source_sha256": claimed["approval_source_sha256"],
                "origin_attestation_sha256": claimed["origin_attestation_sha256"],
                "claim_sha256": claimed["claim_sha256"],
                "prepared_path": str(prepared_path),
                "approval_path": str(approval_path),
                "origin_attestation_path": str(origin_attestation_path),
                "executor_invoked": False,
                "real_order_submitted": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
