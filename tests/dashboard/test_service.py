from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bp_engine.dashboard.service import build_dashboard_snapshot, summarize_performance


class FakeRepository:
    def __init__(self) -> None:
        now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
        self.active_markets = [
            {
                "condition_id": "c1",
                "slug": "btc-updown-5m",
                "question": "BTC Up or Down?",
                "horizon_seconds": 300,
                "start_at": now - timedelta(minutes=1),
                "end_at": now + timedelta(minutes=4),
                "calibrated_probability": 0.62,
                "market_probability": 0.55,
                "predicted_side": "Up",
                "selected_side": "Up",
                "selected_ask": 0.56,
                "selected_bid": 0.54,
                "cost_adjusted_edge": 0.04,
                "decision_reason": "EDGE_THRESHOLD_MET",
                "trade": True,
                "scheduled_at": now - timedelta(seconds=5),
                "recorded_at": now - timedelta(seconds=4),
            }
        ]
        self.feed_health = [
            {
                "source": "coinbase",
                "stream": "spot",
                "status": "connected",
                "last_received_at": now - timedelta(seconds=2),
                "last_source_timestamp": now - timedelta(seconds=3),
                "updated_at": now - timedelta(seconds=1),
                "age_seconds": 2.0,
                "details": {},
            }
        ]
        self.predictions = [
            {
                "prediction_id": "p1",
                "condition_id": "c1",
                "slug": "btc-updown-5m",
                "horizon_seconds": 300,
                "scheduled_at": now - timedelta(minutes=10),
                "recorded_at": now - timedelta(minutes=10),
                "calibrated_probability": 0.8,
                "predicted_side": "Up",
                "market_probability": 0.7,
                "selected_side": "Up",
                "selected_ask": 0.71,
                "selected_bid": 0.69,
                "cost_adjusted_edge": 0.07,
                "decision_reason": "EDGE_THRESHOLD_MET",
                "trade": True,
                "official_outcome": "Up",
                "official_target": 1,
                "evaluated_at": now - timedelta(minutes=4),
                "correct": True,
                "calibrated_brier": 0.04,
                "calibrated_log_loss": 0.223143551,
            },
            {
                "prediction_id": "p2",
                "condition_id": "c2",
                "slug": "btc-updown-5m-2",
                "horizon_seconds": 300,
                "scheduled_at": now - timedelta(minutes=5),
                "recorded_at": now - timedelta(minutes=5),
                "calibrated_probability": 0.7,
                "predicted_side": "Up",
                "market_probability": 0.65,
                "selected_side": "Up",
                "selected_ask": 0.66,
                "selected_bid": 0.64,
                "cost_adjusted_edge": 0.02,
                "decision_reason": "NO_TRADE_EDGE",
                "trade": False,
                "official_outcome": None,
                "official_target": None,
                "evaluated_at": None,
                "correct": None,
                "calibrated_brier": None,
                "calibrated_log_loss": None,
            },
        ]

    def list_active_markets(self, now: datetime):
        return self.active_markets

    def list_feed_health(self, now: datetime):
        return self.feed_health

    def list_predictions(self, limit: int = 100):
        return self.predictions[:limit]


def test_performance_uses_only_officially_evaluated_predictions() -> None:
    predictions = [
        {"prediction_id": "p1", "horizon_seconds": 300, "calibrated_probability": 0.8},
        {"prediction_id": "p2", "horizon_seconds": 300, "calibrated_probability": 0.7},
        {"prediction_id": "p3", "horizon_seconds": 900, "calibrated_probability": 0.4},
    ]
    evaluations = [
        {
            "prediction_id": "p1",
            "official_target": 1,
            "correct": True,
            "calibrated_brier": 0.04,
            "calibrated_log_loss": 0.223143551,
        },
        {
            "prediction_id": "p3",
            "official_target": 0,
            "correct": True,
            "calibrated_brier": 0.16,
            "calibrated_log_loss": 0.510825624,
        },
    ]

    result = summarize_performance(predictions, evaluations)

    five = next(row for row in result if row["horizon_seconds"] == 300)
    fifteen = next(row for row in result if row["horizon_seconds"] == 900)
    assert five["total_predictions"] == 2
    assert five["evaluated_predictions"] == 1
    assert five["coverage"] == 0.5
    assert five["accuracy"] == 1.0
    assert five["calibrated_brier"] == 0.04
    assert five["calibrated_log_loss"] == 0.223143551
    assert five["calibration_buckets"] == [
        {
            "label": "80-85%",
            "count": 1,
            "mean_probability": 0.8,
            "observed_up_rate": 1.0,
        }
    ]
    assert fifteen["total_predictions"] == 1
    assert fifteen["evaluated_predictions"] == 1
    assert fifteen["coverage"] == 1.0


def test_empty_evaluations_stay_null_instead_of_zero() -> None:
    result = summarize_performance(
        [{"prediction_id": "p1", "horizon_seconds": 300, "calibrated_probability": 0.6}],
        [],
    )
    assert result == [
        {
            "horizon_seconds": 300,
            "total_predictions": 1,
            "evaluated_predictions": 0,
            "coverage": 0.0,
            "accuracy": None,
            "calibrated_brier": None,
            "calibrated_log_loss": None,
            "calibration_buckets": [],
        }
    ]


def test_snapshot_is_read_only_research_and_marks_paper_pnl_unavailable() -> None:
    now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    snapshot = build_dashboard_snapshot(FakeRepository(), now=now)

    assert snapshot["generated_at"] == "2026-08-28T12:00:00+00:00"
    assert snapshot["mode"] == {
        "trading_mode": "RESEARCH",
        "live_trading_enabled": False,
        "execution_available": False,
        "paper_execution_available": False,
    }
    assert snapshot["paper_pnl"] == {
        "status": "UNAVAILABLE_UNTIL_PHASE_12",
        "value": None,
    }
    assert snapshot["active_markets"][0]["condition_id"] == "c1"
    assert snapshot["feed_health"][0]["source"] == "coinbase"
    assert snapshot["prediction_history"][0]["prediction_id"] == "p1"
    assert snapshot["performance"][0]["evaluated_predictions"] == 1
