from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import Engine, exists, select

from bp_engine.backfill.provenance import canonical_json_sha256
from bp_engine.labels.service import generate_labels
from bp_engine.live_prediction.evaluation import append_available_evaluations
from bp_engine.polymarket.models import PolymarketMarket
from bp_engine.polymarket.parsing import parse_gamma_market
from bp_engine.storage.historical import (
    HistoricalRepository,
    PolymarketMarketSnapshot,
)
from bp_engine.storage.schema import live_prediction_evaluations, live_predictions


class GammaMarketClient(Protocol):
    async def get_market_by_slug(self, slug: str) -> Mapping[str, Any] | None: ...


class ProspectiveOutcomeIntegrityError(ValueError):
    """Raised when official outcome evidence does not match the prediction identity."""


@dataclass(frozen=True)
class ProspectiveOutcomeSyncReport:
    candidates: int
    pending_markets: int
    resolved_markets: int
    created_snapshots: int
    existing_snapshots: int
    created_labels: int
    existing_labels: int
    created_evaluations: int
    existing_evaluations: int


@dataclass(frozen=True)
class _Candidate:
    prediction_id: str
    condition_id: str
    slug: str
    horizon_seconds: int
    market_start_at: datetime
    market_end_at: datetime
    up_token_id: str
    down_token_id: str


class ProspectiveOutcomeSyncService:
    """Append official post-resolution evidence for ended prospective predictions."""

    def __init__(self, *, engine: Engine, client: GammaMarketClient) -> None:
        self.engine = engine
        self.client = client
        self.historical_repository = HistoricalRepository()

    async def run_once(self, *, now: datetime) -> ProspectiveOutcomeSyncReport:
        observed_at = _aware_utc(now, "now")

        # First consume any canonical labels that already exist. This keeps the network
        # path focused only on genuinely missing outcome evidence.
        with self.engine.begin() as connection:
            initial_evaluations = append_available_evaluations(
                connection,
                evaluated_at=observed_at,
            )
            candidates = self._load_candidates(connection, now=observed_at)

        pending_markets = 0
        resolved_markets = 0
        created_snapshots = 0
        existing_snapshots = 0
        created_labels = 0
        existing_labels = 0

        for candidate in candidates:
            payload = await self.client.get_market_by_slug(candidate.slug)
            if payload is None:
                pending_markets += 1
                continue

            market = parse_gamma_market(payload)
            self._validate_identity(candidate, market)
            if not market.closed or market.resolved_outcome is None:
                pending_markets += 1
                continue

            payload_dict = dict(payload)
            snapshot = PolymarketMarketSnapshot(
                condition_id=market.condition_id,
                gamma_market_id=market.gamma_market_id,
                slug=market.slug,
                downloaded_at=observed_at,
                payload_sha256=canonical_json_sha256(payload_dict),
                payload=payload_dict,
            )

            with self.engine.begin() as connection:
                snapshot_result = self.historical_repository.store_polymarket_market_snapshot(
                    connection,
                    snapshot,
                )
                label_stats = generate_labels(
                    connection,
                    start=market.window_start_at,
                    end=market.window_start_at + timedelta(microseconds=1),
                    generated_at=observed_at,
                )

            resolved_markets += 1
            created_snapshots += int(snapshot_result.created)
            existing_snapshots += int(not snapshot_result.created)
            created_labels += label_stats.inserted
            existing_labels += label_stats.existing

        with self.engine.begin() as connection:
            final_evaluations = append_available_evaluations(
                connection,
                evaluated_at=observed_at,
            )

        return ProspectiveOutcomeSyncReport(
            candidates=len(candidates),
            pending_markets=pending_markets,
            resolved_markets=resolved_markets,
            created_snapshots=created_snapshots,
            existing_snapshots=existing_snapshots,
            created_labels=created_labels,
            existing_labels=existing_labels,
            created_evaluations=(
                initial_evaluations.created + final_evaluations.created
            ),
            existing_evaluations=(
                initial_evaluations.existing + final_evaluations.existing
            ),
        )

    @staticmethod
    def _load_candidates(connection: Any, *, now: datetime) -> list[_Candidate]:
        evaluation_exists = exists(
            select(live_prediction_evaluations.c.prediction_id).where(
                live_prediction_evaluations.c.prediction_id
                == live_predictions.c.prediction_id
            )
        )
        rows = connection.execute(
            select(
                live_predictions.c.prediction_id,
                live_predictions.c.condition_id,
                live_predictions.c.slug,
                live_predictions.c.horizon_seconds,
                live_predictions.c.market_start_at,
                live_predictions.c.market_end_at,
                live_predictions.c.up_token_id,
                live_predictions.c.down_token_id,
            )
            .where(
                live_predictions.c.market_end_at <= now,
                ~evaluation_exists,
            )
            .order_by(
                live_predictions.c.market_end_at,
                live_predictions.c.condition_id,
                live_predictions.c.prediction_id,
            )
        ).mappings()
        return [
            _Candidate(
                prediction_id=str(row["prediction_id"]),
                condition_id=str(row["condition_id"]),
                slug=str(row["slug"]),
                horizon_seconds=int(row["horizon_seconds"]),
                market_start_at=_stored_utc(row["market_start_at"]),
                market_end_at=_stored_utc(row["market_end_at"]),
                up_token_id=str(row["up_token_id"]),
                down_token_id=str(row["down_token_id"]),
            )
            for row in rows
        ]

    @staticmethod
    def _validate_identity(candidate: _Candidate, market: PolymarketMarket) -> None:
        expected = (
            candidate.condition_id,
            candidate.slug,
            candidate.horizon_seconds,
            candidate.market_start_at,
            candidate.market_end_at,
            candidate.up_token_id,
            candidate.down_token_id,
        )
        actual = (
            market.condition_id,
            market.slug,
            market.horizon_seconds,
            market.window_start_at,
            market.window_end_at,
            market.up_token_id,
            market.down_token_id,
        )
        if actual != expected:
            raise ProspectiveOutcomeIntegrityError(
                "official Gamma market identity does not match stored prediction "
                f"prediction_id={candidate.prediction_id}"
            )


def _aware_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
