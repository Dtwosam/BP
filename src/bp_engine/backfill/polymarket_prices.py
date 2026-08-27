from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy import Connection, select

from bp_engine.backfill.provenance import (
    BackfillArtifact,
    BackfillStats,
    ProvenanceRepository,
    artifact_key,
    canonical_json_sha256,
)
from bp_engine.storage.historical import HistoricalRepository, PolymarketPricePoint
from bp_engine.storage.schema import polymarket_markets


class PolymarketPriceHistoryError(ValueError):
    """Raised when CLOB historical price data cannot be consumed safely."""


@dataclass(frozen=True)
class PriceHistoryPoint:
    observed_at: datetime
    price: Decimal


@dataclass(frozen=True)
class PriceHistoryResponse:
    points: tuple[PriceHistoryPoint, ...]
    request_params: dict[str, str]
    raw_payload: dict[str, Any]

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        request_params: dict[str, str],
    ) -> PriceHistoryResponse:
        history = payload.get("history")
        if not isinstance(history, list):
            raise PolymarketPriceHistoryError("price-history response must contain history array")

        points: list[PriceHistoryPoint] = []
        for item in history:
            if not isinstance(item, Mapping):
                raise PolymarketPriceHistoryError("price-history entries must be JSON objects")
            timestamp = item.get("t")
            price_value = item.get("p")
            if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
                raise PolymarketPriceHistoryError("price-history timestamp must be numeric")
            try:
                price = Decimal(str(price_value))
                observed_at = datetime.fromtimestamp(timestamp, tz=UTC)
            except (InvalidOperation, ValueError, OverflowError, OSError) as exc:
                raise PolymarketPriceHistoryError(
                    "price-history entry contains invalid timestamp or price"
                ) from exc
            if not price.is_finite() or price < 0 or price > 1:
                raise PolymarketPriceHistoryError("price-history price must be between 0 and 1")
            points.append(PriceHistoryPoint(observed_at=observed_at, price=price))

        points.sort(key=lambda point: point.observed_at)
        return cls(
            points=tuple(points),
            request_params=dict(request_params),
            raw_payload=dict(payload),
        )


class PolymarketPriceHistoryClient:
    BASE_URL = "https://clob.polymarket.com"

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http_client = http_client

    async def get_history(
        self,
        asset_id: str,
        *,
        start: datetime,
        end: datetime,
        fidelity_minutes: int,
        timeout_seconds: float | None = None,
    ) -> PriceHistoryResponse:
        if not asset_id:
            raise ValueError("asset_id is required")
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError("start must be timezone-aware")
        if end.tzinfo is None or end.utcoffset() is None:
            raise ValueError("end must be timezone-aware")
        if start >= end:
            raise ValueError("start must be before end")
        if fidelity_minutes <= 0:
            raise ValueError("fidelity_minutes must be positive")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive when provided")

        params = {
            "market": asset_id,
            "startTs": str(int(start.timestamp())),
            "endTs": str(int(end.timestamp())),
            "fidelity": str(fidelity_minutes),
        }
        request_kwargs: dict[str, Any] = {"params": params}
        if timeout_seconds is not None:
            request_kwargs["timeout"] = timeout_seconds

        if self._http_client is not None:
            response = await self._http_client.get("/prices-history", **request_kwargs)
        else:
            async with httpx.AsyncClient(base_url=self.BASE_URL, timeout=20.0) as client:
                response = await client.get("/prices-history", **request_kwargs)

        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise PolymarketPriceHistoryError("price-history response must be a JSON object")
        return PriceHistoryResponse.from_payload(payload, request_params=params)


def _database_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def backfill_polymarket_prices(
    connection: Connection,
    client: PolymarketPriceHistoryClient,
    *,
    run_id: str,
    start: datetime,
    end: datetime,
    downloaded_at: datetime,
    fidelity_minutes: int = 1,
    historical_repository: HistoricalRepository | None = None,
    provenance_repository: ProvenanceRepository | None = None,
) -> BackfillStats:
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("start must be timezone-aware")
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("end must be timezone-aware")
    if downloaded_at.tzinfo is None or downloaded_at.utcoffset() is None:
        raise ValueError("downloaded_at must be timezone-aware")
    if start >= end:
        raise ValueError("start must be before end")
    if fidelity_minutes <= 0:
        raise ValueError("fidelity_minutes must be positive")

    historical_repository = historical_repository or HistoricalRepository()
    provenance_repository = provenance_repository or ProvenanceRepository()

    market_rows = connection.execute(
        select(polymarket_markets).where(
            polymarket_markets.c.start_at >= start,
            polymarket_markets.c.start_at < end,
        )
    ).mappings()

    inserted = 0
    existing = 0
    chunks = 0

    for market in market_rows:
        market_start = _database_time(market["start_at"])
        market_end = _database_time(market["end_at"])
        for outcome, asset_id in (
            ("Up", market["up_token_id"]),
            ("Down", market["down_token_id"]),
        ):
            response = await client.get_history(
                asset_id,
                start=market_start,
                end=market_end,
                fidelity_minutes=fidelity_minutes,
            )
            chunks += 1

            provenance_repository.record_artifact(
                connection,
                BackfillArtifact(
                    run_id=run_id,
                    artifact_key=artifact_key(
                        "polymarket_clob",
                        "prices_history",
                        response.request_params,
                    ),
                    source="polymarket_clob",
                    dataset="prices_history",
                    request_params=response.request_params,
                    downloaded_at=downloaded_at,
                    response_sha256=canonical_json_sha256(response.raw_payload),
                    row_count=len(response.points),
                ),
            )

            for point in response.points:
                if point.observed_at < market_start or point.observed_at >= market_end:
                    continue
                result = historical_repository.store_polymarket_price(
                    connection,
                    PolymarketPricePoint(
                        condition_id=market["condition_id"],
                        asset_id=asset_id,
                        outcome=outcome,
                        observed_at=point.observed_at,
                        price=point.price,
                        fidelity_minutes=fidelity_minutes,
                    ),
                )
                if result.created:
                    inserted += 1
                else:
                    existing += 1

    return BackfillStats(
        rows_inserted=inserted,
        rows_existing=existing,
        chunks_fetched=chunks,
    )
