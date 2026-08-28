from __future__ import annotations

import inspect
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection

from bp_engine.backfill.polymarket_prices import PolymarketPriceHistoryClient
from bp_engine.features.calculators import FeatureGroup, book_state
from bp_engine.features.hashing import canonical_hash
from bp_engine.features.sources import FeatureSourceReader, StateObservation

LIVE_PRICE_SOURCE = "polymarket_clob"
LIVE_PRICE_DATASET = "prices_history"
MAX_LATENESS_SECONDS = 10
DEADLINE_COMPLETION_RESERVE_SECONDS = 0.5


class LiveInputDeadlineExceeded(RuntimeError):
    """Raised when live input observation misses the V1 prediction deadline."""


class LiveInputIntegrityError(ValueError):
    """Raised when prospective input metadata or provenance is inconsistent."""


@dataclass(frozen=True)
class LiveBookInput:
    asset_id: str
    state_key: str
    bucket_at: datetime
    last_event_at: datetime
    fresh: bool
    age_seconds: float
    state: Mapping[str, Any]


@dataclass(frozen=True)
class LiveMarketInput:
    condition_id: str
    up_token_id: str
    down_token_id: str
    market_start_at: datetime
    market_end_at: datetime
    scheduled_at: datetime
    downloaded_at: datetime
    price_source: str
    price_dataset: str
    price_request_params: Mapping[str, str]
    price_response_sha256: str
    price_response_payload: Mapping[str, Any]
    market_probability_observed: bool
    market_probability: float | None
    market_probability_observed_at: datetime | None
    up_book: LiveBookInput | None
    down_book: LiveBookInput | None
    predictors: Mapping[str, Any]
    input_fingerprint: str


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise LiveInputIntegrityError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _validate_window(
    market_start_at: datetime,
    market_end_at: datetime,
    scheduled_at: datetime,
) -> tuple[datetime, datetime, datetime]:
    start = _utc(market_start_at, "market_start_at")
    end = _utc(market_end_at, "market_end_at")
    scheduled = _utc(scheduled_at, "scheduled_at")
    if end <= start:
        raise LiveInputIntegrityError("market_end_at must be after market_start_at")
    if not start < scheduled < end:
        raise LiveInputIntegrityError("scheduled_at must be strictly inside market window")
    return start, end, scheduled


def _clock_time(clock: Callable[[], datetime]) -> datetime:
    return _utc(clock(), "clock")


def _check_deadline(
    current: datetime,
    *,
    scheduled_at: datetime,
    market_end_at: datetime,
    max_lateness_seconds: int,
) -> None:
    if max_lateness_seconds < 0:
        raise LiveInputIntegrityError("max_lateness_seconds must be non-negative")
    if current < scheduled_at:
        raise LiveInputIntegrityError("live input observation cannot run before scheduled_at")
    if current > scheduled_at + timedelta(seconds=max_lateness_seconds):
        raise LiveInputDeadlineExceeded("live input observation exceeded lateness deadline")
    if current >= market_end_at:
        raise LiveInputDeadlineExceeded("live input observation reached market end")


def _request_timeout_seconds(
    current: datetime,
    *,
    scheduled_at: datetime,
    max_lateness_seconds: int,
) -> float:
    deadline = scheduled_at + timedelta(seconds=max_lateness_seconds)
    remaining = (deadline - current).total_seconds()
    timeout_seconds = remaining - DEADLINE_COMPLETION_RESERVE_SECONDS
    if timeout_seconds <= 0:
        raise LiveInputDeadlineExceeded(
            "insufficient live input deadline budget for price-history request"
        )
    return timeout_seconds


def _history_supports_timeout(client: Any) -> bool:
    try:
        return "timeout_seconds" in inspect.signature(client.get_history).parameters
    except (TypeError, ValueError):
        return False


def _expected_request_params(
    *,
    up_token_id: str,
    market_start_at: datetime,
    scheduled_at: datetime,
) -> dict[str, str]:
    return {
        "market": up_token_id,
        "startTs": str(int(market_start_at.timestamp())),
        "endTs": str(int(scheduled_at.timestamp())),
        "fidelity": "1",
    }


def _selected_market_probability(
    points,
    *,
    scheduled_at: datetime,
) -> tuple[float | None, datetime | None]:
    eligible = []
    for point in points:
        observed_at = _utc(point.observed_at, "price observed_at")
        if observed_at <= scheduled_at:
            eligible.append((observed_at, point.price))
    if not eligible:
        return None, None
    observed_at, raw_price = max(eligible, key=lambda item: item[0])
    price = float(raw_price)
    if not math.isfinite(price) or not 0.0 <= price <= 1.0:
        raise LiveInputIntegrityError("selected market probability must be within [0, 1]")
    return price, observed_at


def _book_input(state: StateObservation | None) -> LiveBookInput | None:
    if state is None:
        return None
    return LiveBookInput(
        asset_id=str(state.asset_id),
        state_key=state.state_key,
        bucket_at=state.bucket_at,
        last_event_at=state.last_event_at,
        fresh=state.fresh,
        age_seconds=state.age_seconds,
        state=dict(state.state),
    )


def _merge_book_predictors(
    probability: float | None,
    up_group: FeatureGroup,
    down_group: FeatureGroup,
) -> dict[str, Any]:
    predictors: dict[str, Any] = {"pm_up_price": probability}
    for group in (up_group, down_group):
        predictors.update(group.values)
        predictors.update(
            {
                f"missing__{key}": 1.0 if missing else 0.0
                for key, missing in group.missing_flags.items()
            }
        )
    return predictors


def _book_descriptor(book: LiveBookInput | None) -> dict[str, Any] | None:
    if book is None:
        return None
    return {
        "asset_id": book.asset_id,
        "state_key": book.state_key,
        "bucket_at": book.bucket_at,
        "last_event_at": book.last_event_at,
        "fresh": book.fresh,
        "age_seconds": book.age_seconds,
        "state": dict(book.state),
    }


async def observe_live_input(
    connection: Connection,
    client: PolymarketPriceHistoryClient,
    *,
    condition_id: str,
    up_token_id: str,
    down_token_id: str,
    market_start_at: datetime,
    market_end_at: datetime,
    scheduled_at: datetime,
    clock: Callable[[], datetime],
    max_lateness_seconds: int = MAX_LATENESS_SECONDS,
) -> LiveMarketInput:
    if not condition_id:
        raise LiveInputIntegrityError("condition_id is required")
    if not up_token_id or not down_token_id or up_token_id == down_token_id:
        raise LiveInputIntegrityError("distinct Up and Down token ids are required")

    start, end, scheduled = _validate_window(
        market_start_at,
        market_end_at,
        scheduled_at,
    )
    request_started_at = _clock_time(clock)
    _check_deadline(
        request_started_at,
        scheduled_at=scheduled,
        market_end_at=end,
        max_lateness_seconds=max_lateness_seconds,
    )

    history_kwargs: dict[str, Any] = {
        "start": start,
        "end": scheduled,
        "fidelity_minutes": 1,
    }
    if _history_supports_timeout(client):
        history_kwargs["timeout_seconds"] = _request_timeout_seconds(
            request_started_at,
            scheduled_at=scheduled,
            max_lateness_seconds=max_lateness_seconds,
        )
    response = await client.get_history(up_token_id, **history_kwargs)
    downloaded_at = _clock_time(clock)
    _check_deadline(
        downloaded_at,
        scheduled_at=scheduled,
        market_end_at=end,
        max_lateness_seconds=max_lateness_seconds,
    )

    expected_params = _expected_request_params(
        up_token_id=up_token_id,
        market_start_at=start,
        scheduled_at=scheduled,
    )
    request_params = dict(response.request_params)
    if request_params != expected_params:
        raise LiveInputIntegrityError("price-history response request provenance mismatch")

    probability, probability_at = _selected_market_probability(
        response.points,
        scheduled_at=scheduled,
    )

    reader = FeatureSourceReader()
    up_state = reader.latest_state(
        connection,
        source="polymarket",
        stream="market",
        instrument=condition_id,
        asset_id=up_token_id,
        feature_at=scheduled,
    )
    down_state = reader.latest_state(
        connection,
        source="polymarket",
        stream="market",
        instrument=condition_id,
        asset_id=down_token_id,
        feature_at=scheduled,
    )
    up_group = book_state("pm_up", up_state)
    down_group = book_state("pm_down", down_state)
    predictors = _merge_book_predictors(probability, up_group, down_group)
    up_book = _book_input(up_state)
    down_book = _book_input(down_state)
    response_payload = dict(response.raw_payload)
    response_sha256 = canonical_hash(response_payload)

    final_clock = _clock_time(clock)
    _check_deadline(
        final_clock,
        scheduled_at=scheduled,
        market_end_at=end,
        max_lateness_seconds=max_lateness_seconds,
    )

    fingerprint = canonical_hash(
        {
            "condition_id": condition_id,
            "up_token_id": up_token_id,
            "down_token_id": down_token_id,
            "market_start_at": start,
            "market_end_at": end,
            "scheduled_at": scheduled,
            "downloaded_at": downloaded_at,
            "price_source": LIVE_PRICE_SOURCE,
            "price_dataset": LIVE_PRICE_DATASET,
            "price_request_params": request_params,
            "price_response_sha256": response_sha256,
            "market_probability_observed": probability is not None,
            "market_probability": probability,
            "market_probability_observed_at": probability_at,
            "up_book": _book_descriptor(up_book),
            "down_book": _book_descriptor(down_book),
            "predictors": predictors,
        }
    )

    return LiveMarketInput(
        condition_id=condition_id,
        up_token_id=up_token_id,
        down_token_id=down_token_id,
        market_start_at=start,
        market_end_at=end,
        scheduled_at=scheduled,
        downloaded_at=downloaded_at,
        price_source=LIVE_PRICE_SOURCE,
        price_dataset=LIVE_PRICE_DATASET,
        price_request_params=request_params,
        price_response_sha256=response_sha256,
        price_response_payload=response_payload,
        market_probability_observed=probability is not None,
        market_probability=probability,
        market_probability_observed_at=probability_at,
        up_book=up_book,
        down_book=down_book,
        predictors=predictors,
        input_fingerprint=fingerprint,
    )
