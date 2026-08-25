from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class MarketLabel:
    condition_id: str
    gamma_market_id: str
    slug: str
    horizon_seconds: int
    market_start_at: datetime
    market_end_at: datetime
    official_outcome: Literal["Up", "Down"]
    start_reference: Decimal | None
    end_reference: Decimal | None
    resolution_source: str
    rules_hash: str
    label_source: str
    label_version: str
    source_snapshot_sha256: str
    source_observed_at: datetime
    generated_at: datetime
