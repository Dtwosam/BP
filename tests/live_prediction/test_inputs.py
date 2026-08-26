from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, insert

from bp_engine.backfill.polymarket_prices import (
    PriceHistoryPoint,
    PriceHistoryResponse,
)
from bp_engine.storage.schema import market_state_1s, metadata


class FakePriceHistoryClient:
    def __init__(
        self,
        response: PriceHistoryResponse,
        *,
        after_request=None,
    ) -> None:
        self.response = response
        self.after_request = after_request
        self.calls: list[dict[str, object]] = []

    async def get_history(
        self,
        asset_id: str,
        *,
        start: datetime,
        end: datetime,
        fidelity_minutes: int,
    ) -> PriceHistoryResponse:
        self.calls.append(
            {
                "asset_id": asset_id,
                "start": start,
                "end": end,
                "fidelity_minutes": fidelity_minutes,
            }
        )
        if self.after_request is not None:
            self.after_request()
        return self.response


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def _module():
    return importlib.import_module("bp_engine.live_prediction.inputs")


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return engine


def _times() -> tuple[datetime, datetime, datetime]:
    start = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    end = start + timedelta(minutes=5)
    scheduled = start + timedelta(minutes=4)
    return start, end, scheduled


def _response(
    scheduled: datetime,
    points: tuple[PriceHistoryPoint, ...],
) -> PriceHistoryResponse:
    return PriceHistoryResponse(
        points=points,
        request_params={
            "market": "up-token",
            "startTs": str(int((scheduled - timedelta(minutes=4)).timestamp())),
            "endTs": str(int(scheduled.timestamp())),
            "fidelity": "1",
        },
        raw_payload={
            "history": [
                {"t": int(point.observed_at.timestamp()), "p": str(point.price)}
                for point in points
            ]
        },
    )


def _insert_book(
    connection,
    *,
    scheduled: datetime,
    asset_id: str,
    best_bid: str,
    best_ask: str,
    age_seconds: int = 0,
    last_event_offset_seconds: int | None = None,
) -> None:
    bucket_at = scheduled - timedelta(seconds=age_seconds)
    last_event_at = (
        scheduled + timedelta(seconds=last_event_offset_seconds)
        if last_event_offset_seconds is not None
        else bucket_at
    )
    connection.execute(
        insert(market_state_1s).values(
            bucket_at=bucket_at,
            state_key=f"polymarket/market/condition-1/{asset_id}",
            source="polymarket",
            stream="market",
            instrument="condition-1",
            market_id="condition-1",
            asset_id=asset_id,
            last_event_at=last_event_at,
            state={
                "best_bid": best_bid,
                "best_ask": best_ask,
                "bid_depth": "100",
                "ask_depth": "120",
            },
        )
    )


@pytest.mark.asyncio
async def test_observer_requests_up_history_at_exact_schedule_and_selects_latest_safe_point() -> None:
    module = _module()
    engine = _engine()
    start, end, scheduled = _times()
    points = (
        PriceHistoryPoint(scheduled - timedelta(seconds=60), Decimal("0.57")),
        PriceHistoryPoint(scheduled, Decimal("0.61")),
        PriceHistoryPoint(scheduled + timedelta(seconds=1), Decimal("0.99")),
    )
    client = FakePriceHistoryClient(_response(scheduled, points))
    clock = MutableClock(scheduled + timedelta(seconds=1))

    with engine.begin() as connection:
        live_input = await module.observe_live_input(
            connection,
            client,
            condition_id="condition-1",
            up_token_id="up-token",
            down_token_id="down-token",
            market_start_at=start,
            market_end_at=end,
            scheduled_at=scheduled,
            clock=clock,
        )

    assert client.calls == [
        {
            "asset_id": "up-token",
            "start": start,
            "end": scheduled,
            "fidelity_minutes": 1,
        }
    ]
    assert live_input.market_probability_observed is True
    assert live_input.market_probability == pytest.approx(0.61)
    assert live_input.market_probability_observed_at == scheduled
    assert live_input.downloaded_at == scheduled + timedelta(seconds=1)
    assert live_input.price_request_params == client.response.request_params
    assert len(live_input.price_response_sha256) == 64
    assert live_input.price_response_payload == client.response.raw_payload
    assert live_input.predictors["pm_up_price"] == pytest.approx(0.61)
    assert len(live_input.input_fingerprint) == 64


@pytest.mark.asyncio
async def test_missing_history_is_explicit_and_never_replaced_by_book_midpoint() -> None:
    module = _module()
    engine = _engine()
    start, end, scheduled = _times()
    client = FakePriceHistoryClient(_response(scheduled, ()))

    with engine.begin() as connection:
        _insert_book(
            connection,
            scheduled=scheduled,
            asset_id="up-token",
            best_bid="0.58",
            best_ask="0.62",
        )
        live_input = await module.observe_live_input(
            connection,
            client,
            condition_id="condition-1",
            up_token_id="up-token",
            down_token_id="down-token",
            market_start_at=start,
            market_end_at=end,
            scheduled_at=scheduled,
            clock=lambda: scheduled + timedelta(seconds=1),
        )

    assert live_input.market_probability_observed is False
    assert live_input.market_probability is None
    assert live_input.market_probability_observed_at is None
    assert live_input.predictors["pm_up_price"] is None
    assert live_input.predictors["pm_up_mid"] == pytest.approx(0.60)


@pytest.mark.asyncio
async def test_books_use_exact_tokens_and_future_receipt_falls_back_to_safe_state() -> None:
    module = _module()
    engine = _engine()
    start, end, scheduled = _times()
    client = FakePriceHistoryClient(_response(scheduled, ()))

    with engine.begin() as connection:
        _insert_book(
            connection,
            scheduled=scheduled,
            asset_id="up-token",
            best_bid="0.57",
            best_ask="0.59",
            age_seconds=1,
        )
        _insert_book(
            connection,
            scheduled=scheduled,
            asset_id="up-token",
            best_bid="0.01",
            best_ask="0.99",
            last_event_offset_seconds=1,
        )
        _insert_book(
            connection,
            scheduled=scheduled,
            asset_id="down-token",
            best_bid="0.40",
            best_ask="0.42",
        )
        _insert_book(
            connection,
            scheduled=scheduled,
            asset_id="wrong-token",
            best_bid="0.10",
            best_ask="0.90",
        )

        live_input = await module.observe_live_input(
            connection,
            client,
            condition_id="condition-1",
            up_token_id="up-token",
            down_token_id="down-token",
            market_start_at=start,
            market_end_at=end,
            scheduled_at=scheduled,
            clock=lambda: scheduled + timedelta(seconds=1),
        )

    assert live_input.up_book is not None
    assert live_input.up_book.asset_id == "up-token"
    assert live_input.up_book.last_event_at == scheduled - timedelta(seconds=1)
    assert live_input.down_book is not None
    assert live_input.down_book.asset_id == "down-token"
    assert live_input.predictors["pm_up_best_ask"] == pytest.approx(0.59)
    assert live_input.predictors["pm_down_best_ask"] == pytest.approx(0.42)


@pytest.mark.asyncio
async def test_stale_book_remains_explicit_in_phase9_predictor_mapping() -> None:
    module = _module()
    engine = _engine()
    start, end, scheduled = _times()
    client = FakePriceHistoryClient(_response(scheduled, ()))

    with engine.begin() as connection:
        _insert_book(
            connection,
            scheduled=scheduled,
            asset_id="up-token",
            best_bid="0.58",
            best_ask="0.60",
            age_seconds=11,
        )
        live_input = await module.observe_live_input(
            connection,
            client,
            condition_id="condition-1",
            up_token_id="up-token",
            down_token_id="down-token",
            market_start_at=start,
            market_end_at=end,
            scheduled_at=scheduled,
            clock=lambda: scheduled + timedelta(seconds=1),
        )

    assert live_input.up_book is not None
    assert live_input.up_book.fresh is False
    assert live_input.predictors["pm_up_best_ask"] is None
    assert live_input.predictors["missing__pm_up_book_missing"] == 1.0
    assert live_input.predictors["missing__pm_up_book_stale"] == 1.0


@pytest.mark.asyncio
async def test_request_crossing_lateness_deadline_fails_before_input_is_returned() -> None:
    module = _module()
    engine = _engine()
    start, end, scheduled = _times()
    clock = MutableClock(scheduled + timedelta(seconds=5))
    client = FakePriceHistoryClient(
        _response(scheduled, ()),
        after_request=lambda: setattr(
            clock,
            "value",
            scheduled + timedelta(seconds=11),
        ),
    )

    with engine.begin() as connection:
        with pytest.raises(module.LiveInputDeadlineExceeded):
            await module.observe_live_input(
                connection,
                client,
                condition_id="condition-1",
                up_token_id="up-token",
                down_token_id="down-token",
                market_start_at=start,
                market_end_at=end,
                scheduled_at=scheduled,
                clock=clock,
            )
