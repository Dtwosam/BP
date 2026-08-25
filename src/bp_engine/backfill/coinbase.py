from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import Connection

from bp_engine.backfill.bybit import iter_candle_windows
from bp_engine.backfill.provenance import (
    BackfillArtifact,
    BackfillStats,
    ProvenanceRepository,
    artifact_key,
    canonical_json_sha256,
)
from bp_engine.storage.historical import BtcCandle, HistoricalRepository


class CoinbaseHistoryError(ValueError):
    """Raised when Coinbase historical candle data cannot be normalized safely."""


@dataclass(frozen=True)
class CoinbaseCandle:
    bucket_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class CoinbaseCandleResponse:
    candles: tuple[CoinbaseCandle, ...]
    request_params: dict[str, str]
    raw_payload: dict[str, Any]

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        request_params: dict[str, str],
    ) -> CoinbaseCandleResponse:
        rows = payload.get("candles")
        if not isinstance(rows, list):
            raise CoinbaseHistoryError("Coinbase candle response must contain candles array")

        candles: list[CoinbaseCandle] = []
        for raw_row in rows:
            if not isinstance(raw_row, Mapping):
                raise CoinbaseHistoryError("Coinbase candle rows must be JSON objects")
            try:
                bucket_at = datetime.fromtimestamp(int(raw_row["start"]), tz=UTC)
                low = Decimal(str(raw_row["low"]))
                high = Decimal(str(raw_row["high"]))
                open_price = Decimal(str(raw_row["open"]))
                close = Decimal(str(raw_row["close"]))
                volume = Decimal(str(raw_row["volume"]))
            except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
                raise CoinbaseHistoryError(
                    "Coinbase candle row contains invalid or missing numeric data"
                ) from exc

            numeric = (open_price, high, low, close, volume)
            if not all(value.is_finite() for value in numeric):
                raise CoinbaseHistoryError("Coinbase candle row contains non-finite numeric data")

            candles.append(
                CoinbaseCandle(
                    bucket_at=bucket_at,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    raw_payload=dict(raw_row),
                )
            )

        candles.sort(key=lambda candle: candle.bucket_at)
        return cls(
            candles=tuple(candles),
            request_params=dict(request_params),
            raw_payload=dict(payload),
        )


class CoinbaseHistoryClient:
    BASE_URL = "https://api.coinbase.com"

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http_client = http_client

    async def get_candles(
        self,
        *,
        product_id: str,
        granularity: str,
        start: datetime,
        end: datetime,
        limit: int = 350,
    ) -> CoinbaseCandleResponse:
        if not product_id:
            raise ValueError("product_id is required")
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError("start must be timezone-aware")
        if end.tzinfo is None or end.utcoffset() is None:
            raise ValueError("end must be timezone-aware")
        if start >= end:
            raise ValueError("start must be before end")
        if limit < 1 or limit > 350:
            raise ValueError("limit must be between 1 and 350")

        params = {
            "start": str(int(start.timestamp())),
            "end": str(int(end.timestamp())),
            "granularity": granularity,
            "limit": str(limit),
        }
        path = (
            "/api/v3/brokerage/market/products/"
            f"{quote(product_id, safe='')}/candles"
        )
        if self._http_client is not None:
            response = await self._http_client.get(path, params=params)
        else:
            async with httpx.AsyncClient(base_url=self.BASE_URL, timeout=20.0) as client:
                response = await client.get(path, params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise CoinbaseHistoryError("Coinbase candle response must be a JSON object")

        provenance_params = {"product_id": product_id, **params}
        return CoinbaseCandleResponse.from_payload(
            payload,
            request_params=provenance_params,
        )


def _coinbase_granularity(interval_seconds: int) -> str:
    values = {
        60: "ONE_MINUTE",
        300: "FIVE_MINUTE",
        900: "FIFTEEN_MINUTE",
        1800: "THIRTY_MINUTE",
        3600: "ONE_HOUR",
        7200: "TWO_HOUR",
        14400: "FOUR_HOUR",
        21600: "SIX_HOUR",
        86400: "ONE_DAY",
    }
    try:
        return values[interval_seconds]
    except KeyError as exc:
        raise ValueError(f"unsupported Coinbase interval_seconds: {interval_seconds}") from exc


async def backfill_coinbase_candles(
    connection: Connection,
    client: CoinbaseHistoryClient,
    *,
    run_id: str,
    product_id: str,
    start: datetime,
    end: datetime,
    downloaded_at: datetime,
    interval_seconds: int = 60,
    max_candles_per_request: int = 350,
    historical_repository: HistoricalRepository | None = None,
    provenance_repository: ProvenanceRepository | None = None,
) -> BackfillStats:
    if downloaded_at.tzinfo is None or downloaded_at.utcoffset() is None:
        raise ValueError("downloaded_at must be timezone-aware")
    if max_candles_per_request < 1 or max_candles_per_request > 350:
        raise ValueError("max_candles_per_request must be between 1 and 350")

    granularity = _coinbase_granularity(interval_seconds)
    historical_repository = historical_repository or HistoricalRepository()
    provenance_repository = provenance_repository or ProvenanceRepository()

    inserted = 0
    existing = 0
    chunks = 0

    for window_start, window_end in iter_candle_windows(
        start,
        end,
        interval_seconds=interval_seconds,
        max_candles=max_candles_per_request,
    ):
        response = await client.get_candles(
            product_id=product_id,
            granularity=granularity,
            start=window_start,
            end=window_end,
            limit=max_candles_per_request,
        )
        chunks += 1
        provenance_repository.record_artifact(
            connection,
            BackfillArtifact(
                run_id=run_id,
                artifact_key=artifact_key(
                    "coinbase",
                    "public_product_candles",
                    response.request_params,
                ),
                source="coinbase",
                dataset="public_product_candles",
                request_params=response.request_params,
                downloaded_at=downloaded_at,
                response_sha256=canonical_json_sha256(response.raw_payload),
                row_count=len(response.candles),
            ),
        )

        for candle in response.candles:
            if candle.bucket_at < window_start or candle.bucket_at >= window_end:
                continue
            result = historical_repository.store_btc_candle(
                connection,
                BtcCandle(
                    source="coinbase",
                    market_type="spot",
                    symbol=product_id,
                    interval_seconds=interval_seconds,
                    bucket_at=candle.bucket_at,
                    open=candle.open,
                    high=candle.high,
                    low=candle.low,
                    close=candle.close,
                    volume=candle.volume,
                    turnover=None,
                    raw_payload=candle.raw_payload,
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
