from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class PolymarketMarket(BaseModel):
    model_config = ConfigDict(frozen=True)

    gamma_market_id: str
    event_id: str | None
    condition_id: str
    slug: str
    question: str
    horizon_seconds: int
    window_start_at: datetime
    window_end_at: datetime
    up_token_id: str
    down_token_id: str
    resolution_source: str
    rules_text: str
    rules_hash: str
    active: bool
    closed: bool
    accepting_orders: bool
    resolved_outcome: Literal["Up", "Down"] | None = None
