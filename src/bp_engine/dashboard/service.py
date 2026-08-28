from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from bp_engine.config import Settings
from bp_engine.dashboard.metrics import build_performance
from bp_engine.dashboard.models import HealthResponse, OverviewResponse, PerformanceResponse
from bp_engine.dashboard.queries import VERIFIED_HORIZONS, DashboardQueries

_HEALTHY_FEED_STATUSES = frozenset({"healthy", "ok", "active", "connected"})


class DashboardService:
    def __init__(self, queries: DashboardQueries, settings: Settings) -> None:
        self._queries = queries
        self._settings = settings

    def health(self, *, now: datetime | None = None) -> HealthResponse:
        generated_at = now or datetime.now(UTC)
        database = self._queries.health()
        return HealthResponse(
            database_status=database["database"],
            generated_at=generated_at,
        )

    def overview(self, *, now: datetime | None = None) -> OverviewResponse:
        generated_at = now or datetime.now(UTC)
        database = self._queries.health()
        markets = self._queries.active_markets(
            now=generated_at,
            horizon_seconds=None,
            limit=100,
        )
        predictions = self._queries.predictions(
            horizon_seconds=None,
            evaluation_state=None,
            trade=None,
            limit=100,
            before_recorded_at=None,
        )
        feeds = self._queries.feed_health(
            now=generated_at,
            incident_window=timedelta(hours=1),
        )

        evaluated_count = sum(item.get("evaluation") is not None for item in predictions)
        active_5m = sum(item.get("horizon_seconds") == 300 for item in markets)
        active_15m = sum(item.get("horizon_seconds") == 900 for item in markets)
        unhealthy_feeds = sum(
            str(item.get("status", "")).lower() not in _HEALTHY_FEED_STATUSES
            for item in feeds
        )

        return OverviewResponse(
            generated_at=generated_at,
            mode=self._settings.mode.value,
            live_trading_enabled=self._settings.live_trading_enabled,
            verified_horizons_seconds=VERIFIED_HORIZONS,
            database_status=database["database"],
            active_market_count=len(markets),
            active_market_count_5m=active_5m,
            active_market_count_15m=active_15m,
            recent_prediction_count=len(predictions),
            evaluated_prediction_count=evaluated_count,
            pending_prediction_count=len(predictions) - evaluated_count,
            feed_count=len(feeds),
            unhealthy_feed_count=unhealthy_feeds,
        )

    def markets(
        self,
        *,
        now: datetime | None = None,
        horizon_seconds: int | None = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        return self._queries.active_markets(
            now=now or datetime.now(UTC),
            horizon_seconds=horizon_seconds,
            limit=limit,
        )

    def predictions(
        self,
        *,
        horizon_seconds: int | None = None,
        evaluation_state: str | None = None,
        trade: bool | None = None,
        limit: int = 50,
        before_recorded_at: datetime | None = None,
    ) -> list[dict[str, Any]]:
        return self._queries.predictions(
            horizon_seconds=horizon_seconds,
            evaluation_state=evaluation_state,
            trade=trade,
            limit=limit,
            before_recorded_at=before_recorded_at,
        )

    def performance(self) -> PerformanceResponse:
        return build_performance(self._queries.evaluation_rows(limit=100))
