from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import polymarket
from google.cloud import pubsub_v1

from bp_engine.execution.fast_live import (
    FAST_LIVE_APPROVAL_PURPOSE,
    FAST_LIVE_PREPARE_PURPOSE,
    FAST_LIVE_PURPOSE,
    FAST_LIVE_WARMUP_PURPOSE,
    FastLiveError,
    create_result_message,
    load_private_json,
    verify_approval_message,
    verify_envelope,
    verify_prepare_message,
    verify_runtime_authorization,
    verify_warmup_message,
)
from bp_engine.execution.fast_live_book import StreamingBookCache
from bp_engine.execution.fast_live_executor import (
    FastLiveExecutor,
    SafetyCache,
    SafetySnapshot,
    execute_with_bounded_pre_attempt_retry,
)
from bp_engine.execution.telegram_transport import load_transport_key_file

GEOBLOCK_URL = "https://polymarket.com/api/geoblock"
COLLATERAL_BASE_UNITS_PER_USD = Decimal("1000000")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Receive and execute one pre-authorized fast-live V3 order."
    )
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
        default=Path("/etc/bp-fast-live/transport.key"),
    )
    parser.add_argument("--transport-key-id", required=True)
    parser.add_argument("--expected-main", required=True)
    parser.add_argument("--gcp-project", required=True)
    parser.add_argument("--subscription-id", required=True)
    parser.add_argument("--result-topic-id", required=True)
    parser.add_argument(
        "--state-root",
        type=Path,
        default=Path("/var/lib/bp-canary/fast-live"),
    )
    parser.add_argument(
        "--kill-switch",
        type=Path,
        default=Path("/var/lib/bp-canary/fast-live/KILL"),
    )
    parser.add_argument("--safety-refresh-seconds", type=float, default=0.25)
    return parser.parse_args()


def _require_runtime() -> None:
    if os.environ.get("BP_PHASE15_FAST_LIVE_EXECUTOR_ENABLED", "no") != "yes":
        raise SystemExit("fast live executor is not explicitly enabled")
    if not os.environ.get("POLYMARKET_PRIVATE_KEY", "").strip():
        raise SystemExit("POLYMARKET_PRIVATE_KEY is required")


def _telegram_approval_required() -> bool:
    return (
        os.environ.get("BP_FAST_LIVE_TELEGRAM_APPROVAL_REQUIRED", "no")
        .strip()
        .lower()
        == "yes"
    )


def _load_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FastLiveError("project state is not readable") from exc
    if not isinstance(payload, dict):
        raise FastLiveError("project state must contain an object")
    return payload


def _secure_client() -> object:
    private_key = os.environ["POLYMARKET_PRIVATE_KEY"].strip()
    wallet = os.environ.get("POLYMARKET_WALLET_ADDRESS", "").strip()
    kwargs: dict[str, str] = {"private_key": private_key}
    if wallet:
        kwargs["wallet"] = wallet
    return polymarket.SecureClient.create(**kwargs)


def _geoblock() -> tuple[bool, str]:
    request = urllib.request.Request(
        GEOBLOCK_URL,
        headers={"User-Agent": "BP-phase15-fast-live/1"},
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        if response.status != 200:
            raise FastLiveError("geoblock request failed")
        payload = json.loads(response.read().decode("utf-8"))
    if type(payload.get("blocked")) is not bool:
        raise FastLiveError("geoblock response invalid")
    country = str(payload.get("country") or "")
    if not country:
        raise FastLiveError("geoblock country missing")
    return bool(payload["blocked"]), country


def _warm_execution_metadata(
    client: object,
    token_ids: list[str],
) -> float:
    started = time.monotonic_ns()
    for token_id in token_ids:
        client.create_limit_order(
            token_id=token_id,
            price=Decimal("0.50"),
            size=Decimal("1"),
            side="BUY",
        )
    completed = time.monotonic_ns()
    return (completed - started) / 1_000_000


def _account_snapshot(client: object) -> tuple[int, Decimal]:
    balance = client.get_balance_allowance(asset_type="COLLATERAL")
    open_orders = tuple(client.list_open_orders().iter_items())
    balance_usd = (
        Decimal(int(balance.balance)) / COLLATERAL_BASE_UNITS_PER_USD
    )
    return len(open_orders), balance_usd


class SafetyRefresher:
    def __init__(
        self,
        *,
        client: object,
        cache: SafetyCache,
        refresh_seconds: float,
    ) -> None:
        self._client = client
        self._cache = cache
        self._refresh_seconds = refresh_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="bp-fast-live-safety",
            daemon=True,
        )
        self._geo_blocked = True
        self._geo_country = ""
        self._geo_updated_monotonic = 0.0

    def refresh_once(self) -> None:
        now_monotonic = time.monotonic()
        if now_monotonic - self._geo_updated_monotonic >= 10:
            blocked, country = _geoblock()
            self._geo_blocked = blocked
            self._geo_country = country
            self._geo_updated_monotonic = now_monotonic
        if now_monotonic - self._geo_updated_monotonic > 30:
            raise FastLiveError("geoblock safety snapshot stale")
        open_orders, collateral = _account_snapshot(self._client)
        self._cache.replace(
            SafetySnapshot(
                observed_at=_utc_now(),
                geoblock_blocked=self._geo_blocked,
                geoblock_country=self._geo_country,
                open_order_count=open_orders,
                collateral_balance_usd=collateral,
            )
        )

    def start(self) -> None:
        self.refresh_once()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.refresh_once()
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            "status": "safety_refresh_failed",
                            "error": type(exc).__name__,
                            "observed_at": _utc_now().isoformat(),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            elapsed = time.monotonic() - started
            self._stop.wait(max(0.01, self._refresh_seconds - elapsed))


def _decode_message(
    message: object,
) -> tuple[dict[str, Any], dict[str, str]]:
    data = bytes(getattr(message, "data", b""))
    if not data or len(data) > 256 * 1024:
        raise FastLiveError("fast live message data size invalid")
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FastLiveError("fast live message JSON invalid") from exc
    if not isinstance(payload, dict):
        raise FastLiveError("fast live message must contain an object")
    attributes = {
        str(key): str(value)
        for key, value in dict(getattr(message, "attributes", {}) or {}).items()
    }
    common = {
        "purpose": str(payload.get("purpose") or ""),
        "key_id": str(payload.get("key_id") or ""),
        "authorization_id": str(payload.get("authorization_id") or ""),
    }
    if any(attributes.get(name) != value for name, value in common.items()):
        raise FastLiveError("fast live message attributes mismatch")
    return payload, attributes


def main() -> int:
    args = _parse_args()
    _require_runtime()
    if not 0.1 <= args.safety_refresh_seconds <= 1:
        raise SystemExit("safety refresh seconds must be within 0.1..1")

    approval_required = _telegram_approval_required()
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
        requires_telegram_approval=approval_required,
    )
    runtime_expires_at = datetime.fromisoformat(
        str(verified_runtime["expires_at"])
    ).astimezone(UTC)
    key = load_transport_key_file(args.transport_key_file)

    execution_client = _secure_client()
    safety_client = _secure_client()
    cache = SafetyCache()
    refresher = SafetyRefresher(
        client=safety_client,
        cache=cache,
        refresh_seconds=args.safety_refresh_seconds,
    )
    refresher.start()
    book_cache = StreamingBookCache()
    book_cache.start()

    executor = FastLiveExecutor(
        client=execution_client,
        safety_cache=cache,
        book_cache=book_cache,
        state_root=args.state_root,
        kill_switch_path=args.kill_switch,
    )
    subscriber = pubsub_v1.SubscriberClient()
    subscription_path = subscriber.subscription_path(
        args.gcp_project,
        args.subscription_id,
    )
    result_publisher = pubsub_v1.PublisherClient()
    result_topic_path = result_publisher.topic_path(
        args.gcp_project,
        args.result_topic_id,
    )
    terminal_event = threading.Event()
    prepared_lock = threading.Lock()
    prepared_orders: dict[tuple[str, str], dict[str, Any]] = {}

    def publish_result(
        result: dict[str, Any],
        *,
        runtime_authorization: dict[str, Any],
    ) -> str:
        result_message = create_result_message(
            result,
            key=key,
            key_id=args.transport_key_id,
            authorization_id=str(runtime_authorization["authorization_id"]),
            created_at=_utc_now(),
        )
        data = json.dumps(
            result_message,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
            default=str,
        ).encode()
        future = result_publisher.publish(
            result_topic_path,
            data,
            purpose=str(result_message["purpose"]),
            key_id=str(result_message["key_id"]),
            authorization_id=str(result_message["authorization_id"]),
            intent_id=str(result_message["intent_id"]),
            request_sha256=str(result_message["request_sha256"]),
        )
        return str(future.result(timeout=2.0))

    def callback(message: object) -> None:
        received_at = _utc_now()
        order_verified = False
        try:
            state_now = _load_state(args.project_state)
            runtime_now = load_private_json(
                args.runtime_authorization,
                label="fast live runtime authorization",
            )
            verify_runtime_authorization(
                runtime_now,
                state=state_now,
                expected_main=args.expected_main,
                observed_at=received_at,
                requires_telegram_approval=approval_required,
            )
            payload, attributes = _decode_message(message)
            purpose = str(payload.get("purpose") or "")
            if purpose == FAST_LIVE_WARMUP_PURPOSE:
                if attributes.get("condition_id") != str(
                    payload.get("condition_id") or ""
                ):
                    raise FastLiveError("fast live warmup attributes mismatch")
                warmup = verify_warmup_message(
                    payload,
                    runtime_authorization=runtime_now,
                    key=key,
                    expected_key_id=args.transport_key_id,
                    observed_at=received_at,
                )
                warm_tokens = list(warmup["token_ids"])
                book_cache.subscribe(warm_tokens)
                metadata_warm_latency_ms = _warm_execution_metadata(
                    execution_client,
                    warm_tokens,
                )
                print(
                    json.dumps(
                        {
                            "status": "fast_live_books_and_metadata_warming",
                            "condition_id": warmup["condition_id"],
                            "token_ids": warmup["token_ids"],
                            "metadata_warm_latency_ms": (
                                metadata_warm_latency_ms
                            ),
                            "observed_at": received_at.isoformat(),
                            "network_submission_attempt_consumed": False,
                            "real_order_submitted": False,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                message.ack()
                return
            if purpose == FAST_LIVE_PREPARE_PURPOSE:
                if not approval_required:
                    raise FastLiveError(
                        "fast live prepare received outside Telegram approval mode"
                    )
                required_attributes = {
                    "intent_id": str(payload.get("intent_id") or ""),
                    "request_sha256": str(payload.get("request_sha256") or ""),
                }
                if any(
                    attributes.get(name) != value
                    for name, value in required_attributes.items()
                ):
                    raise FastLiveError("fast live prepare attributes mismatch")
                verified = verify_prepare_message(
                    payload,
                    runtime_authorization=runtime_now,
                    key=key,
                    expected_key_id=args.transport_key_id,
                    observed_at=received_at,
                )
                prepared_order = executor.prepare_order(verified)
                cache_key = (
                    str(verified["intent_id"]),
                    str(verified["request_sha256"]),
                )
                with prepared_lock:
                    prepared_orders[cache_key] = {
                        "verified": verified,
                        "prepared_order": prepared_order,
                        "prepared_sha256": str(verified["prepared_sha256"]),
                    }
                print(
                    json.dumps(
                        {
                            "status": "fast_live_prepared_waiting_for_telegram",
                            "intent_id": verified["intent_id"],
                            "request_sha256": verified["request_sha256"],
                            "prepared_sha256": verified["prepared_sha256"],
                            "sign_latency_ms": prepared_order.sign_latency_ms,
                            "network_submission_attempt_consumed": False,
                            "real_order_submitted": False,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                message.ack()
                return
            if purpose == FAST_LIVE_APPROVAL_PURPOSE:
                if not approval_required:
                    raise FastLiveError(
                        "fast live approval received outside Telegram approval mode"
                    )
                required_attributes = {
                    "intent_id": str(payload.get("intent_id") or ""),
                    "request_sha256": str(payload.get("request_sha256") or ""),
                }
                if any(
                    attributes.get(name) != value
                    for name, value in required_attributes.items()
                ):
                    raise FastLiveError("fast live approval attributes mismatch")
                approved = verify_approval_message(
                    payload,
                    runtime_authorization=runtime_now,
                    key=key,
                    expected_key_id=args.transport_key_id,
                    observed_at=received_at,
                )
                cache_key = (
                    str(approved["intent_id"]),
                    str(approved["request_sha256"]),
                )
                with prepared_lock:
                    cached = prepared_orders.pop(cache_key, None)
                if cached is None:
                    raise FastLiveError(
                        "fast live approval has no matching prepared order"
                    )
                if str(cached["prepared_sha256"]) != str(
                    approved["prepared_sha256"]
                ):
                    raise FastLiveError(
                        "fast live approval prepared hash mismatch"
                    )
                verified = dict(cached["verified"])
                verified["expires_at"] = str(approved["expires_at"])
                order_verified = True
                result = execute_with_bounded_pre_attempt_retry(
                    executor,
                    verified,
                    prepared_order=cached["prepared_order"],
                )
                result["prepare_created_at"] = str(
                    cached["verified"]["created_at"]
                )
                result["approval_created_at"] = str(approved["created_at"])
                result["approval_received_at"] = received_at.isoformat()
                approval_created = datetime.fromisoformat(
                    str(approved["created_at"])
                ).astimezone(UTC)
                result["approval_to_receive_ms"] = (
                    received_at - approval_created
                ).total_seconds() * 1000
                try:
                    result_message_id = publish_result(
                        result,
                        runtime_authorization=runtime_now,
                    )
                except Exception as exc:
                    print(
                        json.dumps(
                            {
                                "status": "fast_live_result_publish_failed",
                                "error": type(exc).__name__,
                                "intent_id": result.get("intent_id"),
                                "request_sha256": result.get("request_sha256"),
                                "network_submission_attempt_consumed": result.get(
                                    "network_submission_attempt_consumed"
                                )
                                is True,
                                "observed_at": _utc_now().isoformat(),
                            },
                            sort_keys=True,
                        ),
                        flush=True,
                    )
                    message.nack()
                    return
                result["result_message_id"] = result_message_id
                print(json.dumps(result, sort_keys=True, default=str), flush=True)
                message.ack()
                if result.get("network_submission_attempt_consumed") is True:
                    terminal_event.set()
                return
            if purpose != FAST_LIVE_PURPOSE:
                raise FastLiveError("fast live message purpose invalid")
            if approval_required:
                raise FastLiveError(
                    "direct fast live order forbidden in Telegram approval mode"
                )
            required_attributes = {
                "intent_id": str(payload.get("intent_id") or ""),
                "request_sha256": str(payload.get("request_sha256") or ""),
            }
            if any(
                attributes.get(name) != value
                for name, value in required_attributes.items()
            ):
                raise FastLiveError("fast live order attributes mismatch")
            verified = verify_envelope(
                payload,
                runtime_authorization=runtime_now,
                key=key,
                expected_key_id=args.transport_key_id,
                observed_at=received_at,
            )
            order_verified = True
            result = execute_with_bounded_pre_attempt_retry(
                executor,
                verified,
            )
            result["envelope_created_at"] = verified["created_at"]
            result["message_received_at"] = received_at.isoformat()
            created = datetime.fromisoformat(str(verified["created_at"])).astimezone(UTC)
            result["source_to_receive_ms"] = (
                received_at - created
            ).total_seconds() * 1000
            try:
                result_message_id = publish_result(
                    result,
                    runtime_authorization=runtime_now,
                )
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            "status": "fast_live_result_publish_failed",
                            "error": type(exc).__name__,
                            "intent_id": result.get("intent_id"),
                            "request_sha256": result.get("request_sha256"),
                            "network_submission_attempt_consumed": result.get(
                                "network_submission_attempt_consumed"
                            )
                            is True,
                            "observed_at": _utc_now().isoformat(),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                message.nack()
                return
            result["result_message_id"] = result_message_id
            print(json.dumps(result, sort_keys=True, default=str), flush=True)
            message.ack()
            if result.get("network_submission_attempt_consumed") is True:
                terminal_event.set()
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "status": "fast_live_message_failed_closed",
                        "error": type(exc).__name__,
                        "message_id": str(getattr(message, "message_id", "") or ""),
                        "observed_at": received_at.isoformat(),
                        "network_submission_attempt_consumed": (
                            executor.attempt_path.exists()
                        ),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            if order_verified and executor.attempt_path.exists():
                message.nack()
            else:
                message.ack()

    future = subscriber.subscribe(subscription_path, callback=callback)
    try:
        while True:
            if terminal_event.wait(timeout=0.25):
                future.cancel()
                break
            if _utc_now() >= runtime_expires_at:
                future.cancel()
                break
            if future.done():
                future.result()
                break
    except KeyboardInterrupt:
        future.cancel()
    finally:
        book_cache.stop()
        refresher.stop()
        subscriber.close()
        result_publisher.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
