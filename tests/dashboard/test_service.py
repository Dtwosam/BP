from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from bp_engine.dashboard.service import DashboardService

from bp_engine.config import Settings


class FakeQueries:
    def __init__(self, evaluation_rows: list[dict[str, object]] | None = None) -> None:
        self._evaluation_rows = evaluation_rows or []

    def health(self) -> dict[str, str]:
        return {"database": "ok"}

    def active_markets(self, *, now, horizon_seconds=None, limit=25):
        del now
        rows = [
            {"condition_id": "c-1", "horizon_seconds": 300},
            {"condition_id": "c-2", "horizon_seconds": 300},
            {"condition_id": "c-3", "horizon_seconds": 900},
        ]
        if horizon_seconds is not None:
            rows = [row for row in rows if row["horizon_seconds"] == horizon_seconds]
        return rows[:limit]

    def predictions(
        self,
        *,
        horizon_seconds=None,
        evaluation_state=None,
        trade=None,
        limit=50,
        before_recorded_at=None,
    ):
        del trade, before_recorded_at
        rows = [
            {"prediction_id": "p-1", "horizon_seconds": 300, "evaluation": {"correct": True}},
            {"prediction_id": "p-2", "horizon_seconds": 300, "evaluation": None},
            {"prediction_id": "p-3", "horizon_seconds": 900, "evaluation": None},
        ]
        if horizon_seconds is not None:
            rows = [row for row in rows if row["horizon_seconds"] == horizon_seconds]
        if evaluation_state == "evaluated":
            rows = [row for row in rows if row["evaluation"] is not None]
        elif evaluation_state == "pending":
            rows = [row for row in rows if row["evaluation"] is None]
        return rows[:limit]

    def feed_health(self, *, now, incident_window):
        del now, incident_window
        return [
            {"source": "polymarket", "stream": "market", "status": "healthy"},
            {"source": "bybit", "stream": "spot", "status": "stale"},
        ]

    def evaluation_rows(self, *, limit=100):
        return self._evaluation_rows[:limit]


def test_overview_reports_truthful_mode_counts_and_paper_pnl_status() -> None:
    service = DashboardService(FakeQueries(), Settings(_env_file=None))
    now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)

    overview = service.overview(now=now)

    assert overview.generated_at == now
    assert overview.mode == "research"
    assert overview.live_trading_enabled is False
    assert overview.verified_horizons_seconds == (300, 900)
    assert overview.database_status == "ok"
    assert overview.active_market_count == 3
    assert overview.active_market_count_5m == 2
    assert overview.active_market_count_15m == 1
    assert overview.recent_prediction_count == 3
    assert overview.evaluated_prediction_count == 1
    assert overview.pending_prediction_count == 2
    assert overview.feed_count == 2
    assert overview.unhealthy_feed_count == 1
    assert overview.paper_pnl_status == "unavailable_until_phase_12"


def test_performance_is_pending_without_official_evaluations() -> None:
    service = DashboardService(FakeQueries(), Settings(_env_file=None))

    performance = service.performance()

    assert performance.status == "pending"
    assert performance.evaluated_count == 0
    assert performance.accuracy is None
    assert performance.paper_pnl_status == "unavailable_until_phase_12"


def test_performance_uses_evaluation_rows_without_relabeling_research_pnl() -> None:
    rows = [
        {
            "horizon_seconds": 300,
            "calibrated_probability": Decimal("0.8"),
            "official_target": 1,
            "correct": True,
            "calibrated_brier": Decimal("0.04"),
            "calibrated_log_loss": Decimal("0.22"),
            "hypothetical_assumed_cost_pnl": Decimal("0.10"),
        }
    ]
    service = DashboardService(FakeQueries(rows), Settings(_env_file=None))

    performance = service.performance()

    assert performance.status == "evaluated"
    assert performance.evaluated_count == 1
    assert performance.research_hypothetical_assumed_cost_pnl == Decimal("0.10")
    assert performance.paper_pnl_status == "unavailable_until_phase_12"


def test_markets_and_predictions_delegate_bounded_filters() -> None:
    service = DashboardService(FakeQueries(), Settings(_env_file=None))
    now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)

    markets = service.markets(now=now, horizon_seconds=900, limit=10)
    pending = service.predictions(
        horizon_seconds=300,
        evaluation_state="pending",
        trade=None,
        limit=10,
        before_recorded_at=None,
    )

    assert [row["horizon_seconds"] for row in markets] == [900]
    assert [row["prediction_id"] for row in pending] == ["p-2"]
