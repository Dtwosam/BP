from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


def canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def build_dedupe_key(
    *,
    source: str,
    stream: str,
    event_type: str,
    source_timestamp: datetime | None,
    sequence: str | int | None,
    payload: Mapping[str, Any],
) -> str:
    identity = {
        "source": source,
        "stream": stream,
        "event_type": event_type,
        "source_timestamp": _utc(source_timestamp).isoformat() if source_timestamp else None,
        "sequence": str(sequence) if sequence is not None else None,
        "payload_hash": canonical_payload_hash(payload),
    }
    return canonical_payload_hash(identity)


class RawEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    stream: str
    instrument: str
    event_type: str
    source_timestamp: datetime | None = None
    received_at: datetime
    sequence: str | None = None
    market_id: str | None = None
    asset_id: str | None = None
    payload: dict[str, Any]
    dedupe_key: str

    @model_validator(mode="before")
    @classmethod
    def populate_dedupe_key(cls, data: object) -> object:
        if not isinstance(data, dict) or data.get("dedupe_key"):
            return data
        payload = data.get("payload")
        if not isinstance(payload, dict):
            return data
        updated = dict(data)
        updated["dedupe_key"] = build_dedupe_key(
            source=str(data.get("source", "")),
            stream=str(data.get("stream", "")),
            event_type=str(data.get("event_type", "")),
            source_timestamp=data.get("source_timestamp"),
            sequence=data.get("sequence"),
            payload=payload,
        )
        return updated

    @field_validator("source_timestamp", "received_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        return _utc(value)

    @classmethod
    def build(
        cls,
        *,
        source: str,
        stream: str,
        instrument: str,
        event_type: str,
        source_timestamp: datetime | None,
        received_at: datetime,
        payload: Mapping[str, Any],
        sequence: str | int | None = None,
        market_id: str | None = None,
        asset_id: str | None = None,
    ) -> "RawEvent":
        payload_dict = dict(payload)
        return cls(
            source=source,
            stream=stream,
            instrument=instrument,
            event_type=event_type,
            source_timestamp=source_timestamp,
            received_at=received_at,
            sequence=str(sequence) if sequence is not None else None,
            market_id=market_id,
            asset_id=asset_id,
            payload=payload_dict,
        )


class FeedIncident(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    stream: str
    incident_type: str
    observed_at: datetime
    details: dict[str, Any] = {}

    @field_validator("observed_at")
    @classmethod
    def normalize_observed_at(cls, value: datetime) -> datetime:
        normalized = _utc(value)
        assert normalized is not None
        return normalized
