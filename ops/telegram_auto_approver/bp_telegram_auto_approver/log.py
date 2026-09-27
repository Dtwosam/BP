from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Protocol

_REDACTED_PARTS = ("api_hash", "api_id", "hash", "token", "secret", "phone", "session", "private")


class EventLogger(Protocol):
    def emit(self, event: str, **fields: object) -> None: ...


class JsonLogger:
    def emit(self, event: str, **fields: object) -> None:
        payload: dict[str, object] = {
            "event": event,
            "ts": datetime.now(UTC).isoformat(),
        }
        for key, value in fields.items():
            lowered = key.lower()
            if any(part in lowered for part in _REDACTED_PARTS):
                continue
            payload[key] = value
        print(json.dumps(payload, sort_keys=True, default=str), flush=True)


class ListLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(self, event: str, **fields: object) -> None:
        self.events.append((event, dict(fields)))
