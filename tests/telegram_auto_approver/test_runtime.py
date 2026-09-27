from __future__ import annotations

from datetime import UTC, datetime

import pytest
from bp_telegram_auto_approver.runtime import (
    HistoryScanRefused,
    adapt_message,
    buttons_from_markup,
    only_latest,
    operator_matches,
)


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
