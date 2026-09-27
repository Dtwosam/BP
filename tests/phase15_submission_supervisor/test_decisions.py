from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "ops" / "phase15_submission_supervisor" / "run.py"
SPEC = importlib.util.spec_from_file_location("bp_phase15_submission_supervisor", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

Decision = MODULE.Decision
decide = MODULE.decide

NOW = datetime(2026, 9, 27, 19, 0, 0, tzinfo=UTC)


def _prepared(*, market_end: datetime | None = None, notional: object = "5") -> dict:
    end = market_end or (NOW - timedelta(seconds=30))
    return {
        "action": "submit",
        "intent_id": "live-intent-" + "a" * 32,
        "prediction_id": "b" * 64,
        "paper_order_id": "c" * 64,
        "request_sha256": "d" * 64,
        "market_end_at": end.isoformat(),
        "target_notional_usd": notional,
    }


def _recorder(*, prepared: dict | None = None, status: str = "prepared", active: bool = False) -> dict:
    return {
        "service_active": active,
        "run_dir": "/var/lib/bp/run",
        "status": {"status": status},
        "prepared": prepared,
    }


def _safe_executor() -> dict:
    return {
        "marker": None,
        "result": None,
        "failure": None,
        "health": {
            "status": "ok",
            "kill_switch_engaged": True,
            "activation_valid": False,
            "submission_ready": False,
            "live_order_submitted": False,
            "open_order_count": 0,
            "clean_for_canary": True,
        },
    }


def test_running_watcher_waits_and_inactive_empty_state_restarts() -> None:
    running = decide(
        _recorder(prepared=None, status="running", active=True),
        None,
        observed_at=NOW,
    )
    assert running == Decision("wait", "watcher_running")

    inactive = decide(
        _recorder(prepared=None, status="expired", active=False),
        None,
        observed_at=NOW,
    )
    assert inactive == Decision("start", "watcher_inactive_without_prepared_candidate")


def test_candidate_waits_until_market_end_grace_then_reconciles() -> None:
    future = _prepared(market_end=NOW + timedelta(seconds=5))
    assert decide(
        _recorder(prepared=future),
        _safe_executor(),
        observed_at=NOW,
    ) == Decision("wait", "candidate_window_not_terminal")

    expired = _prepared(market_end=NOW - timedelta(seconds=21))
    assert decide(
        _recorder(prepared=expired),
        _safe_executor(),
        observed_at=NOW,
    ) == Decision("reconcile_restart", "candidate_expired_without_network_attempt")


def test_only_clear_accepted_real_submission_is_success() -> None:
    prepared = _prepared()
    intent = prepared["intent_id"]
    executor = _safe_executor()
    executor["marker"] = {
        "intent_id": intent,
        "started_at": (NOW - timedelta(seconds=2)).isoformat(),
    }
    executor["result"] = {
        "intent_id": intent,
        "network_submission_attempt_consumed": True,
        "authorization_slot_consumed": True,
        "accepted": True,
        "real_order_submitted": True,
    }
    assert decide(
        _recorder(prepared=prepared),
        executor,
        observed_at=NOW,
    ) == Decision("success", "real_five_dollar_submission_recorded")


def test_consumed_network_attempt_without_success_halts() -> None:
    prepared = _prepared()
    intent = prepared["intent_id"]
    executor = _safe_executor()
    executor["marker"] = {
        "intent_id": intent,
        "started_at": (NOW - timedelta(seconds=2)).isoformat(),
    }
    executor["result"] = {
        "intent_id": intent,
        "network_submission_attempt_consumed": True,
        "authorization_slot_consumed": True,
        "accepted": False,
        "real_order_submitted": False,
    }
    assert decide(
        _recorder(prepared=prepared),
        executor,
        observed_at=NOW,
    ) == Decision("halt", "network_attempt_terminal_without_success")


def test_privileged_failure_after_network_attempt_halts() -> None:
    prepared = _prepared()
    intent = prepared["intent_id"]
    executor = _safe_executor()
    executor["marker"] = {
        "intent_id": intent,
        "started_at": (NOW - timedelta(seconds=2)).isoformat(),
    }
    executor["failure"] = {
        "intent_id": intent,
        "network_submission_attempt_consumed": True,
        "real_order_submitted": False,
    }
    assert decide(
        _recorder(prepared=prepared),
        executor,
        observed_at=NOW,
    ) == Decision("halt", "network_attempt_failed_closed")


def test_ambiguous_network_attempt_never_retries() -> None:
    prepared = _prepared()
    executor = _safe_executor()
    executor["marker"] = {
        "intent_id": prepared["intent_id"],
        "started_at": (NOW - timedelta(seconds=46)).isoformat(),
    }
    assert decide(
        _recorder(prepared=prepared),
        executor,
        observed_at=NOW,
    ) == Decision("halt", "network_attempt_result_ambiguous")


def test_marker_identity_mismatch_halts() -> None:
    prepared = _prepared()
    executor = _safe_executor()
    executor["marker"] = {
        "intent_id": "live-intent-" + "f" * 32,
        "started_at": NOW.isoformat(),
    }
    assert decide(
        _recorder(prepared=prepared),
        executor,
        observed_at=NOW,
    ) == Decision("halt", "network_attempt_marker_intent_mismatch")


def test_executor_must_be_safe_idle_before_recycling_candidate() -> None:
    prepared = _prepared()
    executor = _safe_executor()
    executor["health"]["kill_switch_engaged"] = False
    assert decide(
        _recorder(prepared=prepared),
        executor,
        observed_at=NOW,
    ) == Decision("halt", "executor_not_safe_idle_without_attempt_marker")


def test_supervisor_refuses_non_five_dollar_candidate() -> None:
    prepared = _prepared(notional="6")
    assert decide(
        _recorder(prepared=prepared),
        _safe_executor(),
        observed_at=NOW,
    ) == Decision("halt", "prepared_candidate_notional_not_five")
