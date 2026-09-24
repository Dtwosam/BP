from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from bp_engine.execution.telegram_transport import (
    TRANSPORT_PURPOSE,
    TransportError,
    payload_sha256,
)

METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/"
    "instance/service-accounts/default/token"
)
PUBSUB_SCOPE = "https://www.googleapis.com/auth/pubsub"
PUBSUB_API_ROOT = "https://pubsub.googleapis.com/v1"
MAX_ENVELOPE_BYTES = 256 * 1024
MAX_ACCESS_TOKEN_BYTES = 16 * 1024


class PubSubTransportError(RuntimeError):
    pass


@dataclass(frozen=True)
class PulledEnvelope:
    ack_id: str
    message_id: str
    envelope: dict[str, Any]
    envelope_sha256: str


def _resource_segment(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized.encode()) > 255:
        raise PubSubTransportError(f"{label} invalid")
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in normalized):
        raise PubSubTransportError(f"{label} invalid")
    if "/" in normalized:
        raise PubSubTransportError(f"{label} invalid")
    return quote(normalized, safe="")


def _json_response(response: httpx.Response, operation: str) -> dict[str, Any]:
    if response.status_code < 200 or response.status_code >= 300:
        raise PubSubTransportError(
            f"{operation} returned HTTP {response.status_code}"
        )
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise PubSubTransportError(f"{operation} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise PubSubTransportError(f"{operation} returned invalid payload")
    return payload


def metadata_access_token(client: httpx.Client) -> str:
    try:
        response = client.get(
            METADATA_TOKEN_URL,
            headers={"Metadata-Flavor": "Google"},
            params={
                "enforce_scopes": "true",
                "scopes": PUBSUB_SCOPE,
            },
            timeout=3.0,
        )
    except httpx.HTTPError as exc:
        raise PubSubTransportError("metadata token request failed") from exc
    payload = _json_response(response, "metadata token request")
    token = str(payload.get("access_token") or "")
    token_type = str(payload.get("token_type") or "").lower()
    try:
        expires_in = int(payload.get("expires_in", 0))
    except (TypeError, ValueError) as exc:
        raise PubSubTransportError("metadata token expiry invalid") from exc
    if not token or len(token.encode()) > MAX_ACCESS_TOKEN_BYTES:
        raise PubSubTransportError("metadata access token invalid")
    if token_type != "bearer":
        raise PubSubTransportError("metadata token type invalid")
    if expires_in <= 0:
        raise PubSubTransportError("metadata token already expired")
    return token


def _auth_headers(access_token: str) -> dict[str, str]:
    token = access_token.strip()
    if not token or len(token.encode()) > MAX_ACCESS_TOKEN_BYTES:
        raise PubSubTransportError("access token invalid")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _envelope_bytes(envelope: Mapping[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            dict(envelope),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise PubSubTransportError("transport envelope is not JSON canonicalizable") from exc
    if not encoded or len(encoded) > MAX_ENVELOPE_BYTES:
        raise PubSubTransportError("transport envelope size invalid")
    return encoded


def envelope_attributes(envelope: Mapping[str, Any]) -> dict[str, str]:
    required = {
        "purpose": str(envelope.get("purpose") or ""),
        "key_id": str(envelope.get("key_id") or ""),
        "intent_id": str(envelope.get("intent_id") or ""),
        "request_sha256": str(envelope.get("request_sha256") or ""),
        "prepared_sha256": str(envelope.get("prepared_sha256") or ""),
    }
    if required["purpose"] != TRANSPORT_PURPOSE:
        raise PubSubTransportError("transport purpose mismatch")
    if not all(required.values()):
        raise PubSubTransportError("transport envelope routing attributes missing")
    return required


def publish_envelope(
    client: httpx.Client,
    *,
    project_id: str,
    topic_id: str,
    access_token: str,
    envelope: Mapping[str, Any],
) -> dict[str, str]:
    project = _resource_segment(project_id, "project id")
    topic = _resource_segment(topic_id, "topic id")
    encoded = _envelope_bytes(envelope)
    attributes = envelope_attributes(envelope)
    body = {
        "messages": [
            {
                "data": base64.b64encode(encoded).decode("ascii"),
                "attributes": attributes,
            }
        ]
    }
    try:
        response = client.post(
            f"{PUBSUB_API_ROOT}/projects/{project}/topics/{topic}:publish",
            headers=_auth_headers(access_token),
            json=body,
            timeout=5.0,
        )
    except httpx.HTTPError as exc:
        raise PubSubTransportError("Pub/Sub publish request failed") from exc
    payload = _json_response(response, "Pub/Sub publish")
    message_ids = payload.get("messageIds")
    if (
        not isinstance(message_ids, list)
        or len(message_ids) != 1
        or not isinstance(message_ids[0], str)
        or not message_ids[0]
    ):
        raise PubSubTransportError("Pub/Sub publish response missing message id")
    return {
        "message_id": message_ids[0],
        "envelope_sha256": payload_sha256(envelope),
    }


def pull_one(
    client: httpx.Client,
    *,
    project_id: str,
    subscription_id: str,
    access_token: str,
) -> PulledEnvelope | None:
    project = _resource_segment(project_id, "project id")
    subscription = _resource_segment(subscription_id, "subscription id")
    try:
        response = client.post(
            f"{PUBSUB_API_ROOT}/projects/{project}/subscriptions/{subscription}:pull",
            headers=_auth_headers(access_token),
            json={"maxMessages": 1},
            timeout=5.0,
        )
    except httpx.HTTPError as exc:
        raise PubSubTransportError("Pub/Sub pull request failed") from exc
    payload = _json_response(response, "Pub/Sub pull")
    received = payload.get("receivedMessages", [])
    if received is None:
        received = []
    if not isinstance(received, list):
        raise PubSubTransportError("Pub/Sub pull response invalid")
    if not received:
        return None
    if len(received) != 1 or not isinstance(received[0], dict):
        raise PubSubTransportError("Pub/Sub pull returned unexpected message count")

    item = received[0]
    ack_id = str(item.get("ackId") or "")
    message = item.get("message")
    if not ack_id or not isinstance(message, dict):
        raise PubSubTransportError("Pub/Sub pulled message missing ack metadata")
    message_id = str(message.get("messageId") or "")
    data = message.get("data")
    attributes = message.get("attributes")
    if not message_id or not isinstance(data, str) or not isinstance(attributes, dict):
        raise PubSubTransportError("Pub/Sub pulled message invalid")
    try:
        raw = base64.b64decode(data.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise PubSubTransportError("Pub/Sub message data is not valid base64") from exc
    if not raw or len(raw) > MAX_ENVELOPE_BYTES:
        raise PubSubTransportError("Pub/Sub message data size invalid")
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PubSubTransportError("Pub/Sub message envelope JSON invalid") from exc
    if not isinstance(envelope, dict):
        raise PubSubTransportError("Pub/Sub message envelope must be an object")

    expected_attributes = envelope_attributes(envelope)
    normalized_attributes = {str(key): str(value) for key, value in attributes.items()}
    if normalized_attributes != expected_attributes:
        raise PubSubTransportError("Pub/Sub message attributes do not match envelope")

    return PulledEnvelope(
        ack_id=ack_id,
        message_id=message_id,
        envelope=envelope,
        envelope_sha256=payload_sha256(envelope),
    )


def acknowledge(
    client: httpx.Client,
    *,
    project_id: str,
    subscription_id: str,
    access_token: str,
    ack_id: str,
) -> None:
    project = _resource_segment(project_id, "project id")
    subscription = _resource_segment(subscription_id, "subscription id")
    if not ack_id:
        raise PubSubTransportError("Pub/Sub ack id missing")
    try:
        response = client.post(
            (
                f"{PUBSUB_API_ROOT}/projects/{project}/subscriptions/"
                f"{subscription}:acknowledge"
            ),
            headers=_auth_headers(access_token),
            json={"ackIds": [ack_id]},
            timeout=5.0,
        )
    except httpx.HTTPError as exc:
        raise PubSubTransportError("Pub/Sub acknowledge request failed") from exc
    if response.status_code < 200 or response.status_code >= 300:
        raise PubSubTransportError(
            f"Pub/Sub acknowledge returned HTTP {response.status_code}"
        )
