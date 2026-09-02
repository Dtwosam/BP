from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Connection, and_, select

from bp_engine.features.v2_models import LastTradeObservation
from bp_engine.storage.schema import market_state_1s


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_explicit_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not result.is_finite():
        return None
    return result


def _last_trade_from_row(row: Any, *, feature_at: datetime) -> LastTradeObservation | None:
    state = row.state
    if not isinstance(state, dict):
        return None

    source_at = _parse_explicit_utc(state.get("last_trade_source_at"))
    received_at = _parse_explicit_utc(state.get("last_trade_received_at"))
    dedupe_key = state.get("last_trade_event_dedupe_key")
    price = _decimal(state.get("last_trade_price"))
    if (
        source_at is None
        or received_at is None
        or not isinstance(dedupe_key, str)
        or not dedupe_key
        or price is None
        or price < 0
        or price > 1
    ):
        return None

    cutoff = _utc(feature_at)
    if source_at > cutoff or received_at > cutoff:
        return None

    size = _decimal(state.get("last_trade_size"))
    side_value = state.get("last_trade_side")
    side = str(side_value) if side_value is not None else None

    return LastTradeObservation(
        compact_state_row_id=int(row.id),
        compact_state_bucket_at=_utc(row.bucket_at),
        compact_state_last_event_at=_utc(row.last_event_at),
        asset_id=str(row.asset_id),
        price=price,
        size=size,
        side=side,
        source_at=source_at,
        received_at=received_at,
        event_dedupe_key=dedupe_key,
    )


class V2FeatureSourceReader:
    def latest_polymarket_last_trade(
        self,
        connection: Connection,
        *,
        condition_id: str,
        asset_id: str,
        feature_at: datetime,
    ) -> LastTradeObservation | None:
        cutoff = _utc(feature_at)
        row = connection.execute(
            select(market_state_1s)
            .where(
                and_(
                    market_state_1s.c.source == "polymarket",
                    market_state_1s.c.stream == "market",
                    market_state_1s.c.instrument == condition_id,
                    market_state_1s.c.asset_id == asset_id,
                    market_state_1s.c.bucket_at <= cutoff,
                    market_state_1s.c.last_event_at <= cutoff,
                )
            )
            .order_by(
                market_state_1s.c.bucket_at.desc(),
                market_state_1s.c.last_event_at.desc(),
                market_state_1s.c.id.desc(),
            )
            .limit(1)
        ).mappings().first()
        if row is None:
            return None
        return _last_trade_from_row(row, feature_at=cutoff)
