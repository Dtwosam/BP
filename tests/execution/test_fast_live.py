from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bp_engine.execution.fast_live import (
    FastLiveError,
    create_envelope,
    create_warmup_message,
    marketable_depth,
    project_state_sha256,
    verify_envelope,
    verify_runtime_authorization,
    verify_source_authorization,
    verify_warmup_message,
)

MAIN = "a" * 40
KEY = bytes(range(32))
KEY_ID = "phase15-fast-live-v1"
ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "PROJECT_STATE.json"


def _state(now: datetime) -> dict[str, object]:
    return {
        "source_of_truth_version": "synthetic-fast-live-v1",
        "phase_15_v3_live_canary": {
            "fast_live_preauthorization": {
                "status": "AUTHORIZED_NOT_CONSUMED",
                "authorized": True,
                "consumed": False,
                "authorization_id": "fast-live-auth-1",
                "authorized_at_main": "b" * 40,
                "target_notional_usd": 5,
                "max_trade_size_usd": 10,
                "max_total_exposure_usd": 10,
                "max_daily_loss_usd": 10,
                "max_consecutive_losses": 1,
                "min_edge": 0.075,
                "max_network_submission_attempts": 1,
                "requires_telegram_approval": False,
                "prediction_version": "v3-frozen-paper-v1",
                "execution_version": "paper-execution-v3-frozen-v1",
                "executor_country": "ZA",
                "max_transit_seconds": 2,
                "expires_at": (now + timedelta(minutes=10)).isoformat(),
            }
        },
    }


def _runtime(state: dict[str, object], now: datetime) -> dict[str, object]:
    return {
        "schema_version": 1,
        "purpose": "phase15-v3-fast-live-v1",
        "authorized": True,
        "authorization_id": "fast-live-auth-1",
        "release_main": MAIN,
        "project_state_sha256": project_state_sha256(state),
        "max_network_submission_attempts": 1,
        "target_notional_usd": 5,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
    }


def _prepared(now: datetime) -> dict[str, object]:
    return {
        "status": "prepared",
        "intent_id": "live-intent-fast-1",
        "request_id": "live-request-fast-1",
        "risk_decision_id": "risk-fast-1",
        "prediction_id": "prediction-fast-1",
        "paper_order_id": "paper-fast-1",
        "market_end_at": (now + timedelta(seconds=50)).isoformat(),
        "timing": {
            "prediction_scheduled_at": (now - timedelta(seconds=2)).isoformat(),
            "prediction_recorded_at": (now - timedelta(seconds=1)).isoformat(),
            "paper_order_submitted_at": (now - timedelta(milliseconds=500)).isoformat(),
            "prepared_observed_at": now.isoformat(),
        },
        "request": {
            "prediction_id": "prediction-fast-1",
            "prediction_semantic_sha256": "1" * 64,
            "condition_id": "condition-fast-1",
            "token_id": "token-fast-1",
            "selected_side": "up",
            "action": "BUY",
            "requested_shares": "8.238141",
            "target_notional_usd": "5",
            "submitted_at": (now - timedelta(seconds=1)).isoformat(),
            "arrival_at": (now - timedelta(milliseconds=750)).isoformat(),
            "expires_at": (now + timedelta(seconds=20)).isoformat(),
            "limit_price": "0.59",
            "execution_version": "paper-execution-v3-frozen-v1",
            "execution_config_sha256": "2" * 64,
        },
        "policy": {
            "policy_version": "v3-live-canary-v1",
            "max_trade_size_usd": "10",
            "max_total_exposure_usd": "10",
            "max_daily_loss_usd": "10",
            "max_consecutive_losses": 1,
            "max_submission_attempts": 1,
        },
    }


def test_repository_source_truth_remains_fail_closed() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    with pytest.raises(FastLiveError, match="source-truth authorization missing"):
        verify_source_authorization(
            state,
            expected_main=MAIN,
            observed_at=datetime(2026, 9, 28, 20, 0, tzinfo=UTC),
        )


def test_current_source_truth_shape_is_required() -> None:
    with pytest.raises(FastLiveError, match="source-truth authorization missing"):
        verify_source_authorization(
            {"phase_15_v3_live_canary": {}},
            expected_main=MAIN,
            observed_at=datetime(2026, 9, 28, 20, 0, tzinfo=UTC),
        )


def test_runtime_authorization_is_bound_to_exact_source_truth() -> None:
    now = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
    state = _state(now)
    runtime = _runtime(state, now)
    verified = verify_runtime_authorization(
        runtime,
        state=state,
        expected_main=MAIN,
        observed_at=now + timedelta(seconds=1),
    )
    assert verified["authorization_id"] == "fast-live-auth-1"

    changed = copy.deepcopy(state)
    phase = changed["phase_15_v3_live_canary"]
    assert isinstance(phase, dict)
    auth = phase["fast_live_preauthorization"]
    assert isinstance(auth, dict)
    auth["target_notional_usd"] = 6
    with pytest.raises(FastLiveError):
        verify_runtime_authorization(
            runtime,
            state=changed,
            expected_main=MAIN,
            observed_at=now + timedelta(seconds=1),
        )


def test_envelope_is_exact_bound_short_lived_and_tamper_evident() -> None:
    now = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
    state = _state(now)
    runtime = _runtime(state, now)
    prepared = _prepared(now)
    envelope = create_envelope(
        prepared,
        runtime_authorization=runtime,
        key=KEY,
        key_id=KEY_ID,
        created_at=now,
    )

    verified = verify_envelope(
        envelope,
        runtime_authorization=runtime,
        key=KEY,
        expected_key_id=KEY_ID,
        observed_at=now + timedelta(milliseconds=120),
    )
    assert verified["intent_id"] == "live-intent-fast-1"
    assert verified["request"]["limit_price"] == "0.59"

    tampered = copy.deepcopy(envelope)
    prepared_copy = tampered["prepared"]
    assert isinstance(prepared_copy, dict)
    request = prepared_copy["request"]
    assert isinstance(request, dict)
    request["limit_price"] = "0.60"
    with pytest.raises(FastLiveError, match="hmac mismatch"):
        verify_envelope(
            tampered,
            runtime_authorization=runtime,
            key=KEY,
            expected_key_id=KEY_ID,
            observed_at=now + timedelta(milliseconds=130),
        )

    with pytest.raises(FastLiveError, match="expired"):
        verify_envelope(
            envelope,
            runtime_authorization=runtime,
            key=KEY,
            expected_key_id=KEY_ID,
            observed_at=now + timedelta(seconds=3),
        )


def test_warmup_message_is_authenticated_and_short_lived() -> None:
    now = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
    state = _state(now)
    runtime = _runtime(state, now)
    warmup = create_warmup_message(
        condition_id="condition-warm-1",
        token_ids=("up-token-warm", "down-token-warm"),
        runtime_authorization=runtime,
        key=KEY,
        key_id=KEY_ID,
        created_at=now,
    )
    verified = verify_warmup_message(
        warmup,
        runtime_authorization=runtime,
        key=KEY,
        expected_key_id=KEY_ID,
        observed_at=now + timedelta(seconds=1),
    )
    assert verified["condition_id"] == "condition-warm-1"
    assert verified["token_ids"] == ("up-token-warm", "down-token-warm")

    tampered = copy.deepcopy(warmup)
    tampered["token_ids"] = ["other-up", "other-down"]
    with pytest.raises(FastLiveError, match="hmac mismatch"):
        verify_warmup_message(
            tampered,
            runtime_authorization=runtime,
            key=KEY,
            expected_key_id=KEY_ID,
            observed_at=now + timedelta(seconds=1),
        )


def test_marketable_depth_allows_partial_immediate_fill_at_limit() -> None:
    result = marketable_depth(
        (("0.57", "3"), ("0.58", "4"), ("0.59", "10"), ("0.60", "50")),
        limit_price="0.59",
        requested_shares="8.238141",
    )
    assert result["marketable"] is True
    assert result["full_size_marketable"] is True
    assert result["best_ask"] == "0.57"
    assert result["marketable_depth"] == "17"

    stale = marketable_depth(
        (("0.60", "20"),),
        limit_price="0.59",
        requested_shares="8.238141",
    )
    assert stale["marketable"] is False

    thin = marketable_depth(
        (("0.58", "8"),),
        limit_price="0.59",
        requested_shares="8.238141",
    )
    assert thin["marketable"] is True
    assert thin["full_size_marketable"] is False
