from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from bp_telegram_auto_approver.contract import (
    _APPROVAL_SOURCE,
    APPROVAL_CONTRACT_BLOB_SHA,
    LOCAL_DEADLINE_SAFETY_MARGIN_SECONDS,
    CallbackButton,
    ContractMismatch,
    approval_deadline,
    approval_source,
    classify_keyboard,
    git_blob_sha1,
    is_exact_approve_callback,
    parse_listener_approval_edit,
    parse_prompt,
    verify_approval_contract,
)

from tests.telegram_auto_approver.support import NONCE, keyboard, prompt_text


def test_phase15_constants_and_generated_nonce_stay_pinned() -> None:
    source = approval_source()
    assert source.TARGET_NOTIONAL_USD == Decimal("5")
    assert source.MAX_APPROVAL_LIFETIME_SECONDS == 45
    assert source.MIN_APPROVAL_WINDOW_SECONDS == 20
    assert source.SUBMIT_SAFETY_FLOOR_SECONDS == 10
    now = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    pending = source.new_pending(
        {
            "status": "prepared",
            "action": "submit",
            "intent_id": "live-intent-123",
            "prediction_id": "prediction-123",
            "paper_order_id": "paper-123",
            "market_end_at": (now + timedelta(seconds=50)).isoformat(),
            "request": {
                "selected_side": "down",
                "limit_price": "0.72",
                "requested_shares": "6.81",
                "target_notional_usd": "5",
            },
            "policy": {"policy_version": "v3-live-canary-v1", "max_submission_attempts": 1},
        },
        telegram_user_id=1,
        telegram_chat_id=1,
        created_at=now,
    )
    assert re.fullmatch(r"[A-Za-z0-9_-]{16}", pending["nonce"])
    for _ in range(20):
        assert re.fullmatch(r"[A-Za-z0-9_-]{16}", secrets.token_urlsafe(12))


def test_real_prompt_and_keyboard_round_trip() -> None:
    observed = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    text = prompt_text(observed, side="up")
    parsed = parse_prompt(text)
    assert parsed is not None
    assert parsed.side == "UP"
    assert parsed.limit_price == "0.72"
    assert parsed.shares == "6.81"
    assert parsed.maximum_spend == "5"
    assert parsed.time_remaining == Decimal("50.0")
    nonce, reason = classify_keyboard(keyboard())
    assert reason == ""
    assert nonce == NONCE
    assert is_exact_approve_callback(keyboard()[0][0].callback_data or b"", NONCE)
    assert not is_exact_approve_callback(keyboard()[0][1].callback_data or b"", NONCE)


def test_prompt_rejects_mutation_spend_and_short_window() -> None:
    observed = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    text = prompt_text(observed)
    assert parse_prompt(text.replace("Maximum spend: $5", "Maximum spend: $6")) is None
    assert parse_prompt(text.replace("DOWN", "SIDEWAYS")) is None
    assert parse_prompt(text + "\nextra") is None
    short = prompt_text(observed, seconds=20).replace(
        "Time remaining: 20.0s",
        "Time remaining: 19.9s",
    )
    assert parse_prompt(short) is None


def test_keyboard_rejects_missing_reordered_and_extra_buttons() -> None:
    source = approval_source()
    approve = CallbackButton("APPROVE", source.callback_data("approve", NONCE).encode())
    skip = CallbackButton("SKIP", source.callback_data("skip", NONCE).encode())
    assert classify_keyboard(()) == (None, "no_approve_button")
    assert classify_keyboard(((skip,),))[1] == "no_approve_button"
    assert classify_keyboard(((skip, approve),))[1] == "keyboard_mismatch"
    assert classify_keyboard(((approve, skip, approve),))[1] == "keyboard_mismatch"
    assert classify_keyboard(((approve,), (skip,)))[1] == "keyboard_mismatch"
    url = CallbackButton("APPROVE", approve.callback_data, url="https://example.invalid")
    assert classify_keyboard(((url, skip),))[1] == "keyboard_mismatch"
    other = CallbackButton("SKIP", source.callback_data("skip", "ZZZZZZZZZZZZZZZZ").encode())
    assert classify_keyboard(((approve, other),))[1] == "keyboard_mismatch"


def test_deadline_uses_prompt_snapshot_phase15_caps_and_two_second_margin() -> None:
    sent = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    assert LOCAL_DEADLINE_SAFETY_MARGIN_SECONDS == 2
    assert approval_deadline(sent, Decimal("50.0")) == sent + timedelta(seconds=38)
    assert approval_deadline(sent, Decimal("20.0")) == sent + timedelta(seconds=8)
    assert approval_deadline(sent, Decimal("100.0")) == sent + timedelta(seconds=43)


def test_reviewed_contract_blob_is_pinned_and_a_change_fails_closed(tmp_path) -> None:
    reviewed = _APPROVAL_SOURCE.read_bytes()
    assert git_blob_sha1(reviewed) == APPROVAL_CONTRACT_BLOB_SHA
    assert verify_approval_contract() == APPROVAL_CONTRACT_BLOB_SHA
    changed = tmp_path / "telegram_approval.py"
    changed.write_bytes(reviewed + b"\n# unreviewed contract drift\n")
    with pytest.raises(ContractMismatch, match="APPROVAL_CONTRACT_MISMATCH") as caught:
        verify_approval_contract(changed)
    assert caught.value.actual_blob != APPROVAL_CONTRACT_BLOB_SHA
    missing = tmp_path / "missing.py"
    with pytest.raises(ContractMismatch, match="APPROVAL_CONTRACT_MISMATCH"):
        verify_approval_contract(missing)


def test_unreviewed_pin_refuses_to_load_the_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        "bp_telegram_auto_approver.contract.APPROVAL_CONTRACT_BLOB_SHA",
        "0" * 40,
    )
    with pytest.raises(ContractMismatch, match="APPROVAL_CONTRACT_MISMATCH"):
        approval_source()


def test_listener_edit_parser_accepts_only_exact_approved_text() -> None:
    decision = "2026-09-27T12:00:05+00:00"
    text = f"BP V3 trade APPROVED\nIntent: live-intent-123\nDecision time: {decision}"
    parsed = parse_listener_approval_edit(text)
    assert parsed is not None
    assert parsed.intent_id == "live-intent-123"
    assert parsed.decision_at == datetime.fromisoformat(decision)
    assert parse_listener_approval_edit(text.replace("APPROVED", "SKIPPED")) is None
    assert parse_listener_approval_edit(text + "\nmore") is None
    with pytest.raises(ValueError):
        approval_deadline(datetime(2026, 9, 27, 12, 0), Decimal("50.0"))
