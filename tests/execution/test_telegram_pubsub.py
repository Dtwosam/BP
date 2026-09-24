from __future__ import annotations

import base64
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from bp_engine.execution.telegram_approval import approval_record, new_pending
from bp_engine.execution.telegram_pubsub import (
    METADATA_TOKEN_URL,
    PUBSUB_SCOPE,
    PubSubTransportError,
    acknowledge,
    envelope_attributes,
    metadata_access_token,
    publish_envelope,
    pull_one,
)
from bp_engine.execution.telegram_transport import (
    create_transport_envelope,
    encode_transport_key,
    payload_sha256,
)

ROOT = Path(__file__).resolve().parents[2]
PUBLISH_SCRIPT = ROOT / "scripts" / "run_phase15_v3_telegram_pubsub_publish.py"
RECEIVE_SCRIPT = ROOT / "scripts" / "run_phase15_v3_telegram_pubsub_receive.py"
KEY_ID = "phase15-telegram-transport-v1"


def _load_script(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prepared(now: datetime) -> dict[str, object]:
    return {
        "status": "prepared",
        "action": "submit",
        "intent_id": "live-intent-pubsub",
        "prediction_id": "prediction-pubsub",
        "paper_order_id": "paper-pubsub",
        "market_end_at": (now + timedelta(seconds=55)).isoformat(),
        "request": {
            "selected_side": "down",
            "limit_price": "0.72",
            "requested_shares": "6.81",
            "target_notional_usd": "5",
            "token_id": "token-pubsub",
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
        nonce="pubsub-transport-nonce",
    )
    return envelope, key


def _metadata_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": "metadata-access-token",
            "expires_in": 3599,
            "token_type": "Bearer",
        },
        headers={"Metadata-Flavor": "Google"},
    )


def test_metadata_token_uses_compute_metadata_server_and_pubsub_scope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(METADATA_TOKEN_URL)
        assert request.headers["Metadata-Flavor"] == "Google"
        assert request.url.params["enforce_scopes"] == "true"
        assert request.url.params["scopes"] == PUBSUB_SCOPE
        return _metadata_response()

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert metadata_access_token(client) == "metadata-access-token"


def test_metadata_token_rejects_missing_google_response_flavor() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": "must-not-be-trusted",
                "expires_in": 3599,
                "token_type": "Bearer",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PubSubTransportError, match="response flavor invalid"):
            metadata_access_token(client)


def test_pubsub_publish_encodes_exact_envelope_and_routing_attributes() -> None:
    now = datetime(2026, 9, 24, 21, 30, tzinfo=UTC)
    envelope, _ = _envelope(now)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(
            "/projects/project-123/topics/bp-telegram:publish"
        )
        assert request.headers["Authorization"] == "Bearer token"
        payload = json.loads(request.content)
        assert len(payload["messages"]) == 1
        message = payload["messages"][0]
        decoded = json.loads(base64.b64decode(message["data"]))
        assert decoded == envelope
        assert message["attributes"] == envelope_attributes(envelope)
        return httpx.Response(200, json={"messageIds": ["message-1"]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = publish_envelope(
            client,
            project_id="project-123",
            topic_id="bp-telegram",
            access_token="token",
            envelope=envelope,
        )
    assert result["message_id"] == "message-1"
    assert result["envelope_sha256"] == payload_sha256(envelope)


def test_pubsub_pull_and_ack_preserve_exact_envelope() -> None:
    now = datetime(2026, 9, 24, 21, 30, tzinfo=UTC)
    envelope, _ = _envelope(now)
    encoded = base64.b64encode(
        json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    ).decode("ascii")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith(":pull"):
            return httpx.Response(
                200,
                json={
                    "receivedMessages": [
                        {
                            "ackId": "ack-secret",
                            "message": {
                                "messageId": "message-1",
                                "data": encoded,
                                "attributes": envelope_attributes(envelope),
                            },
                        }
                    ]
                },
            )
        if request.url.path.endswith(":acknowledge"):
            payload = json.loads(request.content)
            assert payload == {"ackIds": ["ack-secret"]}
            return httpx.Response(200, json={})
        raise AssertionError(request.url)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        pulled = pull_one(
            client,
            project_id="project-123",
            subscription_id="bp-telegram-exec",
            access_token="token",
        )
        assert pulled is not None
        assert pulled.message_id == "message-1"
        assert pulled.envelope == envelope
        acknowledge(
            client,
            project_id="project-123",
            subscription_id="bp-telegram-exec",
            access_token="token",
            ack_id=pulled.ack_id,
        )
    assert calls == [
        "/v1/projects/project-123/subscriptions/bp-telegram-exec:pull",
        "/v1/projects/project-123/subscriptions/bp-telegram-exec:acknowledge",
    ]


def test_pubsub_pull_rejects_attribute_mismatch() -> None:
    now = datetime(2026, 9, 24, 21, 30, tzinfo=UTC)
    envelope, _ = _envelope(now)
    encoded = base64.b64encode(json.dumps(envelope).encode()).decode("ascii")
    attributes = envelope_attributes(envelope)
    attributes["intent_id"] = "wrong-intent"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "receivedMessages": [
                    {
                        "ackId": "ack-secret",
                        "message": {
                            "messageId": "message-1",
                            "data": encoded,
                            "attributes": attributes,
                        },
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PubSubTransportError, match="attributes do not match"):
            pull_one(
                client,
                project_id="project-123",
                subscription_id="bp-telegram-exec",
                access_token="token",
            )


def test_pubsub_publish_then_receive_is_durable_idempotent_and_executor_free(
    tmp_path: Path,
) -> None:
    publish_script = _load_script(PUBLISH_SCRIPT, "telegram_pubsub_publish_test")
    receive_script = _load_script(RECEIVE_SCRIPT, "telegram_pubsub_receive_test")
    now = datetime(2026, 9, 24, 21, 30, tzinfo=UTC)
    envelope, key = _envelope(now)
    envelope_path = tmp_path / "envelope.json"
    key_path = tmp_path / "transport.key"
    receipt_dir = tmp_path / "published"
    inbox_dir = tmp_path / "inbox"
    envelope_path.write_text(json.dumps(envelope), encoding="utf-8")
    envelope_path.chmod(0o600)
    key_path.write_text(encode_transport_key(key) + "\n", encoding="utf-8")
    key_path.chmod(0o600)

    published_data: dict[str, object] = {}
    publish_calls = 0

    def publish_handler(request: httpx.Request) -> httpx.Response:
        nonlocal publish_calls
        if request.url.host == "metadata.google.internal":
            return _metadata_response()
        if request.url.path.endswith(":publish"):
            publish_calls += 1
            body = json.loads(request.content)
            published_data["message"] = body["messages"][0]
            return httpx.Response(200, json={"messageIds": ["message-1"]})
        raise AssertionError(request.url)

    with httpx.Client(transport=httpx.MockTransport(publish_handler)) as client:
        first = publish_script.publish_transport(
            client=client,
            envelope_path=envelope_path,
            key_path=key_path,
            expected_key_id=KEY_ID,
            project_id="project-123",
            topic_id="bp-telegram",
            receipt_dir=receipt_dir,
            observed_at=now + timedelta(seconds=3),
        )
        second = publish_script.publish_transport(
            client=client,
            envelope_path=envelope_path,
            key_path=key_path,
            expected_key_id=KEY_ID,
            project_id="project-123",
            topic_id="bp-telegram",
            receipt_dir=receipt_dir,
            observed_at=now + timedelta(seconds=4),
        )
    assert first["status"] == "published"
    assert second["status"] == "already_published"
    assert first["executor_invoked"] is False
    assert first["real_order_submitted"] is False
    assert publish_calls == 1

    message = published_data["message"]
    assert isinstance(message, dict)
    ack_calls = 0

    def receive_handler(request: httpx.Request) -> httpx.Response:
        nonlocal ack_calls
        if request.url.host == "metadata.google.internal":
            return _metadata_response()
        if request.url.path.endswith(":pull"):
            return httpx.Response(
                200,
                json={
                    "receivedMessages": [
                        {
                            "ackId": "ack-1",
                            "message": {
                                "messageId": "message-1",
                                "data": message["data"],
                                "attributes": message["attributes"],
                            },
                        }
                    ]
                },
            )
        if request.url.path.endswith(":acknowledge"):
            ack_calls += 1
            return httpx.Response(200, json={})
        raise AssertionError(request.url)

    with httpx.Client(transport=httpx.MockTransport(receive_handler)) as client:
        received = receive_script.receive_transport(
            client=client,
            key_path=key_path,
            expected_key_id=KEY_ID,
            project_id="project-123",
            subscription_id="bp-telegram-exec",
            inbox_dir=inbox_dir,
            observed_at=now + timedelta(seconds=4),
        )
        duplicate = receive_script.receive_transport(
            client=client,
            key_path=key_path,
            expected_key_id=KEY_ID,
            project_id="project-123",
            subscription_id="bp-telegram-exec",
            inbox_dir=inbox_dir,
            observed_at=now + timedelta(seconds=5),
        )
    assert received["status"] == "received_acknowledged"
    assert duplicate["status"] == "duplicate_acknowledged"
    assert received["executor_invoked"] is False
    assert received["real_order_submitted"] is False
    assert ack_calls == 2
    stored = json.loads(Path(received["envelope_path"]).read_text(encoding="utf-8"))
    assert stored == envelope


def test_pubsub_entrypoints_disable_environment_proxy_inheritance() -> None:
    for path in (PUBLISH_SCRIPT, RECEIVE_SCRIPT):
        text = path.read_text(encoding="utf-8")
        assert "httpx.Client(trust_env=False)" in text
        assert "httpx.Client()" not in text


def test_pubsub_runtime_sources_do_not_invoke_trading_or_gcloud() -> None:
    for path in (
        ROOT / "src/bp_engine/execution/telegram_pubsub.py",
        PUBLISH_SCRIPT,
        RECEIVE_SCRIPT,
    ):
        text = path.read_text(encoding="utf-8")
        compile(text, str(path), "exec")
        for forbidden in (
            "gcloud",
            "post_order",
            "create_limit_order",
            "cancel_order",
            "phase15_v3_canary_arm",
            "phase15_v3_canary_executor",
            "PHASE15_ACCEPT_REAL_MONEY",
            "POLYMARKET_PRIVATE_KEY=",
            "POLYMARKET_WALLET_ADDRESS=",
        ):
            assert forbidden not in text
