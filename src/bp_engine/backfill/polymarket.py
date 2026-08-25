from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import Connection

from bp_engine.backfill.provenance import (
    BackfillArtifact,
    BackfillStats,
    ProvenanceRepository,
    artifact_key,
    canonical_json_sha256,
)
from bp_engine.polymarket.gamma import GammaClient
from bp_engine.polymarket.parsing import parse_gamma_market
from bp_engine.storage.historical import HistoricalRepository, PolymarketMarketSnapshot
from bp_engine.storage.polymarket_markets import PolymarketMarketRepository


def _horizon_specs(horizons: tuple[str, ...]) -> tuple[tuple[str, int], ...]:
    specs: list[tuple[str, int]] = []
    seen: set[str] = set()
    for horizon in horizons:
        if horizon in seen:
            continue
        if not horizon.endswith("m"):
            raise ValueError(f"unsupported horizon format: {horizon}")
        try:
            minutes = int(horizon[:-1])
        except ValueError as exc:
            raise ValueError(f"unsupported horizon format: {horizon}") from exc
        if minutes <= 0:
            raise ValueError(f"unsupported horizon format: {horizon}")
        seen.add(horizon)
        specs.append((horizon, minutes * 60))
    if not specs:
        raise ValueError("at least one horizon is required")
    return tuple(specs)


def _first_aligned_epoch(start: datetime, interval_seconds: int) -> int:
    epoch = int(start.timestamp())
    if datetime.fromtimestamp(epoch, tz=UTC) < start.astimezone(UTC):
        epoch += 1
    return ((epoch + interval_seconds - 1) // interval_seconds) * interval_seconds


def iter_expected_btc_market_slugs(
    start: datetime,
    end: datetime,
    horizons: tuple[str, ...],
) -> Iterator[tuple[str, int, datetime]]:
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("start must be timezone-aware")
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("end must be timezone-aware")
    if start >= end:
        raise ValueError("start must be before end")

    end_utc = end.astimezone(UTC)
    for horizon, interval_seconds in _horizon_specs(horizons):
        epoch = _first_aligned_epoch(start, interval_seconds)
        while True:
            window_start = datetime.fromtimestamp(epoch, tz=UTC)
            if window_start >= end_utc:
                break
            yield f"btc-updown-{horizon}-{epoch}", interval_seconds, window_start
            epoch += interval_seconds


async def backfill_polymarket_markets(
    connection: Connection,
    client: GammaClient,
    *,
    run_id: str,
    start: datetime,
    end: datetime,
    horizons: tuple[str, ...],
    downloaded_at: datetime,
    market_repository: PolymarketMarketRepository | None = None,
    historical_repository: HistoricalRepository | None = None,
    provenance_repository: ProvenanceRepository | None = None,
) -> BackfillStats:
    if downloaded_at.tzinfo is None or downloaded_at.utcoffset() is None:
        raise ValueError("downloaded_at must be timezone-aware")

    market_repository = market_repository or PolymarketMarketRepository()
    historical_repository = historical_repository or HistoricalRepository()
    provenance_repository = provenance_repository or ProvenanceRepository()

    inserted = 0
    existing = 0
    chunks = 0

    for slug, horizon_seconds, expected_start in iter_expected_btc_market_slugs(
        start,
        end,
        horizons,
    ):
        payload = await client.get_market_by_slug(slug)
        chunks += 1
        if payload is None:
            continue

        returned_slug = payload.get("slug")
        if returned_slug != slug:
            raise RuntimeError(
                f"Gamma market slug mismatch: requested={slug} returned={returned_slug}"
            )

        market = parse_gamma_market(payload)
        if market.horizon_seconds != horizon_seconds or market.window_start_at != expected_start:
            raise RuntimeError(f"Gamma market window mismatch for slug: {slug}")
        if not market.closed:
            continue

        request_params = {"slug": slug}
        provenance_repository.record_artifact(
            connection,
            BackfillArtifact(
                run_id=run_id,
                artifact_key=artifact_key(
                    "polymarket_gamma",
                    "market_by_slug",
                    request_params,
                ),
                source="polymarket_gamma",
                dataset="market_by_slug",
                request_params=request_params,
                downloaded_at=downloaded_at,
                response_sha256=canonical_json_sha256(payload),
                row_count=1,
            ),
        )

        result = market_repository.upsert(connection, market, downloaded_at)
        if result.created:
            inserted += 1
        else:
            existing += 1

        historical_repository.store_polymarket_market_snapshot(
            connection,
            PolymarketMarketSnapshot(
                condition_id=market.condition_id,
                gamma_market_id=market.gamma_market_id,
                slug=market.slug,
                downloaded_at=downloaded_at,
                payload_sha256=canonical_json_sha256(payload),
                payload=payload,
            ),
        )

    return BackfillStats(
        rows_inserted=inserted,
        rows_existing=existing,
        chunks_fetched=chunks,
    )
