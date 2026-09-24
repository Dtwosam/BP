from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import httpx

from bp_engine.execution.telegram_approval import approval_record, new_pending
from bp_engine.execution.telegram_transport import (
    create_transport_envelope,
    encode_transport_key,
)

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "scripts" / "run_phase15_v3_telegram_pubsub_publish_worker.py"
KEY_ID = "phase15-telegram-transport-v1"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("telegram_pubsub_worker_test", WORKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prepared(now: datetime) -> dict[str, object]:
    return {
        "status": "prepared",
        "action": "submit",
        "intent_id": "live-intent-worker",
        "prediction_id": "prediction-worker",
        "paper_order_id": "paper-worker",
        "market_end_at": (now + timedelta(seconds=55)).isoformat(),
        "request": {
            "selected_side": "down",
            "limit_price": "0.72",
            "requested_shares": "6.81",
            "target_notional_usd": "5",
            "token_id": "token-worker",
            "action": "BUY",
        },
        "policy": {
            "policy_version": "v3-live-canary-v1",
            "max_submission_attempts": 1,
        },
    }


def _envelope(now: datetime) -> tuple[dict[str, object], bytes]:
    prepared = _prepared(now)
    pending = new_pending(
        prepared,
        telegram_user_id=111,
        telegram_chat_id=111,
        created_at=now,
        nonce="approval-nonce",
    )
    approval = approval_record(
        action="approve",
        pending=pending,
        callback_query_id="callback-id",
        approved_at=now + timedelta(seconds=1),
    )
    key = bytes(range(32))
    envelope = create_transport_envelope(
        prepared,
        approval=approval,
        key=key,
        key_id=KEY_ID,
        created_at=now + timedelta(seconds=2),
        nonce="publisher-worker-nonce",
    )
    return envelope, key


def _write_inputs(
    tmp_path: Path,
    envelope: dict[str, object],
    key: bytes,
) -> tuple[Path, Path, Path, Path]:
    outbox = tmp_path / "outbox"
    published = tmp_path / "published"
    failed = tmp_path / "failed"
    outbox.mkdir(mode=0o700)
    identity = __import__("hashlib").sha256(
        f"{envelope['intent_id']}\0{envelope['request_sha256']}".encode()
    ).hexdigest()
    envelope_path = outbox / f"{identity}.json"
    envelope_path.write_text(json.dumps(envelope), encoding="utf-8")
    envelope_path.chmod(0o600)
    key_path = tmp_path / "transport.key"
    key_path.write_text(encode_transport_key(key) + "\n", encoding="utf-8")
    key_path.chmod(0o600)
    return outbox, published, failed, key_path


def _metadata_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": "metadata-token",
            "expires_in": 3599,
            "token_type": "Bearer",
        },
        headers={"Metadata-Flavor": "Google"},
    )


def test_publisher_worker_publishes_once_then_skips_receipted_outbox(
    tmp_path: Path,
) -> None:
    worker = _load()
    now = datetime.now(UTC)
    envelope, key = _envelope(now)
    outbox, published, failed, key_path = _write_inputs(tmp_path, envelope, key)
    publish_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal publish_calls
        if request.url.host == "metadata.google.internal":
            return _metadata_response()
        if request.url.path.endswith(":publish"):
            publish_calls += 1
            return httpx.Response(200, json={"messageIds": ["message-1"]})
        raise AssertionError(request.url)

    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:
        first = worker.publish_pending_once(
            client=client,
            outbox_dir=outbox,
            receipt_dir=published,
            failure_dir=failed,
            key_path=key_path,
            expected_key_id=KEY_ID,
            project_id="project-123",
            topic_id="bp-telegram",
            observed_at=now + timedelta(seconds=3),
        )
        second = worker.publish_pending_once(
            client=client,
            outbox_dir=outbox,
            receipt_dir=published,
            failure_dir=failed,
            key_path=key_path,
            expected_key_id=KEY_ID,
            project_id="project-123",
            topic_id="bp-telegram",
            observed_at=now + timedelta(seconds=4),
        )

    assert [item["status"] for item in first] == ["published"]
    assert second == []
    assert publish_calls == 1
    assert len(list(published.glob("*.json"))) == 1
    assert len(list(outbox.glob("*.json"))) == 1
    assert list(failed.glob("*.json")) == []


def test_publisher_worker_retries_network_failure_with_same_envelope(
    tmp_path: Path,
) -> None:
    worker = _load()
    now = datetime.now(UTC)
    envelope, key = _envelope(now)
    outbox, published, failed, key_path = _write_inputs(tmp_path, envelope, key)
    publish_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal publish_calls
        if request.url.host == "metadata.google.internal":
            return _metadata_response()
        if request.url.path.endswith(":publish"):
            publish_calls += 1
            if publish_calls == 1:
                return httpx.Response(503, json={"error": {"message": "temporary"}})
            return httpx.Response(200, json={"messageIds": ["message-2"]})
        raise AssertionError(request.url)

    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:
        first = worker.publish_pending_once(
            client=client,
            outbox_dir=outbox,
            receipt_dir=published,
            failure_dir=failed,
            key_path=key_path,
            expected_key_id=KEY_ID,
            project_id="project-123",
            topic_id="bp-telegram",
            observed_at=now + timedelta(seconds=3),
        )
        second = worker.publish_pending_once(
            client=client,
            outbox_dir=outbox,
            receipt_dir=published,
            failure_dir=failed,
            key_path=key_path,
            expected_key_id=KEY_ID,
            project_id="project-123",
            topic_id="bp-telegram",
            observed_at=now + timedelta(seconds=4),
        )

    assert first[0]["status"] == "delivery_retry_later"
    assert second[0]["status"] == "published"
    assert publish_calls == 2
    assert len(list(outbox.glob("*.json"))) == 1
    assert len(list(published.glob("*.json"))) == 1


def test_publisher_worker_terminalizes_expired_envelope_without_network(
    tmp_path: Path,
) -> None:
    worker = _load()
    now = datetime.now(UTC)
    envelope, key = _envelope(now)
    outbox, published, failed, key_path = _write_inputs(tmp_path, envelope, key)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"network must not be used for expired envelope: {request.url}")

    expires_at = datetime.fromisoformat(str(envelope["expires_at"]))
    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:
        result = worker.publish_pending_once(
            client=client,
            outbox_dir=outbox,
            receipt_dir=published,
            failure_dir=failed,
            key_path=key_path,
            expected_key_id=KEY_ID,
            project_id="project-123",
            topic_id="bp-telegram",
            observed_at=expires_at,
        )
        repeat = worker.publish_pending_once(
            client=client,
            outbox_dir=outbox,
            receipt_dir=published,
            failure_dir=failed,
            key_path=key_path,
            expected_key_id=KEY_ID,
            project_id="project-123",
            topic_id="bp-telegram",
            observed_at=expires_at + timedelta(seconds=1),
        )

    assert result[0]["status"] == "terminal_invalid_or_expired_envelope"
    assert repeat == []
    assert len(list(failed.glob("*.json"))) == 1
    assert list(published.glob("*.json")) == []
    assert len(list(outbox.glob("*.json"))) == 1


def test_publisher_worker_source_has_no_executor_or_order_path() -> None:
    text = WORKER.read_text(encoding="utf-8")
    compile(text, str(WORKER), "exec")
    for marker in (
        "BP_TELEGRAM_PUBSUB_PUBLISH_WORKER_ENABLED",
        "delivery_retry_later",
        "terminal_invalid_or_expired_envelope",
        "network_publish_attempted",
        "executor_invoked",
        "real_order_submitted",
    ):
        assert marker in text
    for forbidden in (
        "gcloud",
        "post_order",
        "create_limit_order",
        "cancel_order",
        "phase15_v3_canary_arm",
        "phase15_v3_canary_executor",
        "PHASE15_ACCEPT_REAL_MONEY",
    ):
        assert forbidden not in text
