from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from google.cloud import pubsub_v1
from sqlalchemy import create_engine, select

from bp_engine.config import Settings
from bp_engine.execution.fast_live import (
    FAST_LIVE_PURPOSE,
    FAST_LIVE_WARMUP_PURPOSE,
    create_envelope,
    create_warmup_message,
    load_private_json,
    verify_runtime_authorization,
)
from bp_engine.execution.fast_live_prepare import prepare_fast_live_candidate
from bp_engine.execution.live import InterlockDecision
from bp_engine.execution.telegram_transport import load_transport_key_file
from bp_engine.storage import schema


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare and immediately publish one pre-authorized fast-live V3 candidate."
    )
    parser.add_argument("--env-file", default=None)
    parser.add_argument(
        "--project-state",
        type=Path,
        default=Path("/etc/bp-fast-live/PROJECT_STATE.json"),
    )
    parser.add_argument(
        "--runtime-authorization",
        type=Path,
        default=Path("/etc/bp-fast-live/authorization.json"),
    )
    parser.add_argument(
        "--transport-key-file",
        type=Path,
        default=Path("/etc/bp-telegram-transport/transport.key"),
    )
    parser.add_argument("--transport-key-id", required=True)
    parser.add_argument("--expected-main", required=True)
    parser.add_argument("--gcp-project", required=True)
    parser.add_argument("--topic-id", required=True)
    parser.add_argument("--official-open-order-count", type=int, required=True)
    parser.add_argument("--collateral-balance-usd", required=True)
    parser.add_argument("--poll-seconds", type=float, default=0.05)
    parser.add_argument(
        "--receipt-dir",
        type=Path,
        default=Path("/var/lib/bp/phase15-fast-live/published"),
    )
    return parser.parse_args()


def _require_runtime() -> None:
    if os.environ.get("BP_PHASE15_FAST_LIVE_SOURCE_ENABLED", "no") != "yes":
        raise SystemExit("fast live source is not explicitly enabled")
    for name in ("POLYMARKET_PRIVATE_KEY", "POLYMARKET_WALLET_ADDRESS"):
        if os.environ.get(name):
            raise SystemExit(f"{name} must not be present in fast live source")


def _load_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit("project state is not readable") from exc
    if not isinstance(payload, dict):
        raise SystemExit("project state must contain an object")
    return payload


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SystemExit("fast live receipt directory invalid")
    os.chmod(path, 0o700)


def _receipt_path(root: Path, intent_id: str, request_sha256: str) -> Path:
    key = hashlib.sha256(f"{intent_id}\0{request_sha256}".encode()).hexdigest()
    return root / f"{key}.json"


def _write_receipt(path: Path, payload: dict[str, Any]) -> None:
    encoded = (
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish_once(
    publisher: pubsub_v1.PublisherClient,
    *,
    topic_path: str,
    envelope: dict[str, Any],
) -> str:
    data = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()
    future = publisher.publish(
        topic_path,
        data,
        purpose=FAST_LIVE_PURPOSE,
        key_id=str(envelope["key_id"]),
        authorization_id=str(envelope["authorization_id"]),
        intent_id=str(envelope["intent_id"]),
        request_sha256=str(envelope["request_sha256"]),
    )
    return str(future.result(timeout=0.45))


def _publish_with_bounded_retry(
    publisher: pubsub_v1.PublisherClient,
    *,
    topic_path: str,
    envelope: dict[str, Any],
) -> tuple[str, int]:
    expires_at = datetime.fromisoformat(str(envelope["expires_at"])).astimezone(UTC)
    attempts = 0
    last_error: Exception | None = None
    while attempts < 3:
        attempts += 1
        now = _utc_now()
        if (expires_at - now).total_seconds() <= 0.15:
            break
        try:
            return (
                _publish_once(
                    publisher,
                    topic_path=topic_path,
                    envelope=envelope,
                ),
                attempts,
            )
        except Exception as exc:
            last_error = exc
            if attempts < 3:
                time.sleep(0.025)
    raise RuntimeError(
        f"fast live publish failed after {attempts} bounded attempts"
    ) from last_error


def _current_warmup_market(
    engine,
    *,
    observed_at: datetime,
) -> dict[str, str] | None:
    with engine.connect() as connection:
        row = connection.execute(
            select(
                schema.polymarket_markets.c.condition_id,
                schema.polymarket_markets.c.up_token_id,
                schema.polymarket_markets.c.down_token_id,
            )
            .where(
                schema.polymarket_markets.c.horizon_seconds == 300,
                schema.polymarket_markets.c.active.is_(True),
                schema.polymarket_markets.c.closed.is_(False),
                schema.polymarket_markets.c.accepting_orders.is_(True),
                schema.polymarket_markets.c.start_at <= observed_at,
                schema.polymarket_markets.c.end_at > observed_at,
            )
            .order_by(schema.polymarket_markets.c.start_at.desc())
            .limit(1)
        ).mappings().one_or_none()
    if row is None:
        return None
    return {
        "condition_id": str(row["condition_id"]),
        "up_token_id": str(row["up_token_id"]),
        "down_token_id": str(row["down_token_id"]),
    }


def _publish_warmup_async(
    publisher: pubsub_v1.PublisherClient,
    *,
    topic_path: str,
    warmup: dict[str, Any],
) -> None:
    data = json.dumps(
        warmup,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()
    publisher.publish(
        topic_path,
        data,
        purpose=FAST_LIVE_WARMUP_PURPOSE,
        key_id=str(warmup["key_id"]),
        authorization_id=str(warmup["authorization_id"]),
        condition_id=str(warmup["condition_id"]),
    )


def main() -> int:
    args = _parse_args()
    _require_runtime()
    if not 0.02 <= args.poll_seconds <= 1:
        raise SystemExit("poll seconds must be within 0.02..1")
    if args.official_open_order_count != 0:
        raise SystemExit("official open order count must be zero")
    collateral = Decimal(args.collateral_balance_usd)
    if collateral < Decimal("5"):
        raise SystemExit("official collateral must be at least 5")

    key = load_transport_key_file(args.transport_key_file)
    state = _load_state(args.project_state)
    runtime = load_private_json(
        args.runtime_authorization,
        label="fast live runtime authorization",
    )
    verified_runtime = verify_runtime_authorization(
        runtime,
        state=state,
        expected_main=args.expected_main,
        observed_at=_utc_now(),
    )
    activated_at = datetime.fromisoformat(str(verified_runtime["issued_at"]))
    if activated_at.tzinfo is None or activated_at.utcoffset() is None:
        raise SystemExit("runtime authorization issued_at must be timezone-aware")
    activated_at = activated_at.astimezone(UTC)

    settings = (
        Settings(_env_file=args.env_file)
        if args.env_file
        else Settings()
    )
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(args.gcp_project, args.topic_id)
    _ensure_private_dir(args.receipt_dir)
    interlock = InterlockDecision(eligible=True, reasons=())
    warmed_condition_id = ""
    next_warmup_check = 0.0

    try:
        while True:
            observed = _utc_now()
            now_monotonic = time.monotonic()
            if now_monotonic >= next_warmup_check:
                next_warmup_check = now_monotonic + 0.5
                warm_market = _current_warmup_market(
                    engine,
                    observed_at=observed,
                )
                if (
                    warm_market is not None
                    and warm_market["condition_id"] != warmed_condition_id
                ):
                    state_now = _load_state(args.project_state)
                    runtime_now = load_private_json(
                        args.runtime_authorization,
                        label="fast live runtime authorization",
                    )
                    verify_runtime_authorization(
                        runtime_now,
                        state=state_now,
                        expected_main=args.expected_main,
                        observed_at=observed,
                    )
                    warmup = create_warmup_message(
                        condition_id=warm_market["condition_id"],
                        token_ids=(
                            warm_market["up_token_id"],
                            warm_market["down_token_id"],
                        ),
                        runtime_authorization=runtime_now,
                        key=key,
                        key_id=args.transport_key_id,
                        created_at=observed,
                    )
                    _publish_warmup_async(
                        publisher,
                        topic_path=topic_path,
                        warmup=warmup,
                    )
                    warmed_condition_id = warm_market["condition_id"]

            report = prepare_fast_live_candidate(
                engine=engine,
                activated_at=activated_at,
                observed_at=observed,
                interlock=interlock,
                api_healthy=True,
                official_open_order_count=args.official_open_order_count,
                collateral_balance_usd=collateral,
            )
            status = str(report.get("status") or "")
            if status == "waiting":
                time.sleep(args.poll_seconds)
                continue
            if status == "skipped":
                print(json.dumps(report, sort_keys=True, default=str), flush=True)
                time.sleep(args.poll_seconds)
                continue
            if status != "prepared":
                print(json.dumps(report, sort_keys=True, default=str), flush=True)
                return 2

            state = _load_state(args.project_state)
            runtime = load_private_json(
                args.runtime_authorization,
                label="fast live runtime authorization",
            )
            verify_runtime_authorization(
                runtime,
                state=state,
                expected_main=args.expected_main,
                observed_at=_utc_now(),
            )
            envelope = create_envelope(
                report,
                runtime_authorization=runtime,
                key=key,
                key_id=args.transport_key_id,
                created_at=_utc_now(),
            )
            receipt_path = _receipt_path(
                args.receipt_dir,
                str(envelope["intent_id"]),
                str(envelope["request_sha256"]),
            )
            if receipt_path.exists():
                print(
                    json.dumps(
                        {
                            "status": "already_published",
                            "intent_id": envelope["intent_id"],
                            "request_sha256": envelope["request_sha256"],
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                return 0

            publish_started = time.monotonic_ns()
            message_id, publish_attempts = _publish_with_bounded_retry(
                publisher,
                topic_path=topic_path,
                envelope=envelope,
            )
            publish_completed = time.monotonic_ns()
            receipt = {
                "status": "fast_live_published",
                "message_id": message_id,
                "intent_id": envelope["intent_id"],
                "request_sha256": envelope["request_sha256"],
                "prepared_sha256": envelope["prepared_sha256"],
                "authorization_id": envelope["authorization_id"],
                "published_at": _utc_now().isoformat(),
                "publish_latency_ms": (
                    publish_completed - publish_started
                ) / 1_000_000,
                "publish_attempts": publish_attempts,
                "network_submission_attempt_consumed": False,
                "real_order_submitted": False,
            }
            _write_receipt(receipt_path, receipt)
            print(json.dumps(receipt, sort_keys=True), flush=True)
            return 0
    finally:
        engine.dispose()
        publisher.stop()


if __name__ == "__main__":
    raise SystemExit(main())
