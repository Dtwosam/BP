from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, insert, select, update

from bp_engine.polymarket.models import PolymarketMarket
from bp_engine.storage.schema import polymarket_markets


class RuleChangeDetected(RuntimeError):
    """Raised when an existing market's rules fingerprint changes unexpectedly."""


@dataclass(frozen=True)
class UpsertResult:
    created: bool
    status_changed: bool


class PolymarketMarketRepository:
    def upsert(
        self,
        connection: Connection,
        market: PolymarketMarket,
        observed_at: datetime,
    ) -> UpsertResult:
        if observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")

        existing = connection.execute(
            select(polymarket_markets).where(
                polymarket_markets.c.condition_id == market.condition_id
            )
        ).mappings().one_or_none()

        if existing is None:
            connection.execute(
                insert(polymarket_markets).values(
                    **self._base_values(market),
                    discovered_at=observed_at,
                    updated_at=observed_at,
                )
            )
            return UpsertResult(created=True, status_changed=False)

        if existing["rules_hash"] != market.rules_hash:
            raise RuleChangeDetected(
                f"resolution rules changed for condition {market.condition_id}: "
                f"{existing['rules_hash']} -> {market.rules_hash}"
            )

        status_changed = any(
            (
                existing["active"] != market.active,
                existing["closed"] != market.closed,
                existing["accepting_orders"] != market.accepting_orders,
                existing["resolved_outcome"] != market.resolved_outcome,
            )
        )

        connection.execute(
            update(polymarket_markets)
            .where(polymarket_markets.c.condition_id == market.condition_id)
            .values(
                event_id=market.event_id,
                question=market.question,
                active=market.active,
                closed=market.closed,
                accepting_orders=market.accepting_orders,
                resolved_outcome=market.resolved_outcome,
                updated_at=observed_at,
            )
        )
        return UpsertResult(created=False, status_changed=status_changed)

    @staticmethod
    def _base_values(market: PolymarketMarket) -> dict[str, object]:
        return {
            "gamma_market_id": market.gamma_market_id,
            "event_id": market.event_id,
            "condition_id": market.condition_id,
            "slug": market.slug,
            "question": market.question,
            "horizon_seconds": market.horizon_seconds,
            "start_at": market.window_start_at,
            "end_at": market.window_end_at,
            "up_token_id": market.up_token_id,
            "down_token_id": market.down_token_id,
            "resolution_source": market.resolution_source,
            "rules_text": market.rules_text,
            "rules_hash": market.rules_hash,
            "active": market.active,
            "closed": market.closed,
            "accepting_orders": market.accepting_orders,
            "resolved_outcome": market.resolved_outcome,
        }
