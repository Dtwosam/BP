from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from bp_engine.execution.telegram_pubsub import PubSubTransportError
from bp_engine.execution.telegram_pubsub_delivery import (
    ensure_private_directory,
    load_envelope_file,
    publish_transport,
    write_new_json,
)
from bp_engine.execution.telegram_transport import (
    TransportError,
    load_transport_key_file,
    payload_sha256,
    verify_transport_envelope,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish pending immutable BP Telegram transport envelopes."
    )
    parser.add_argument(
        "--outbox-dir",
        type=Path,
        default=Path("/var/lib/bp/phase15-canary-telegram-transport/outbox"),
    )
    parser.add_argument(
        "--receipt-dir",
        type=Path,
        default=Path("/var/lib/bp/phase15-canary-telegram-transport/published"),
    )
    parser.add_argument(
        "--failure-dir",
        type=Path,
        default=Path("/var/lib/bp/phase15-canary-telegram-transport/failed"),
    )
    parser.add_argument("--poll-seconds", type=float, default=0.25)
    return parser.parse_args()


def _failure_path(failure_dir: Path, envelope_path: Path) -> Path:
    return failure_dir / envelope_path.name


def _record_terminal_failure(
    *,
    failure_dir: Path,
    envelope_path: Path,
    envelope: dict[str, Any] | None,
    error: Exception,
    observed_at: datetime,
) -> Path:
    ensure_private_directory(failure_dir, label="publish failure directory")
    path = _failure_path(failure_dir, envelope_path)
    if path.exists():
        return path
    payload: dict[str, Any] = {
        "schema_version": 1,
        "status": "terminal_transport_failure",
        "outbox_path": str(envelope_path),
        "error_type": type(error).__name__,
        "error": str(error)[:512],
        "failed_at": observed_at.astimezone(UTC).isoformat(),
        "network_publish_attempted": False,
        "executor_invoked": False,
        "real_order_submitted": False,
    }
    if envelope is not None:
        payload["envelope_sha256"] = payload_sha256(envelope)
        payload["intent_id"] = str(envelope.get("intent_id") or "")
        payload["request_sha256"] = str(envelope.get("request_sha256") or "")
        payload["key_id"] = str(envelope.get("key_id") or "")
    write_new_json(path, payload, label="publish failure receipt")
    return path


def _pending(outbox_dir: Path, receipt_dir: Path, failure_dir: Path) -> list[Path]:
    ensure_private_directory(outbox_dir, label="transport outbox directory")
    ensure_private_directory(receipt_dir, label="publish receipt directory")
    ensure_private_directory(failure_dir, label="publish failure directory")
    result: list[Path] = []
    for path in sorted(outbox_dir.glob("*.json")):
        if (receipt_dir / path.name).exists() or (failure_dir / path.name).exists():
            continue
        result.append(path)
    return result


def _validate_pending(
    *,
    envelope_path: Path,
    key: bytes,
    expected_key_id: str,
    observed_at: datetime,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    try:
        envelope = load_envelope_file(envelope_path)
    except TransportError as exc:
        return None, {
            "status": "terminal_invalid_outbox",
            "error": exc,
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
            return envelope, {
                "status": "key_id_mismatch_retry_later",
                "error": exc,
            }
        return envelope, {
            "status": "terminal_invalid_or_expired_envelope",
            "error": exc,
        }
    return envelope, {
        "status": "verified",
        "verified": verified,
    }


def publish_pending_once(
    *,
    client: httpx.Client,
    outbox_dir: Path,
    receipt_dir: Path,
    failure_dir: Path,
    key_path: Path,
    expected_key_id: str,
    project_id: str,
    topic_id: str,
    observed_at: datetime,
) -> list[dict[str, Any]]:
    key = load_transport_key_file(key_path)
    results: list[dict[str, Any]] = []
    for envelope_path in _pending(outbox_dir, receipt_dir, failure_dir):
        envelope, validation = _validate_pending(
            envelope_path=envelope_path,
            key=key,
            expected_key_id=expected_key_id,
            observed_at=observed_at,
        )
        status = str(validation["status"])
        if status.startswith("terminal_"):
            failure_path = _record_terminal_failure(
                failure_dir=failure_dir,
                envelope_path=envelope_path,
                envelope=envelope,
                error=validation["error"],
                observed_at=observed_at,
            )
            results.append(
                {
                    "status": status,
                    "failure_path": str(failure_path),
                    "network_publish_attempted": False,
                    "executor_invoked": False,
                    "real_order_submitted": False,
                }
            )
            continue
        if status == "key_id_mismatch_retry_later":
            results.append(
                {
                    "status": status,
                    "network_publish_attempted": False,
                    "executor_invoked": False,
                    "real_order_submitted": False,
                }
            )
            continue

        try:
            published = publish_transport(
                client=client,
                envelope_path=envelope_path,
                key_path=key_path,
                expected_key_id=expected_key_id,
                project_id=project_id,
                topic_id=topic_id,
                receipt_dir=receipt_dir,
                observed_at=observed_at,
            )
        except (PubSubTransportError, TransportError) as exc:
            results.append(
                {
                    "status": "delivery_retry_later",
                    "error_type": type(exc).__name__,
                    "error_sha256": hashlib.sha256(str(exc).encode()).hexdigest(),
                    "network_publish_attempted": True,
                    "executor_invoked": False,
                    "real_order_submitted": False,
                }
            )
            continue
        results.append(published)
    return results


def main() -> int:
    args = _parse_args()
    if os.environ.get("BP_TELEGRAM_PUBSUB_PUBLISH_WORKER_ENABLED", "no") != "yes":
        raise SystemExit("Telegram Pub/Sub publisher worker is not enabled")
    if not 0.1 <= args.poll_seconds <= 5:
        raise SystemExit("poll seconds must be within 0.1..5")
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
            raise SystemExit(f"{forbidden} must not be present in publisher worker")

    key_path_raw = os.environ.get("BP_TELEGRAM_TRANSPORT_KEY_FILE", "").strip()
    key_id = os.environ.get("BP_TELEGRAM_TRANSPORT_KEY_ID", "").strip()
    project_id = os.environ.get("BP_TELEGRAM_PUBSUB_PROJECT_ID", "").strip()
    topic_id = os.environ.get("BP_TELEGRAM_PUBSUB_TOPIC_ID", "").strip()
    if not key_path_raw or not key_id or not project_id or not topic_id:
        raise SystemExit("Telegram Pub/Sub publisher worker configuration incomplete")
    key_path = Path(key_path_raw)
    load_transport_key_file(key_path)

    with httpx.Client(trust_env=False) as client:
        while True:
            results = publish_pending_once(
                client=client,
                outbox_dir=args.outbox_dir,
                receipt_dir=args.receipt_dir,
                failure_dir=args.failure_dir,
                key_path=key_path,
                expected_key_id=key_id,
                project_id=project_id,
                topic_id=topic_id,
                observed_at=_utc_now(),
            )
            for result in results:
                print(json.dumps(result, sort_keys=True), flush=True)
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
