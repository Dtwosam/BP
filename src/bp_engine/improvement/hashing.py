from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("Decimal values must be finite")
    normalized = value.normalize()
    if normalized == 0:
        return "0"
    rendered = format(normalized, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def canonical_payload(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Enum):
        return canonical_payload(value.value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime values must be timezone-aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("float values must be finite")
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: canonical_payload(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("mapping keys must be strings")
            result[key] = canonical_payload(item)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [canonical_payload(item) for item in value]
    raise TypeError(f"unsupported canonical payload type: {type(value).__name__}")


def semantic_sha256(value: Any) -> str:
    encoded = json.dumps(
        canonical_payload(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def derive_id(prefix: str, digest: str) -> str:
    if not prefix.strip():
        raise ValueError("prefix must not be blank")
    if len(digest) != 64:
        raise ValueError("digest must be a 64-character SHA-256 digest")
    return f"{prefix}-{digest[:32]}"


def derive_seed(*parts: str) -> int:
    if not parts:
        raise ValueError("at least one seed part is required")
    if any(not isinstance(part, str) for part in parts):
        raise TypeError("seed parts must be strings")
    framed = b"".join(
        len(part.encode("utf-8")).to_bytes(8, "big") + part.encode("utf-8")
        for part in parts
    )
    digest = hashlib.sha256(framed).digest()
    return int.from_bytes(digest[:8], "big", signed=False)
