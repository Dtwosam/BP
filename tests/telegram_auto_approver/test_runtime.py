from __future__ import annotations

from datetime import UTC, datetime

import pytest
from bp_telegram_auto_approver.runtime import (
    HistoryScanRefused,
    adapt_message,
    buttons_from_markup,
    only_latest,
    operator_matches,
    recovery_action,
    should_consume_latest,
)

from tests.telegram_auto_approver.support import BOT_ID, ClickRecorder, incoming, open_decider


class _Button:
    def __init__(self, text: str, data: bytes | None = None, url: str | None = None) -> None:
        self.text = text
        self.data = data
        self.url = url


class _Row:
    def __init__(self, buttons: list[_Button]) -> None:
        self.buttons = buttons


class _Markup:
    def __init__(self, rows: list[_Row]) -> None:
        self.rows = rows


class _Sender:
    username = "bp_approval_bot"


class _Message:
    id = 15
    sender_id = 424242
    sender = _Sender()
    message = "BP V3 LIVE TRADE READY"
    date = datetime(2026, 9, 27, 12, 0, 1, tzinfo=UTC)
    edit_date = None
    fwd_from = None
    reply_to = None
    out = False
    reply_markup = _Markup(
        [
            _Row(
                [
                    _Button("APPROVE", b"approve:Abcdefghijklmnop"),
                    _Button("SKIP", b"skip:Abcdefghijklmnop"),
                ]
            )
        ]
    )


def test_unobserved_gap_reconciles_a_new_prompt_once(tmp_path) -> None:
    was_connected = True
    reasons: list[str | None] = []
    for connected in (True, True, True):
        reason, disconnected = recovery_action(
            connected=connected,
            was_connected=was_connected,
        )
        assert disconnected is False
        reasons.append(reason)
        was_connected = connected
    assert reasons == ["connected_reconcile", "connected_reconcile", "connected_reconcile"]

    observed_gap, disconnected = recovery_action(connected=False, was_connected=True)
    assert observed_gap is None
    assert disconnected is True
    resumed, disconnected = recovery_action(connected=True, was_connected=False)
    assert resumed == "reconnect"
    assert disconnected is False

    previous_id = 10
    fresh = incoming(message_id=11)
    assert should_consume_latest(
        previous_id=previous_id,
        latest_id=fresh.message_id,
        force=False,
    )
    decider, _logger, _clock, store = open_decider(tmp_path / "state.sqlite", live=True)
    clicker = ClickRecorder()
    first = decider.handle_message(fresh, clicker)
    assert first.event == "APPROVAL_TRIGGERED"
    assert len(clicker.calls) == 1

    assert not should_consume_latest(
        previous_id=fresh.message_id,
        latest_id=fresh.message_id,
        force=False,
    )
    assert should_consume_latest(
        previous_id=fresh.message_id,
        latest_id=fresh.message_id,
        force=True,
    )
    second = decider.handle_message(fresh, clicker)
    assert second.event == "APPROVAL_ALREADY_PROCESSED"
    assert len(clicker.calls) == 1
    assert store.get_by_message(BOT_ID, 11)["status"] == "clicked"
    with pytest.raises(HistoryScanRefused):
        only_latest([fresh, fresh])


def test_only_latest_refuses_a_history_batch() -> None:
    assert only_latest(None) is None
    assert only_latest([]) is None
    assert only_latest(["one"]) == "one"
    assert only_latest("one") == "one"
    with pytest.raises(HistoryScanRefused):
        only_latest(["old", "new"])


def test_adapt_message_reads_private_callback_keyboard() -> None:
    adapted = adapt_message(_Message(), chat_id=424242, is_private=True)
    assert adapted.message_id == 15
    assert adapted.sender_username == "bp_approval_bot"
    assert adapted.forwarded is False
    assert adapted.buttons is not None
    assert adapted.buttons[0][0].callback_data == b"approve:Abcdefghijklmnop"
    assert buttons_from_markup(object()) is None
    assert operator_matches(user_id=7, is_bot=False, expected_user_id=7)
    assert not operator_matches(user_id=7, is_bot=True, expected_user_id=7)
    assert not operator_matches(user_id=8, is_bot=False, expected_user_id=7)


def test_forwarded_and_edited_flags_are_preserved() -> None:
    message = _Message()
    message.fwd_from = object()
    message.edit_date = datetime(2026, 9, 27, 12, 0, 2, tzinfo=UTC)
    message.reply_to = object()
    message.out = True
    adapted = adapt_message(message, chat_id=424242, is_private=False)
    assert adapted.forwarded is True
    assert adapted.edit_date == message.edit_date
    assert adapted.reply is True
    assert adapted.outgoing is True
    assert adapted.is_private is False
