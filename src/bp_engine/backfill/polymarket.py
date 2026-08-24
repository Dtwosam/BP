from __future__ import annotations

from datetime import datetime

from sqlalchemy import Connection

from bp_engine.backfill.provenance import (
    BackfillArtifact,
    BackfillStats,
    ProvenanceRepository,
    artifact_key,
    canonical_json_sha256,
)
from bp_engine.polymarket.gamma import GammaClient
from bp_engine.polymarket.parsing import GammaMarketError, parse_gamma_market, parse_horizon_slug
from bp_engine.storage.historical import HistoricalRepository, PolymarketMarketSnapshot
from bp_engine.storage.polymarket_markets import PolymarketMarketRepository


def _allowed_horizon_seconds(horizons: tuple[str, ...]) -> frozenset[int]:
    values: set[int] = set()
    for horizon in horizons:
        if not horizon.endswith("m"):
            raise ValueError(f"unsupported horizon format: {horizon}")
        try:
            minutes = int(horizon[:-1])
        except ValueError as exc:
            raise ValueError(f"unsupported horizon format: {horizon}") from exc
        if minutes <= 0:
            raise ValueError(f"unsupported horizon format: {horizon}")
        values.add(minutes * 60)
    if not values:
        raise ValueError("at least one horizon is required")
    return frozenset(values)


async def backfill_polymarket_markets(
    connection: Connection,
    client: GammaClient,
    *,
    run_id: str,
    start: datetime,
    end: datetime,
    horizons: tuple[str, ...],
    downloaded_at: datetime,
    initial_cursor: str | None = None,
    market_repository: PolymarketMarketRepository | None = None,
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

    allowed = _allowed_horizon_seconds(horizons)
    market_repository = market_repository or PolymarketMarketRepository()
    historical_repository = historical_repository or HistoricalRepository()
    provenance_repository = provenance_repository or ProvenanceRepository()

    inserted = 0
    existing = 0
    chunks = 0
    cursor = initial_cursor
    seen_cursors: set[str] = set()

    while True:
        if cursor is not None:
            if cursor in seen_cursors:
                raise RuntimeError(f"Gamma keyset cursor repeated: {cursor}")
            seen_cursors.add(cursor)

        page = await client.list_markets_page(
            start=start,
            end=end,
            limit=100,
            after_cursor=cursor,
        )
        chunks += 1

        page_artifact_key = artifact_key(
            "polymarket_gamma",
            "markets_keyset",
            page.request_params,
        )
        provenance_repository.record_artifact(
            connection,
            BackfillArtifact(
                run_id=run_id,
                artifact_key=page_artifact_key,
                source="polymarket_gamma",
                dataset="markets_keyset",
                request_params=page.request_params,
                downloaded_at=downloaded_at,
                response_sha256=canonical_json_sha256(page.raw_payload),
                row_count=len(page.markets),
            ),
        )

        for payload in page.markets:
            slug = payload.get("slug")
            if not isinstance(slug, str):
                continue
            try:
                horizon_seconds, _ = parse_horizon_slug(slug)
            except GammaMarketError:
                continue
            if horizon_seconds not in allowed:
                continue

            market = parse_gamma_market(payload)
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

        next_cursor = page.next_cursor
        if next_cursor is None:
            break
        if next_cursor == cursor:
            raise RuntimeError(f"Gamma keyset cursor did not advance: {next_cursor}")
        cursor = next_cursor

    return BackfillStats(
        rows_inserted=inserted,
        rows_existing=existing,
        chunks_fetched=chunks,
    )
