from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

V3_FEATURE_VERSION = "core-v3-btc-native"
V3_OFFSETS_SECONDS = (60, 120, 180, 240)


@dataclass(frozen=True)
class V3FeatureTarget:
    condition_id: str
    slug: str
    horizon_seconds: int
    market_start_at: datetime
    market_end_at: datetime


@dataclass(frozen=True)
class BTCStateObservation:
    row_id: int
    bucket_at: datetime
    last_event_at: datetime
    source: str
    stream: str
    instrument: str
    price: Decimal
    state: dict[str, Any]
    fresh: bool
    age_seconds: float
