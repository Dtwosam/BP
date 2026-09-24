from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bp_engine.execution.telegram_origin_attestation import (
    OriginAttestationError,
    create_origin_attestation,
    load_origin_key_file,
    payload_sha256 as origin_payload_sha256,
)
from bp_engine.execution.telegram_transport import (
    TransportError,
    create_transport_envelope,
    load_transport_key_file,
    payload_sha256 as transport_payload_sha256,
)

MAX_INPUT_BYTES = 256 * 1024


class ApprovedOutboxError(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ApprovedOutboxError(f"{label} is not readable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ApprovedOutboxError(f"{label} must be a regular non-symlink file")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise ApprovedOutboxError(f"{label} mode must be 0600")
    if info.st_size <= 0 or info.st_size > MAX_INPUT_BYTES:
        raise ApprovedOutboxError(f"{label} size invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApprovedOutboxError(f"{label} JSON invalid") from exc
    if not isinstance(payload, Mapping):
        raise ApprovedOutboxError(f"{label} must contain a JSON object")
    return dict(payload)


def _private_directory(path: Path, *, label: str) -> None:
    try:
        path.mkdir(parents=True, mode=0o700)
    except FileExistsError:
        pass
    try:
        info = path.lstat()
    except OSError as exc:
        raise ApprovedOutboxError(f"{label} is not accessible") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ApprovedOutboxError(f"{label} must be a non-symlink directory")
    os.chmod(path, 0o700)


def _write_once(path: Path, payload: Mapping[str, Any], *, label: str) -> None:
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
        raise ApprovedOutboxError(f"{label} already exists") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def stage_approved_outbox(
    *,
    prepared_path: Path,
    approval_path: Path,
    origin_key_path: Path,
    origin_key_id: str,
    transport_key_path: Path,
    transport_key_id: str,
    outbox_dir: Path,
    expected_intent_id: str,
    expected_request_sha256: str,
    observed_at: datetime,
    nonce: str,
) -> dict[str, Any]:
    prepared = _load_json(prepared_path, label="prepared file")
    approval = _load_json(approval_path, label="approval file")
    try:
        origin_key = load_origin_key_file(origin_key_path)
        transport_key = load_transport_key_file(transport_key_path)
    except (OriginAttestationError, TransportError) as exc:
        raise ApprovedOutboxError(str(exc)) from exc

    if hmac.compare_digest(origin_key, transport_key):
        raise ApprovedOutboxError("origin and transport keys must use different material")
    if origin_key_id.strip() == transport_key_id.strip():
        raise ApprovedOutboxError("origin and transport key ids must be different")

    try:
        origin_attestation = create_origin_attestation(
            prepared,
            approval=approval,
            key=origin_key,
            key_id=origin_key_id,
            attested_at=observed_at,
        )
        envelope = create_transport_envelope(
            prepared,
            approval=approval,
            origin_attestation=origin_attestation,
            key=transport_key,
            key_id=transport_key_id,
            created_at=observed_at,
            nonce=nonce,
        )
    except (OriginAttestationError, TransportError) as exc:
        raise ApprovedOutboxError(str(exc)) from exc

    if str(envelope["intent_id"]) != expected_intent_id:
        raise ApprovedOutboxError("approved intent id does not match handoff environment")
    if str(envelope["request_sha256"]) != expected_request_sha256:
        raise ApprovedOutboxError("approved request sha256 does not match handoff environment")

    state_dir = approval_path.parent
    _private_directory(state_dir, label="approval state directory")
    _private_directory(outbox_dir, label="transport outbox directory")

    origin_path = state_dir / "origin-attestation.json"
    identity = hashlib.sha256(
        f"{envelope['intent_id']}\0{envelope['request_sha256']}".encode()
    ).hexdigest()
    envelope_path = outbox_dir / f"{identity}.json"

    _write_once(origin_path, origin_attestation, label="origin attestation")
    _write_once(envelope_path, envelope, label="transport envelope")

    return {
        "schema_version": 1,
        "status": "approved_outbox_staged",
        "intent_id": str(envelope["intent_id"]),
        "request_sha256": str(envelope["request_sha256"]),
        "origin_key_id": str(origin_attestation["key_id"]),
        "transport_key_id": str(envelope["key_id"]),
        "origin_attestation_sha256": origin_payload_sha256(origin_attestation),
        "envelope_sha256": transport_payload_sha256(envelope),
        "origin_attestation_path": str(origin_path),
        "envelope_path": str(envelope_path),
        "expires_at": str(envelope["expires_at"]),
        "retry_allowed": False,
        "network_send_attempted": False,
        "executor_invoked": False,
        "real_order_submitted": False,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Stage one exact Telegram-approved Phase 15 order into the local "
            "authenticated transport outbox."
        )
    )
    parser.add_argument("prepared_path", type=Path)
    parser.add_argument("approval_path", type=Path)
    parser.add_argument(
        "--outbox-dir",
        type=Path,
        default=Path("/var/lib/bp/phase15-canary-telegram-transport/outbox"),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if os.environ.get("BP_TELEGRAM_APPROVED_OUTBOX_ENABLED", "no") != "yes":
        raise SystemExit("Telegram approved outbox staging is not enabled")
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
    ):
        if os.environ.get(forbidden):
            raise SystemExit(f"{forbidden} must not be present in approved outbox staging")

    origin_key_path_raw = os.environ.get("BP_TELEGRAM_ORIGIN_KEY_FILE", "").strip()
    origin_key_id = os.environ.get("BP_TELEGRAM_ORIGIN_KEY_ID", "").strip()
    transport_key_path_raw = os.environ.get(
        "BP_TELEGRAM_TRANSPORT_KEY_FILE", ""
    ).strip()
    transport_key_id = os.environ.get("BP_TELEGRAM_TRANSPORT_KEY_ID", "").strip()
    expected_intent_id = os.environ.get("BP_APPROVED_INTENT_ID", "").strip()
    expected_request_sha256 = os.environ.get(
        "BP_APPROVED_REQUEST_SHA256", ""
    ).strip()
    if not all(
        (
            origin_key_path_raw,
            origin_key_id,
            transport_key_path_raw,
            transport_key_id,
            expected_intent_id,
            expected_request_sha256,
        )
    ):
        raise SystemExit("Telegram approved outbox configuration incomplete")

    origin_key_path = Path(origin_key_path_raw)
    transport_key_path = Path(transport_key_path_raw)
    try:
        if origin_key_path.resolve() == transport_key_path.resolve():
            raise SystemExit("origin and transport key files must be separate")
    except OSError as exc:
        raise SystemExit("unable to resolve Telegram key paths") from exc

    try:
        result = stage_approved_outbox(
            prepared_path=args.prepared_path,
            approval_path=args.approval_path,
            origin_key_path=origin_key_path,
            origin_key_id=origin_key_id,
            transport_key_path=transport_key_path,
            transport_key_id=transport_key_id,
            outbox_dir=args.outbox_dir,
            expected_intent_id=expected_intent_id,
            expected_request_sha256=expected_request_sha256,
            observed_at=_utc_now(),
            nonce=secrets.token_urlsafe(24),
        )
    except ApprovedOutboxError as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error": str(exc),
                    "retry_allowed": False,
                    "network_send_attempted": False,
                    "executor_invoked": False,
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
