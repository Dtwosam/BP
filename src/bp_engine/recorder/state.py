from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


class MarketStateSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    bucket_at: datetime
    state_key: str
    source: str
    stream: str
    instrument: str
    market_id: str | None = None
    asset_id: str | None = None
    last_event_at: datetime
    state: dict[str, Any]

    @field_validator("bucket_at", "last_event_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)
