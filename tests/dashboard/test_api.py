from __future__ import annotations

import json

from bp_engine.dashboard.api import route_request


def _snapshot() -> dict[str, object]:
    return {
        "generated_at": "2026-08-28T12:00:00+00:00",
        "mode": {
            "trading_mode": "RESEARCH",
            "live_trading_enabled": False,
            "execution_available": False,
            "paper_execution_available": False,
        },
        "active_markets": [],
        "feed_health": [],
        "performance": [],
        "prediction_history": [],
        "paper_pnl": {"status": "UNAVAILABLE_UNTIL_PHASE_12", "value": None},
    }


def test_snapshot_route_is_get_only_json_and_not_cached() -> None:
    status, headers, body = route_request("GET", "/api/v1/snapshot", _snapshot)

    assert status == 200
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"
    assert json.loads(body)["mode"]["trading_mode"] == "RESEARCH"


def test_health_route_is_small_and_safe() -> None:
    status, headers, body = route_request("GET", "/health", _snapshot)

    payload = json.loads(body)
    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert payload == {
        "status": "ok",
        "mode": "RESEARCH",
        "live_trading_enabled": False,
    }


def test_unknown_route_is_not_found() -> None:
    status, _, body = route_request("GET", "/api/v1/orders", _snapshot)

    assert status == 404
    assert json.loads(body) == {"error": "not_found"}


def test_non_get_methods_are_rejected_without_calling_snapshot_provider() -> None:
    called = False

    def provider() -> dict[str, object]:
        nonlocal called
        called = True
        return _snapshot()

    status, headers, body = route_request("POST", "/api/v1/snapshot", provider)

    assert status == 405
    assert headers["Allow"] == "GET"
    assert json.loads(body) == {"error": "method_not_allowed"}
    assert called is False
