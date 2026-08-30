from __future__ import annotations

from datetime import UTC, datetime

from bp_engine.dashboard.service import build_dashboard_snapshot


class BaseRepository:
    def list_active_markets(self, now: datetime):
        return []

    def list_feed_health(self, now: datetime):
        return []

    def list_predictions(self, limit: int = 100):
        return []

    def list_performance_predictions(self):
        return []

    def list_evaluations(self):
        return []


class ReadinessRepository(BaseRepository):
    def get_live_readiness_evidence(self):
        return {
            "eligible": False,
            "activation_authorized": False,
            "kill_switch_engaged": True,
            "geoblock_blocked": False,
            "country": "NL",
            "region": "NH",
            "wallet_configured": False,
            "reconciliation_status": "blocked",
            "critical_discrepancy_count": 2,
        }


def test_dashboard_defaults_live_readiness_to_unavailable_and_execution_off() -> None:
    snapshot = build_dashboard_snapshot(
        BaseRepository(),
        now=datetime(2026, 8, 29, 17, 0, tzinfo=UTC),
    )

    assert snapshot["execution_available"] is False
    assert snapshot["mode"]["execution_available"] is False
    assert snapshot["live_readiness"] == {
        "eligible": False,
        "authorized": False,
        "kill_switch_engaged": True,
        "geoblock_blocked": None,
        "country": None,
        "region": None,
        "wallet_configured": False,
        "reconciliation_status": "unavailable",
        "critical_discrepancy_count": None,
    }


def test_dashboard_maps_live_readiness_evidence_without_enabling_execution() -> None:
    snapshot = build_dashboard_snapshot(
        ReadinessRepository(),
        now=datetime(2026, 8, 29, 17, 0, tzinfo=UTC),
    )

    assert snapshot["execution_available"] is False
    assert snapshot["mode"]["execution_available"] is False
    assert snapshot["live_readiness"] == {
        "eligible": False,
        "authorized": False,
        "kill_switch_engaged": True,
        "geoblock_blocked": False,
        "country": "NL",
        "region": "NH",
        "wallet_configured": False,
        "reconciliation_status": "blocked",
        "critical_discrepancy_count": 2,
    }
