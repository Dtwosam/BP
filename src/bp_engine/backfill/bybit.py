from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy import Connection

from bp_engine.backfill.provenance import (
    BackfillArtifact,
    BackfillStats,
    ProvenanceRepository,
    artifact_key,
    canonical_json_sha256,
)
from bp_engine.storage.historical import BtcCandle, HistoricalRepository


class BybitHistoryError(ValueError):
    """Raised when Bybit historical data cannot be normalized safely."""


@dataclass(frozen=True)
class BybitKline:
    bucket_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    turnover: Decimal | None
    raw_payload: list[Any]


@dataclass(frozen=True)
class BybitKlineResponse:
    candles: tuple[BybitKline, ...]
    request_params: dict[str, str]
    raw_payload: dict[str, Any]

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        request_params: dict[str, str],
    ) -> BybitKlineResponse:
        if payload.get("retCode") != 0:
            raise BybitHistoryError(
                f"Bybit kline request failed: retCode={payload.get('retCode')} "
                f"retMsg={payload.get('retMsg')}"
            )
        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise BybitHistoryError("Bybit kline response must contain result object")
        rows = result.get("list")
        if not isinstance(rows, list):
            raise BybitHistoryError("Bybit kline result must contain list array")

        candles: list[BybitKline] = []
        for raw_row in rows:
            if not isinstance(raw_row, list) or len(raw_row) < 7:
                raise BybitHistoryError("Bybit kline rows must contain at least seven values")
            try:
                bucket_ms = int(raw_row[0])
                open_price = Decimal(str(raw_row[1]))
                high = Decimal(str(raw_row[2]))
                low = Decimal(str(raw_row[3]))
                close = Decimal(str(raw_row[4]))
                volume = Decimal(str(raw_row[5]))
                turnover = Decimal(str(raw_row[6])) if raw_row[6] is not None else None
            except (TypeError, ValueError, InvalidOperation) as exc:
                raise BybitHistoryError("Bybit kline row contains invalid numeric data") from exc

            numeric = (open_price, high, low, close, volume)
            if not all(value.is_finite() for value in numeric):
                raise BybitHistoryError("Bybit kline row contains non-finite numeric data")
            if turnover is not None and not turnover.is_finite():
                raise BybitHistoryError("Bybit kline turnover must be finite")

            candles.append(
                BybitKline(
                    bucket_at=datetime.fromtimestamp(bucket_ms / 1000, tz=UTC),
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    turnover=turnover,
                    raw_payload=list(raw_row),
                )
            )

        candles.sort(key=lambda candle: candle.bucket_at)
        return cls(
            candles=tuple(candles),
            request_params=dict(request_params),
            raw_payload=dict(payload),
        )


class BybitHistoryClient:
    BASE_URL = "https://api.bybit.com"

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http_client = http_client

    async def get_klines(
        self,
        *,
        category: str,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
        limit: int = 1000,
    ) -> BybitKlineResponse:
        if category not in {"spot", "linear"}:
            raise ValueError("category must be spot or linear")
        if not symbol:
            raise ValueError("symbol is required")
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError("start must be timezone-aware")
        if end.tzinfo is None or end.utcoffset() is None:
            raise ValueError("end must be timezone-aware")
        if start >= end:
            raise ValueError("start must be before end")
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")

        params = {
            "category": category,
            "symbol": symbol,
            "interval": interval,
            "start": str(int(start.timestamp() * 1000)),
            "end": str(int(end.timestamp() * 1000) - 1),
            "limit": str(limit),
        }
        if self._http_client is not None:
            response = await self._http_client.get("/v5/market/kline", params=params)
        else:
            async with httpx.AsyncClient(base_url=self.BASE_URL, timeout=20.0) as client:
                response = await client.get("/v5/market/kline", params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise BybitHistoryError("Bybit kline response must be a JSON object")
        return BybitKlineResponse.from_payload(payload, request_params=params)


def iter_candle_windows(
    start: datetime,
    end: datetime,
    *,
    interval_seconds: int,
    max_candles: int,
) -> Iterator[tuple[datetime, datetime]]:
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("start must be timezone-aware")
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("end must be timezone-aware")
    if start >= end:
        raise ValueError("start must be before end")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    if max_candles <= 0:
        raise ValueError("max_candles must be positive")

    width = timedelta(seconds=interval_seconds * max_candles)
    cursor = start
    while cursor < end:
        window_end = min(cursor + width, end)
        yield cursor, window_end
        cursor = window_end


def _bybit_interval(interval_seconds: int) -> str:
    values = {
        60: "1",
        180: "3",
        300: "5",
        900: "15",
        1800: "30",
        3600: "60",
        7200: "120",
        14400: "240",
        21600: "360",
        43200: "720",
        86400: "D",
        604800: "W",
    }
    try:
        return values[interval_seconds]
    except KeyError as exc:
        raise ValueError(f"unsupported Bybit interval_seconds: {interval_seconds}") from exc


async def backfill_bybit_candles(
    connection: Connection,
    client: BybitHistoryClient,
    *,
    run_id: str,
    category: str,
    symbol: str,
    start: datetime,
    end: datetime,
    downloaded_at: datetime,
    interval_seconds: int = 60,
    max_candles_per_request: int = 1000,
    historical_repository: HistoricalRepository | None = None,
    provenance_repository: ProvenanceRepository | None = None,
) -> BackfillStats:
    if downloaded_at.tzinfo is None or downloaded_at.utcoffset() is None:
        raise ValueError("downloaded_at must be timezone-aware")
    if max_candles_per_request < 1 or max_candles_per_request > 1000:
        raise ValueError("max_candles_per_request must be between 1 and 1000")

    interval = _bybit_interval(interval_seconds)
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
        response = await client.get_klines(
            category=category,
            symbol=symbol,
            interval=interval,
            start=window_start,
            end=window_end,
            limit=max_candles_per_request,
        )
        chunks += 1
        provenance_repository.record_artifact(
            connection,
            BackfillArtifact(
                run_id=run_id,
                artifact_key=artifact_key("bybit", f"kline_{category}", response.request_params),
                source="bybit",
                dataset=f"kline_{category}",
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
                    source="bybit",
                    market_type=category,
                    symbol=symbol,
                    interval_seconds=interval_seconds,
                    bucket_at=candle.bucket_at,
                    open=candle.open,
                    high=candle.high,
                    low=candle.low,
                    close=candle.close,
                    volume=candle.volume,
                    turnover=candle.turnover,
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
