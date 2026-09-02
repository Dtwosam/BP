from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

V2_FEATURE_VERSION = "core-v2-last-trade"


@dataclass(frozen=True)
class V2FeatureTarget:
    condition_id: str
    slug: str
    horizon_seconds: int
    market_start_at: datetime
    market_end_at: datetime
    up_token_id: str
    down_token_id: str


@dataclass(frozen=True)
class LastTradeObservation:
    compact_state_row_id: int
    compact_state_bucket_at: datetime
    compact_state_last_event_at: datetime
    asset_id: str
    price: Decimal
    size: Decimal | None
    side: str | None
    source_at: datetime
    received_at: datetime
    event_dedupe_key: str
