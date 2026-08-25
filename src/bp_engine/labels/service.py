from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, select

from bp_engine.labels.models import MarketLabel
from bp_engine.labels.repository import MarketLabelRepository
from bp_engine.polymarket.models import PolymarketMarket
from bp_engine.polymarket.parsing import parse_gamma_market
from bp_engine.storage.schema import polymarket_market_snapshots

LABEL_SOURCE = "polymarket_gamma_snapshot"
LABEL_VERSION = "official-outcome-v1"


class LabelLeakageError(RuntimeError):
    """Raised when resolution evidence predates the market end."""


class LabelSourceConflict(RuntimeError):
    """Raised when stored official-resolution evidence contradicts itself."""


@dataclass(frozen=True)
class LabelGenerationStats:
    inserted: int
    existing: int
    skipped: int
    conditions_considered: int


@dataclass(frozen=True)
class _ResolvedSnapshot:
    row_id: int
    downloaded_at: datetime
    payload_sha256: str
    market: PolymarketMarket


def generate_labels(
    connection: Connection,
    *,
    start: datetime,
    end: datetime,
    generated_at: datetime,
    repository: MarketLabelRepository | None = None,
) -> LabelGenerationStats:
    start = _require_aware(start, "start")
    end = _require_aware(end, "end")
    generated_at = _require_aware(generated_at, "generated_at")
    if start >= end:
        raise ValueError("start must be before end")

    rows = connection.execute(
        select(polymarket_market_snapshots).order_by(
            polymarket_market_snapshots.c.condition_id,
            polymarket_market_snapshots.c.downloaded_at,
            polymarket_market_snapshots.c.id,
        )
    ).mappings()

    considered: set[str] = set()
    eligible: dict[str, list[_ResolvedSnapshot]] = {}

    for row in rows:
        market = parse_gamma_market(row["payload"])
        if not (start <= market.window_start_at < end):
            continue

        considered.add(market.condition_id)
        if (
            row["condition_id"] != market.condition_id
            or row["slug"] != market.slug
            or row["gamma_market_id"] != market.gamma_market_id
        ):
            raise LabelSourceConflict(
                "snapshot envelope does not match parsed market for "
                f"condition={market.condition_id}"
            )

        observed_at = _db_datetime(row["downloaded_at"])
        if market.resolved_outcome is not None and observed_at < market.window_end_at:
            raise LabelLeakageError(
                "resolved snapshot observed before market end for "
                f"condition={market.condition_id}"
            )
        if not market.closed or market.resolved_outcome is None:
            continue

        eligible.setdefault(market.condition_id, []).append(
            _ResolvedSnapshot(
                row_id=int(row["id"]),
                downloaded_at=observed_at,
                payload_sha256=str(row["payload_sha256"]),
                market=market,
            )
        )

    repository = repository or MarketLabelRepository()
    inserted = 0
    existing = 0
    skipped = 0

    for condition_id in sorted(considered):
        candidates = eligible.get(condition_id, [])
        if not candidates:
            skipped += 1
            continue

        canonical = min(candidates, key=lambda item: (item.downloaded_at, item.row_id))
        expected = _resolution_semantics(canonical.market)
        for candidate in candidates:
            if _resolution_semantics(candidate.market) != expected:
                raise LabelSourceConflict(
                    f"conflicting resolved snapshots for condition={condition_id}"
                )

        label = MarketLabel(
            condition_id=canonical.market.condition_id,
            gamma_market_id=canonical.market.gamma_market_id,
            slug=canonical.market.slug,
            horizon_seconds=canonical.market.horizon_seconds,
            market_start_at=canonical.market.window_start_at,
            market_end_at=canonical.market.window_end_at,
            official_outcome=canonical.market.resolved_outcome,
            start_reference=None,
            end_reference=None,
            resolution_source=canonical.market.resolution_source,
            rules_hash=canonical.market.rules_hash,
            label_source=LABEL_SOURCE,
            label_version=LABEL_VERSION,
            source_snapshot_sha256=canonical.payload_sha256,
            source_observed_at=canonical.downloaded_at,
            generated_at=generated_at,
        )
        result = repository.store(connection, label)
        if result.created:
            inserted += 1
        else:
            existing += 1

    return LabelGenerationStats(
        inserted=inserted,
        existing=existing,
        skipped=skipped,
        conditions_considered=len(considered),
    )


def _resolution_semantics(market: PolymarketMarket) -> tuple[Any, ...]:
    return (
        market.gamma_market_id,
        market.slug,
        market.horizon_seconds,
        market.window_start_at,
        market.window_end_at,
        market.resolved_outcome,
        market.resolution_source,
        market.rules_hash,
    )


def _require_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _db_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
