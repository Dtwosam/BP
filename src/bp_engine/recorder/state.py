from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from bp_engine.recorder.models import RawEvent


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _sum_sizes(levels: Mapping[str, str]) -> str:
    return _decimal_text(sum((Decimal(size) for size in levels.values()), Decimal("0")))


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


@dataclass
class _TrackedState:
    source: str
    stream: str
    instrument: str
    market_id: str | None
    asset_id: str | None
    last_event_at: datetime
    state: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Book:
    bids: dict[str, str] = field(default_factory=dict)
    asks: dict[str, str] = field(default_factory=dict)


class MarketStateReducer:
    """Reduce immutable raw events into compact current market state."""

    def __init__(self) -> None:
        self._states: dict[str, _TrackedState] = {}
        self._bybit_books: dict[str, _Book] = {}

    @staticmethod
    def _state_key(event: RawEvent, asset_id: str | None = None) -> str:
        parts = [event.source, event.stream, event.instrument]
        effective_asset = asset_id or event.asset_id
        if effective_asset:
            parts.append(effective_asset)
        return "/".join(parts)

    def _touch(
        self, event: RawEvent, *, asset_id: str | None = None
    ) -> tuple[str, _TrackedState]:
        key = self._state_key(event, asset_id)
        effective_asset = asset_id or event.asset_id
        tracked = self._states.get(key)
        if tracked is None:
            tracked = _TrackedState(
                source=event.source,
                stream=event.stream,
                instrument=event.instrument,
                market_id=event.market_id,
                asset_id=effective_asset,
                last_event_at=event.received_at,
            )
            self._states[key] = tracked
        elif event.received_at > tracked.last_event_at:
            tracked.last_event_at = event.received_at
        return key, tracked

    @staticmethod
    def _best(levels: Mapping[str, str], *, highest: bool) -> str | None:
        if not levels:
            return None
        return max(levels, key=Decimal) if highest else min(levels, key=Decimal)

    @staticmethod
    def _set_quote_state(
        state: dict[str, Any],
        bids: Mapping[str, str],
        asks: Mapping[str, str],
    ) -> None:
        best_bid = MarketStateReducer._best(bids, highest=True)
        best_ask = MarketStateReducer._best(asks, highest=False)
        if best_bid is None:
            state.pop("best_bid", None)
        else:
            state["best_bid"] = best_bid
        if best_ask is None:
            state.pop("best_ask", None)
        else:
            state["best_ask"] = best_ask
        state["bid_depth"] = _sum_sizes(bids)
        state["ask_depth"] = _sum_sizes(asks)

    @staticmethod
    def _levels(payload: object) -> dict[str, str]:
        result: dict[str, str] = {}
        if not isinstance(payload, list):
            return result
        for level in payload:
            if isinstance(level, Mapping):
                price = level.get("price")
                size = level.get("size")
            elif isinstance(level, list) and len(level) >= 2:
                price, size = level[0], level[1]
            else:
                continue
            if price is not None and size is not None:
                result[str(price)] = str(size)
        return result

    @staticmethod
    def _apply_levels(book_side: dict[str, str], payload: object) -> None:
        for price, size in MarketStateReducer._levels(payload).items():
            if Decimal(size) == 0:
                book_side.pop(price, None)
            else:
                book_side[price] = size

    def _observe_polymarket(self, event: RawEvent) -> None:
        payload = event.payload
        if event.event_type == "book":
            _, tracked = self._touch(event)
            bids = self._levels(payload.get("bids"))
            asks = self._levels(payload.get("asks"))
            self._set_quote_state(tracked.state, bids, asks)
            return

        if event.event_type == "price_change":
            changes = payload.get("price_changes")
            if not isinstance(changes, list):
                return
            for change in changes:
                if not isinstance(change, Mapping) or not change.get("asset_id"):
                    continue
                _, tracked = self._touch(event, asset_id=str(change["asset_id"]))
                field_map = {
                    "best_bid": "best_bid",
                    "best_ask": "best_ask",
                    "price": "last_change_price",
                    "size": "last_change_size",
                    "side": "last_change_side",
                }
                for source_name, state_name in field_map.items():
                    value = change.get(source_name)
                    if value is not None:
                        tracked.state[state_name] = str(value)
            return

        if event.event_type == "last_trade_price":
            asset_id = payload.get("asset_id") or event.asset_id
            _, tracked = self._touch(
                event,
                asset_id=str(asset_id) if asset_id is not None else None,
            )
            field_map = {
                "price": "last_price",
                "size": "last_trade_size",
                "side": "last_trade_side",
            }
            for source_name, state_name in field_map.items():
                value = payload.get(source_name)
                if value is not None:
                    tracked.state[state_name] = str(value)

    def _observe_bybit(self, event: RawEvent) -> None:
        payload = event.payload
        data = payload.get("data")
        key, tracked = self._touch(event)

        if event.event_type in {"orderbook_snapshot", "orderbook_delta"}:
            if not isinstance(data, Mapping):
                return
            book = self._bybit_books.setdefault(key, _Book())
            if event.event_type == "orderbook_snapshot":
                book.bids.clear()
                book.asks.clear()
            self._apply_levels(book.bids, data.get("b"))
            self._apply_levels(book.asks, data.get("a"))
            self._set_quote_state(tracked.state, book.bids, book.asks)
            return

        if event.event_type == "ticker" and isinstance(data, Mapping):
            field_map = {
                "lastPrice": "last_price",
                "markPrice": "mark_price",
                "indexPrice": "index_price",
                "fundingRate": "funding_rate",
                "openInterest": "open_interest",
                "openInterestValue": "open_interest_value",
                "nextFundingTime": "next_funding_time",
                "bid1Price": "best_bid",
                "ask1Price": "best_ask",
            }
            for source_name, state_name in field_map.items():
                value = data.get(source_name)
                if value is not None:
                    tracked.state[state_name] = str(value)
            return

        if event.event_type == "trade" and isinstance(data, list) and data:
            trade = data[-1]
            if not isinstance(trade, Mapping):
                return
            field_map = {
                "p": "last_price",
                "v": "last_trade_size",
                "S": "last_trade_side",
                "T": "last_trade_time",
            }
            for source_name, state_name in field_map.items():
                value = trade.get(source_name)
                if value is not None:
                    tracked.state[state_name] = str(value)

    def _observe_coinbase(self, event: RawEvent) -> None:
        payload = event.payload
        events = payload.get("events")
        if (
            not isinstance(events, list)
            or not events
            or not isinstance(events[0], Mapping)
        ):
            return
        first = events[0]
        _, tracked = self._touch(event)

        if event.event_type.startswith("ticker_"):
            tickers = first.get("tickers")
            if (
                not isinstance(tickers, list)
                or not tickers
                or not isinstance(tickers[0], Mapping)
            ):
                return
            ticker = tickers[0]
            field_map = {
                "price": "last_price",
                "best_bid": "best_bid",
                "best_bid_quantity": "best_bid_size",
                "best_ask": "best_ask",
                "best_ask_quantity": "best_ask_size",
                "volume_24_h": "volume_24h",
            }
            for source_name, state_name in field_map.items():
                value = ticker.get(source_name)
                if value is not None:
                    tracked.state[state_name] = str(value)
            return

        if event.event_type.startswith("market_trades_"):
            trades = first.get("trades")
            if (
                not isinstance(trades, list)
                or not trades
                or not isinstance(trades[-1], Mapping)
            ):
                return
            trade = trades[-1]
            field_map = {
                "price": "last_price",
                "size": "last_trade_size",
                "side": "last_trade_side",
                "time": "last_trade_time",
            }
            for source_name, state_name in field_map.items():
                value = trade.get(source_name)
                if value is not None:
                    tracked.state[state_name] = str(value)

    def observe(self, event: RawEvent) -> None:
        if event.source == "polymarket":
            self._observe_polymarket(event)
        elif event.source == "bybit":
            self._observe_bybit(event)
        elif event.source == "coinbase":
            self._observe_coinbase(event)

    def snapshots(self, bucket_at: datetime) -> list[MarketStateSnapshot]:
        bucket = _utc(bucket_at).replace(microsecond=0)
        return [
            MarketStateSnapshot(
                bucket_at=bucket,
                state_key=key,
                source=tracked.source,
                stream=tracked.stream,
                instrument=tracked.instrument,
                market_id=tracked.market_id,
                asset_id=tracked.asset_id,
                last_event_at=tracked.last_event_at,
                state=dict(tracked.state),
            )
            for key, tracked in sorted(self._states.items())
        ]


SnapshotWriter = Callable[[list[MarketStateSnapshot]], Awaitable[None]]
NowFactory = Callable[[], datetime]


class MarketStateSnapshotter:
    """Persist the reducer's current state at a fixed cadence and on shutdown."""

    def __init__(
        self,
        *,
        reducer: MarketStateReducer,
        write_snapshots: SnapshotWriter,
        interval_seconds: float = 1.0,
        now: NowFactory | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be greater than zero")
        self._reducer = reducer
        self._write_snapshots = write_snapshots
        self._interval_seconds = interval_seconds
        self._now = now or (lambda: datetime.now(UTC))

    async def _flush(self) -> None:
        snapshots = self._reducer.snapshots(self._now())
        if snapshots:
            await self._write_snapshots(snapshots)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                await self._flush()
                continue
            break
        await self._flush()
