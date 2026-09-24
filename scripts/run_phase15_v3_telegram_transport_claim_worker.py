from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bp_engine.execution.telegram_transport import (
    TransportError,
    claim_transport_envelope,
    load_transport_key_file,
)


MAX_ENVELOPE_BYTES = 256 * 1024


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Claim verified BP Telegram transport inbox envelopes."
    )
    parser.add_argument(
        "--inbox-dir",
        type=Path,
        default=Path("/var/lib/bp-canary/telegram-transport-inbox"),
    )
    parser.add_argument(
        "--claim-dir",
        type=Path,
        default=Path("/var/lib/bp-canary/telegram-transport-claims"),
    )
    parser.add_argument(
        "--ready-dir",
        type=Path,
        default=Path("/var/lib/bp-canary/telegram-transport-ready"),
    )
    parser.add_argument(
        "--processed-dir",
        type=Path,
        default=Path("/var/lib/bp-canary/telegram-transport-claim-processed"),
    )
    parser.add_argument(
        "--failure-dir",
        type=Path,
        default=Path("/var/lib/bp-canary/telegram-transport-claim-failures"),
    )
    parser.add_argument("--poll-seconds", type=float, default=0.1)
    return parser.parse_args()


def _ensure_private_directory(path: Path, *, label: str) -> None:
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


def _validate_readonly_private_directory(path: Path, *, label: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise TransportError(f"{label} is not accessible") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise TransportError(f"{label} must be a non-symlink directory")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise TransportError(f"{label} must not grant group or other access")


def _load_envelope_file(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise TransportError("transport envelope file is not readable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise TransportError("transport envelope must be a regular non-symlink file")
    if info.st_size <= 0 or info.st_size > MAX_ENVELOPE_BYTES:
        raise TransportError("transport envelope file size invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransportError("transport envelope JSON invalid") from exc
    if not isinstance(payload, Mapping):
        raise TransportError("transport envelope must contain a JSON object")
    return dict(payload)


def _write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
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
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _pending(
    inbox_dir: Path,
    processed_dir: Path,
    failure_dir: Path,
) -> list[Path]:
    _validate_readonly_private_directory(
        inbox_dir,
        label="transport inbox directory",
    )
    _ensure_private_directory(processed_dir, label="claim processed directory")
    _ensure_private_directory(failure_dir, label="claim failure directory")
    result: list[Path] = []
    for path in sorted(inbox_dir.glob("*.json")):
        if (processed_dir / path.name).exists() or (failure_dir / path.name).exists():
            continue
        result.append(path)
    return result


def _materialize_ready(
    *,
    ready_root: Path,
    claimed: Mapping[str, Any],
    envelope: Mapping[str, Any],
    observed_at: datetime,
) -> Path:
    _ensure_private_directory(ready_root, label="transport ready directory")
    claim_id = str(claimed["claim_id"])
    if len(claim_id) != 64 or any(ch not in "0123456789abcdef" for ch in claim_id):
        raise TransportError("transport claim id invalid")
    final_dir = ready_root / claim_id
    if final_dir.exists():
        raise TransportError("transport ready directory already exists")

    stage = Path(tempfile.mkdtemp(prefix=f".{claim_id}.", dir=ready_root))
    os.chmod(stage, 0o700)
    try:
        receipt = {
            "schema_version": 1,
            "status": "claimed_ready",
            "key_id": str(claimed["key_id"]),
            "claim_id": claim_id,
            "intent_id": str(claimed["intent_id"]),
            "prediction_id": str(claimed["prediction_id"]),
            "paper_order_id": str(claimed["paper_order_id"]),
            "request_sha256": str(claimed["request_sha256"]),
            "prepared_sha256": str(claimed["prepared_sha256"]),
            "approval_sha256": str(claimed["approval_sha256"]),
            "approval_source_sha256": str(claimed["approval_source_sha256"]),
            "claim_sha256": str(claimed["claim_sha256"]),
            "ready_at": observed_at.astimezone(UTC).isoformat(),
            "retry_allowed": False,
            "executor_invoked": False,
            "real_order_submitted": False,
        }
        _write_private_json(stage / "prepared.json", claimed["prepared"])
        _write_private_json(stage / "approval.json", claimed["approval"])
        _write_private_json(stage / "envelope.json", envelope)
        _write_private_json(stage / "receipt.json", receipt)
        os.rename(stage, final_dir)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return final_dir


def _terminal_failure(
    *,
    failure_dir: Path,
    inbox_path: Path,
    envelope: Mapping[str, Any] | None,
    status: str,
    error: Exception,
    observed_at: datetime,
    claim_consumed: bool,
) -> Path:
    _ensure_private_directory(failure_dir, label="claim failure directory")
    path = failure_dir / inbox_path.name
    if path.exists():
        return path
    payload: dict[str, Any] = {
        "schema_version": 1,
        "status": status,
        "inbox_name": inbox_path.name,
        "error_type": type(error).__name__,
        "error_sha256": hashlib.sha256(str(error).encode()).hexdigest(),
        "failed_at": observed_at.astimezone(UTC).isoformat(),
        "claim_consumed": claim_consumed,
        "retry_allowed": False,
        "executor_invoked": False,
        "real_order_submitted": False,
    }
    if envelope is not None:
        payload["key_id"] = str(envelope.get("key_id") or "")
        payload["intent_id"] = str(envelope.get("intent_id") or "")
        payload["request_sha256"] = str(envelope.get("request_sha256") or "")
    _write_private_json(path, payload)
    return path


def claim_pending_once(
    *,
    inbox_dir: Path,
    claim_dir: Path,
    ready_dir: Path,
    processed_dir: Path,
    failure_dir: Path,
    key_path: Path,
    expected_key_id: str,
    observed_at: datetime,
) -> list[dict[str, Any]]:
    key = load_transport_key_file(key_path)
    _ensure_private_directory(claim_dir, label="transport claim directory")
    _ensure_private_directory(ready_dir, label="transport ready directory")
    results: list[dict[str, Any]] = []

    for inbox_path in _pending(inbox_dir, processed_dir, failure_dir):
        envelope: dict[str, Any] | None = None
        try:
            envelope = _load_envelope_file(inbox_path)
            claimed = claim_transport_envelope(
                envelope,
                key=key,
                expected_key_id=expected_key_id,
                observed_at=observed_at,
                state_dir=claim_dir,
            )
        except TransportError as exc:
            if str(exc) == "transport key id mismatch":
                results.append(
                    {
                        "status": "key_id_mismatch_retry_later",
                        "inbox_name": inbox_path.name,
                        "executor_invoked": False,
                        "real_order_submitted": False,
                    }
                )
                continue
            failure_path = _terminal_failure(
                failure_dir=failure_dir,
                inbox_path=inbox_path,
                envelope=envelope,
                status="terminal_unclaimable_envelope",
                error=exc,
                observed_at=observed_at,
                claim_consumed=str(exc) == "transport envelope already claimed",
            )
            results.append(
                {
                    "status": "terminal_unclaimable_envelope",
                    "failure_path": str(failure_path),
                    "executor_invoked": False,
                    "real_order_submitted": False,
                }
            )
            continue

        try:
            ready_path = _materialize_ready(
                ready_root=ready_dir,
                claimed=claimed,
                envelope=envelope,
                observed_at=observed_at,
            )
        except (OSError, TransportError) as exc:
            failure_path = _terminal_failure(
                failure_dir=failure_dir,
                inbox_path=inbox_path,
                envelope=envelope,
                status="claim_consumed_materialization_failed",
                error=exc,
                observed_at=observed_at,
                claim_consumed=True,
            )
            results.append(
                {
                    "status": "claim_consumed_materialization_failed",
                    "failure_path": str(failure_path),
                    "retry_allowed": False,
                    "executor_invoked": False,
                    "real_order_submitted": False,
                }
            )
            continue

        processed = {
            "schema_version": 1,
            "status": "claimed_ready",
            "inbox_name": inbox_path.name,
            "claim_id": str(claimed["claim_id"]),
            "claim_sha256": str(claimed["claim_sha256"]),
            "intent_id": str(claimed["intent_id"]),
            "request_sha256": str(claimed["request_sha256"]),
            "ready_path": str(ready_path),
            "processed_at": observed_at.astimezone(UTC).isoformat(),
            "retry_allowed": False,
            "executor_invoked": False,
            "real_order_submitted": False,
        }
        processed_path = processed_dir / inbox_path.name
        try:
            _write_private_json(processed_path, processed)
        except (OSError, TransportError) as exc:
            failure_path = _terminal_failure(
                failure_dir=failure_dir,
                inbox_path=inbox_path,
                envelope=envelope,
                status="claim_consumed_processed_receipt_failed",
                error=exc,
                observed_at=observed_at,
                claim_consumed=True,
            )
            results.append(
                {
                    "status": "claim_consumed_processed_receipt_failed",
                    "ready_path": str(ready_path),
                    "failure_path": str(failure_path),
                    "retry_allowed": False,
                    "executor_invoked": False,
                    "real_order_submitted": False,
                }
            )
            continue

        results.append(
            {
                **processed,
                "processed_path": str(processed_path),
            }
        )
    return results


def main() -> int:
    args = _parse_args()
    if os.environ.get("BP_TELEGRAM_TRANSPORT_CLAIM_WORKER_ENABLED", "no") != "yes":
        raise SystemExit("Telegram transport claim worker is not enabled")
    if not 0.05 <= args.poll_seconds <= 5:
        raise SystemExit("poll seconds must be within 0.05..5")
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
            raise SystemExit(f"{forbidden} must not be present in claim worker")

    key_path_raw = os.environ.get("BP_TELEGRAM_TRANSPORT_KEY_FILE", "").strip()
    key_id = os.environ.get("BP_TELEGRAM_TRANSPORT_KEY_ID", "").strip()
    if not key_path_raw or not key_id:
        raise SystemExit("Telegram transport claim worker configuration incomplete")
    key_path = Path(key_path_raw)
    load_transport_key_file(key_path)

    while True:
        results = claim_pending_once(
            inbox_dir=args.inbox_dir,
            claim_dir=args.claim_dir,
            ready_dir=args.ready_dir,
            processed_dir=args.processed_dir,
            failure_dir=args.failure_dir,
            key_path=key_path,
            expected_key_id=key_id,
            observed_at=_utc_now(),
        )
        for result in results:
            print(json.dumps(result, sort_keys=True), flush=True)
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
