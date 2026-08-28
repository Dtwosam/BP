from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from bp_engine.dashboard.app import create_app
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from bp_engine.config import Settings


class StubService:
    def health(self, *, now=None):
        generated_at = now or datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
        return {
            "status": "ok",
            "database_status": "ok",
            "generated_at": generated_at,
        }

    def overview(self, *, now=None):
        generated_at = now or datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
        return {
            "generated_at": generated_at,
            "mode": "research",
            "live_trading_enabled": False,
            "verified_horizons_seconds": (300, 900),
            "database_status": "ok",
            "active_market_count": 1,
            "active_market_count_5m": 1,
            "active_market_count_15m": 0,
            "recent_prediction_count": 1,
            "evaluated_prediction_count": 0,
            "pending_prediction_count": 1,
            "feed_count": 1,
            "unhealthy_feed_count": 0,
            "paper_pnl_status": "unavailable_until_phase_12",
        }

    def markets(self, *, now=None, horizon_seconds=None, limit=25):
        del now, horizon_seconds, limit
        return [
            {
                "condition_id": "condition-1",
                "horizon_seconds": 300,
                "current_up_best_bid": "0.61",
                "prediction": {
                    "calibrated_probability": Decimal("0.620000000000000000"),
                    "cost_adjusted_edge": Decimal("0.005000000000000000"),
                },
            }
        ]

    def predictions(
        self,
        *,
        horizon_seconds=None,
        evaluation_state=None,
        trade=None,
        limit=50,
        before_recorded_at=None,
    ):
        del horizon_seconds, evaluation_state, trade, limit, before_recorded_at
        return []

    def performance(self):
        return {
            "status": "pending",
            "evaluated_count": 0,
            "accuracy": None,
            "calibrated_brier": None,
            "calibrated_log_loss": None,
            "horizons": [],
            "calibration_buckets": [],
            "research_hypothetical_assumed_cost_pnl": None,
            "paper_pnl_status": "unavailable_until_phase_12",
        }


class FailingService(StubService):
    def overview(self, *, now=None):
        del now
        raise SQLAlchemyError("postgresql://user:password@database.internal/bp")


def _client(service=None) -> TestClient:
    app = create_app(service or StubService(), Settings(_env_file=None))
    return TestClient(app)


def test_api_is_get_only_and_validates_limits_and_horizons() -> None:
    client = _client()

    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/overview").status_code == 200
    assert client.get("/api/v1/markets?limit=101").status_code == 422
    assert client.get("/api/v1/markets?horizon_seconds=600").status_code == 422
    assert client.get("/api/v1/predictions?evaluation_state=bogus").status_code == 422
    assert client.post("/api/v1/overview").status_code == 405


def test_api_serializes_decimal_values_as_strings() -> None:
    response = _client().get("/api/v1/markets")

    assert response.status_code == 200
    prediction = response.json()[0]["prediction"]
    assert prediction["calibrated_probability"] == "0.620000000000000000"
    assert prediction["cost_adjusted_edge"] == "0.005000000000000000"


def test_database_failure_is_sanitized() -> None:
    response = _client(FailingService()).get("/api/v1/overview")

    assert response.status_code == 503
    assert response.json() == {"detail": "dashboard data unavailable"}
    body = response.text.lower()
    assert "postgresql" not in body
    assert "password" not in body
    assert "database.internal" not in body
