from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

FEATURE_VERSION = "core-v1"


@dataclass(frozen=True)
class FeatureTarget:
    condition_id: str
    slug: str
    horizon_seconds: int
    market_start_at: datetime
    market_end_at: datetime


@dataclass(frozen=True)
class MarketFeature:
    condition_id: str
    slug: str
    horizon_seconds: int
    market_start_at: datetime
    market_end_at: datetime
    feature_at: datetime
    feature_offset_seconds: int
    feature_version: str
    features: dict[str, Any]
    missing_flags: dict[str, Any]
    source_cutoffs: dict[str, Any]
    input_fingerprint: str
    feature_hash: str
    generated_at: datetime
