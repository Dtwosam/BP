from __future__ import annotations

import time
from datetime import timedelta
from decimal import Decimal

import pytest
from bp_telegram_auto_approver.contract import (
    CallbackButton,
    ContractMismatch,
    approval_deadline,
    approval_source,
)
from bp_telegram_auto_approver.decider import ClickResult, IdentityMismatch

from tests.telegram_auto_approver.support import (
    BOT_ID,
    BOT_USERNAME,
    NONCE,
    STARTED,
    ClickRecorder,
    incoming,
    keyboard,
    open_decider,
    prompt_text,
)


def _events(logger) -> list[str]:
    return [name for name, _fields in logger.events]


def test_dry_run_logs_would_approve_and_does_not_click(tmp_path) -> None:
    decider, logger, _clock, store = open_decider(tmp_path / "state.sqlite", live=False)
    clicker = ClickRecorder()
    decision = decider.handle_message(incoming(), clicker)
    assert decision.event == "WOULD_APPROVE"
    assert clicker.calls == []
    assert "APPROVAL_VALIDATED" in _events(logger)
    assert store.get_by_message(BOT_ID, 10)["status"] == "would_approve"
    assert store.get_by_message(BOT_ID, 10)["intent_id"] is None
    assert store.get_by_message(BOT_ID, 10)["request_sha256"] is None
    validated = next(fields for name, fields in logger.events if name == "APPROVAL_VALIDATED")
    assert validated["market"] is None
    assert validated["prediction_id"] is None
    assert validated["selected_side"] == "DOWN"
    assert validated["maximum_spend"] == "5"


def test_live_mode_clicks_only_the_approve_callback_once(tmp_path) -> None:
    decider, logger, _clock, store = open_decider(tmp_path / "state.sqlite", live=True)
    clicker = ClickRecorder()
    decision = decider.handle_message(incoming(), clicker)
    assert decision.event == "APPROVAL_TRIGGERED"
    assert len(clicker.calls) == 1
    chat_id, message_id, data = clicker.calls[0]
    assert (chat_id, message_id) == (BOT_ID, 10)
    expected = approval_source().callback_data("approve", NONCE).encode("ascii")
    assert data == expected
    assert not data.startswith(b"skip:")
    assert store.get_by_nonce(NONCE)["status"] == "clicked"
    again = decider.handle_message(incoming(), clicker)
    assert again.event == "APPROVAL_ALREADY_PROCESSED"
    assert len(clicker.calls) == 1
    assert "APPROVAL_TRIGGERED" in _events(logger)


def test_wrong_sender_wrong_chat_and_wrong_bot_username(tmp_path) -> None:
    decider, _logger, _clock, _store = open_decider(tmp_path / "state.sqlite")
    clicker = ClickRecorder()
    wrong_sender = decider.handle_message(incoming(sender_id=9), clicker)
    wrong_chat = decider.handle_message(incoming(message_id=11, chat_id=9), clicker)
    wrong_name = decider.handle_message(
        incoming(message_id=12, sender_username="other_bot"),
        clicker,
    )
    assert wrong_sender.reason == "sender_mismatch"
    assert wrong_chat.reason == "chat_mismatch"
    assert wrong_name.reason == "sender_username_mismatch"
    assert {wrong_sender.event, wrong_chat.event, wrong_name.event} == {"UNEXPECTED_SENDER"}
    assert clicker.calls == []


def test_correct_prompt_from_a_different_bot_is_rejected(tmp_path) -> None:
    decider, _logger, _clock, store = open_decider(tmp_path / "state.sqlite", live=True)
    clicker = ClickRecorder()
    decision = decider.handle_message(
        incoming(sender_id=777, chat_id=777, sender_username="other_approval_bot"),
        clicker,
    )
    assert decision.event == "UNEXPECTED_SENDER"
    assert clicker.calls == []
    assert store.get_by_nonce(NONCE) is None


def test_username_resolving_to_a_different_id_fails_closed(tmp_path) -> None:
    decider, logger, _clock, _store = open_decider(tmp_path / "state.sqlite", confirm=False)
    with pytest.raises(IdentityMismatch):
        decider.confirm_identity(user_id=999, username=BOT_USERNAME, is_bot=True)
    clicker = ClickRecorder()
    decision = decider.handle_message(incoming(), clicker)
    assert decision.event == "BOT_IDENTITY_MISMATCH"
    assert clicker.calls == []
    assert "BOT_IDENTITY_MISMATCH" in _events(logger)


def test_missing_approve_button_malformed_and_unrelated_messages(tmp_path) -> None:
    decider, _logger, _clock, _store = open_decider(tmp_path / "state.sqlite", live=True)
    clicker = ClickRecorder()
    source = approval_source()
    skip_only = (
        (CallbackButton("SKIP", source.callback_data("skip", NONCE).encode("ascii")),),
    )
    no_button = decider.handle_message(incoming(buttons=skip_only), clicker)
    malformed = decider.handle_message(
        incoming(message_id=11, text="BP V3 LIVE TRADE READY\nnot the contract"),
        clicker,
    )
    unrelated = decider.handle_message(incoming(message_id=12, text="hello", buttons=()), clicker)
    assert no_button.reason == "no_approve_button"
    assert malformed.reason == "prompt_mismatch"
    assert unrelated.reason == "prompt_mismatch"
    assert {no_button.event, malformed.event, unrelated.event} == {"UNEXPECTED_MESSAGE_FORMAT"}
    assert clicker.calls == []


def test_reordered_and_extra_buttons_are_not_clicked(tmp_path) -> None:
    decider, _logger, _clock, _store = open_decider(tmp_path / "state.sqlite", live=True)
    clicker = ClickRecorder()
    source = approval_source()
    approve = CallbackButton("APPROVE", source.callback_data("approve", NONCE).encode())
    skip = CallbackButton("SKIP", source.callback_data("skip", NONCE).encode())
    reordered = decider.handle_message(incoming(buttons=((skip, approve),)), clicker)
    extra = decider.handle_message(
        incoming(message_id=11, buttons=((approve, skip, skip),)),
        clicker,
    )
    assert reordered.reason == "keyboard_mismatch"
    assert extra.reason == "keyboard_mismatch"
    assert clicker.calls == []


def test_expired_request_is_recorded_and_not_clicked(tmp_path) -> None:
    sent = STARTED + timedelta(seconds=1)
    decider, logger, _clock, store = open_decider(
        tmp_path / "state.sqlite",
        live=True,
        now=sent + timedelta(seconds=40),
    )
    clicker = ClickRecorder()
    decision = decider.handle_message(incoming(sent_at=sent), clicker)
    assert decision.event == "APPROVAL_EXPIRED"
    assert clicker.calls == []
    assert store.get_by_message(BOT_ID, 10)["status"] == "rejected"
    assert "APPROVAL_EXPIRED" in _events(logger)


def test_expired_prompt_seen_after_reconnect_is_not_clicked(tmp_path) -> None:
    sent = STARTED + timedelta(seconds=5)
    text = prompt_text(sent, seconds=20)
    assert "Time remaining: 20.0s" in text
    decider, _logger, _clock, store = open_decider(
        tmp_path / "state.sqlite",
        live=True,
        now=sent + timedelta(seconds=10),
    )
    clicker = ClickRecorder()
    decision = decider.handle_message(
        incoming(sent_at=sent, text=text, seconds=20),
        clicker,
    )
    assert decision.event == "APPROVAL_EXPIRED"
    assert clicker.calls == []
    assert store.get_by_message(BOT_ID, 10)["failure_reason"] == "expired"


def test_two_second_margin_boundary_still_clicks_immediately(tmp_path) -> None:
    sent = STARTED + timedelta(seconds=1)
    deadline = approval_deadline(sent, Decimal("50.0"))
    assert deadline == sent + timedelta(seconds=38)

    early, _logger, _clock, _store = open_decider(
        tmp_path / "early.sqlite",
        live=True,
        now=deadline - timedelta(seconds=1),
    )
    clicker = ClickRecorder()
    started = time.perf_counter()
    decision = early.handle_message(incoming(sent_at=sent), clicker)
    elapsed = time.perf_counter() - started
    assert decision.event == "APPROVAL_TRIGGERED"
    assert len(clicker.calls) == 1
    assert elapsed < 0.5

    exact, _logger, _clock, store = open_decider(
        tmp_path / "exact.sqlite",
        live=True,
        now=deadline,
    )
    exact_clicker = ClickRecorder()
    expired = exact.handle_message(incoming(sent_at=sent), exact_clicker)
    assert expired.event == "APPROVAL_EXPIRED"
    assert exact_clicker.calls == []
    assert store.get_by_message(BOT_ID, 10)["status"] == "rejected"

    inside_old_estimate = deadline + timedelta(seconds=1)
    assert inside_old_estimate < sent + timedelta(seconds=40)
    margin, _logger, _clock, margin_store = open_decider(
        tmp_path / "margin.sqlite",
        live=True,
        now=inside_old_estimate,
    )
    margin_clicker = ClickRecorder()
    margin_decision = margin.handle_message(incoming(sent_at=sent), margin_clicker)
    assert margin_decision.event == "APPROVAL_EXPIRED"
    assert margin_clicker.calls == []
    assert margin_store.get_by_message(BOT_ID, 10)["status"] == "rejected"


def test_changed_contract_does_not_process_or_click(tmp_path, monkeypatch) -> None:
    decider, _logger, _clock, store = open_decider(tmp_path / "state.sqlite", live=True)
    clicker = ClickRecorder()

    def fail(path=None):
        raise ContractMismatch(actual_blob="unreviewed")

    monkeypatch.setattr(
        "bp_telegram_auto_approver.decider.verify_approval_contract",
        fail,
    )
    with pytest.raises(ContractMismatch, match="APPROVAL_CONTRACT_MISMATCH"):
        decider.handle_message(incoming(), clicker)
    assert clicker.calls == []
    assert store.get_by_nonce(NONCE) is None


def test_duplicate_message_and_same_nonce_on_another_message(tmp_path) -> None:
    decider, _logger, _clock, store = open_decider(tmp_path / "state.sqlite", live=True)
    clicker = ClickRecorder()
    first = decider.handle_message(incoming(), clicker)
    duplicate = decider.handle_message(incoming(), clicker)
    other_message = decider.handle_message(incoming(message_id=11), clicker)
    assert first.event == "APPROVAL_TRIGGERED"
    assert duplicate.event == "APPROVAL_ALREADY_PROCESSED"
    assert other_message.event == "APPROVAL_ALREADY_PROCESSED"
    assert len(clicker.calls) == 1
    assert store.get_by_nonce(NONCE)["telegram_message_id"] == 10


def test_edited_forwarded_reply_and_historical_messages_do_not_click(tmp_path) -> None:
    decider, _logger, _clock, store = open_decider(tmp_path / "state.sqlite", live=True)
    clicker = ClickRecorder()
    edited = decider.handle_message(
        incoming(edit_date=STARTED + timedelta(seconds=2)),
        clicker,
    )
    forwarded = decider.handle_message(incoming(message_id=11, forwarded=True), clicker)
    reply = decider.handle_message(incoming(message_id=12, reply=True), clicker)
    historical = decider.handle_message(incoming(message_id=13, sent_at=STARTED), clicker)
    assert edited.reason == "edited"
    assert forwarded.reason == "forwarded"
    assert reply.reason == "reply"
    assert historical.reason == "historical"
    assert clicker.calls == []
    assert store.get_by_nonce(NONCE) is None


def test_callback_timeout_and_ambiguous_result_are_not_retried(tmp_path) -> None:
    decider, _logger, _clock, store = open_decider(tmp_path / "timeout.sqlite", live=True)
    timeout = ClickRecorder(error=TimeoutError("callback timeout"))
    first = decider.handle_message(incoming(), timeout)
    second = decider.handle_message(incoming(), timeout)
    assert first.event == "APPROVAL_FAILED_OR_UNKNOWN"
    assert first.reason == "TimeoutError"
    assert second.event == "APPROVAL_ALREADY_PROCESSED"
    assert len(timeout.calls) == 1
    assert store.get_by_message(BOT_ID, 10)["status"] == "failed_or_unknown"

    ambiguous_store = tmp_path / "ambiguous.sqlite"
    decider, _logger, _clock, store = open_decider(ambiguous_store, live=True)
    ambiguous = ClickRecorder(result=ClickResult(False, "ambiguous_callback_result"))
    decision = decider.handle_message(incoming(message_id=20, nonce="Nonce22222222222"), ambiguous)
    assert decision.event == "APPROVAL_FAILED_OR_UNKNOWN"
    retry = decider.handle_message(incoming(message_id=20, nonce="Nonce22222222222"), ambiguous)
    assert retry.event == "APPROVAL_ALREADY_PROCESSED"
    assert len(ambiguous.calls) == 1
    assert store.get_by_message(BOT_ID, 20)["status"] == "failed_or_unknown"


def test_restart_after_reservation_does_not_click(tmp_path) -> None:
    path = tmp_path / "state.sqlite"
    decider, _logger, _clock, store = open_decider(path, live=True)
    prepared = decider.prepare(incoming())
    assert prepared.row_id > 0
    store.close()

    restarted, logger, _clock, store = open_decider(path, live=True)
    swept = restarted.startup()
    clicker = ClickRecorder()
    decision = restarted.handle_message(incoming(), clicker)
    assert swept == 1
    assert decision.event == "APPROVAL_ALREADY_PROCESSED"
    assert clicker.calls == []
    assert store.get_by_message(BOT_ID, 10)["status"] == "failed_or_unknown"
    assert (
        store.get_by_message(BOT_ID, 10)["failure_reason"]
        == "startup_found_unconfirmed_reservation"
    )
    assert "APPROVAL_FAILED_OR_UNKNOWN" in _events(logger)
    store.close()


def test_restart_with_already_processed_message_does_not_click(tmp_path) -> None:
    path = tmp_path / "state.sqlite"
    decider, _logger, _clock, store = open_decider(path, live=False)
    assert decider.handle_message(incoming()).event == "WOULD_APPROVE"
    store.close()
    restarted, _logger, _clock, store = open_decider(path, live=True)
    restarted.startup()
    clicker = ClickRecorder()
    decision = restarted.handle_message(incoming(), clicker)
    assert decision.event == "APPROVAL_ALREADY_PROCESSED"
    assert clicker.calls == []
    assert store.get_by_message(BOT_ID, 10)["status"] == "would_approve"
    store.close()


def test_listener_edit_is_recorded_and_cannot_trigger_approval(tmp_path) -> None:
    decider, logger, clock, store = open_decider(tmp_path / "state.sqlite", live=True)
    clicker = ClickRecorder()
    assert decider.handle_message(incoming(), clicker).event == "APPROVAL_TRIGGERED"
    edit = incoming(
        text=(
            "BP V3 trade APPROVED\n"
            "Intent: live-intent-123\n"
            "Decision time: 2026-09-27T12:00:05+00:00"
        ),
        buttons=(),
        edit_date=clock.current,
    )
    observed = decider.handle_edit(edit)
    assert observed.event == "LISTENER_EDIT_OBSERVED"
    row = store.get_by_message(BOT_ID, 10)
    assert row["intent_id"] == "live-intent-123"
    assert row["status"] == "clicked"
    assert len(clicker.calls) == 1
    unrelated = decider.handle_edit(
        incoming(
            message_id=99,
            text=(
                "BP V3 trade APPROVED\n"
                "Intent: x\n"
                "Decision time: 2026-09-27T12:00:05+00:00"
            ),
            buttons=(),
        )
    )
    assert unrelated.reason == "edit_without_reservation"
    assert store.get_by_message(BOT_ID, 99) is None
    conflict = incoming(
        text=(
            "BP V3 trade APPROVED\n"
            "Intent: other-intent\n"
            "Decision time: 2026-09-27T12:00:06+00:00"
        ),
        buttons=(),
    )
    assert decider.handle_edit(conflict).reason == "edit_intent_conflict"
    assert store.get_by_message(BOT_ID, 10)["intent_id"] == "live-intent-123"
    assert "LISTENER_EDIT_OBSERVED" in _events(logger)


def test_private_chat_and_outgoing_guards(tmp_path) -> None:
    decider, _logger, _clock, _store = open_decider(tmp_path / "state.sqlite", live=True)
    clicker = ClickRecorder()
    group = decider.handle_message(incoming(is_private=False), clicker)
    outgoing = decider.handle_message(incoming(message_id=11, outgoing=True), clicker)
    assert group.reason == "chat_not_private"
    assert outgoing.reason == "outgoing"
    assert clicker.calls == []
    assert keyboard()[0][0].text == "APPROVE"
