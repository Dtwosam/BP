from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bp_telegram_auto_approver.contract import CallbackButton, approval_source
from bp_telegram_auto_approver.decider import ClickResult, Decider, IncomingMessage
from bp_telegram_auto_approver.log import ListLogger
from bp_telegram_auto_approver.state import ApprovalStore

BOT_ID = 424242
BOT_USERNAME = "bp_approval_bot"
STARTED = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
NONCE = "Abcdefghijklmnop"


class FakeClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current


class ClickRecorder:
    def __init__(self, result: ClickResult | None = None, error: Exception | None = None) -> None:
        self.calls: list[tuple[int, int, bytes]] = []
        self.result = result if result is not None else ClickResult(True, "rpc_returned")
        self.error = error

    def __call__(self, chat_id: int, message_id: int, data: bytes) -> ClickResult:
        self.calls.append((chat_id, message_id, data))
        if self.error is not None:
            raise self.error
        return self.result


def prepared(observed_at: datetime, *, seconds: int = 50, side: str = "down") -> dict[str, object]:
    return {
        "status": "prepared",
        "action": "submit",
        "intent_id": "live-intent-123",
        "prediction_id": "prediction-123",
        "paper_order_id": "paper-123",
        "market_end_at": (observed_at + timedelta(seconds=seconds)).isoformat(),
        "request": {
            "selected_side": side,
            "limit_price": "0.72",
            "requested_shares": "6.81",
            "target_notional_usd": "5",
            "token_id": "token-123",
            "action": "BUY",
        },
        "policy": {
            "policy_version": "v3-live-canary-v1",
            "max_submission_attempts": 1,
        },
    }


def prompt_text(observed_at: datetime, *, seconds: int = 50, side: str = "down") -> str:
    return approval_source().build_prompt(
        prepared(observed_at, seconds=seconds, side=side),
        observed_at=observed_at,
    )


def candidate_prompt_text(
    observed_at: datetime,
    *,
    seconds: int = 50,
    side: str = "down",
) -> str:
    return approval_source().build_candidate_prompt(
        prepared(observed_at, seconds=seconds, side=side),
        observed_at=observed_at,
    )


def keyboard(nonce: str = NONCE) -> tuple[tuple[CallbackButton, ...], ...]:
    source = approval_source()
    return (
        (
            CallbackButton("APPROVE", source.callback_data("approve", nonce).encode("ascii")),
            CallbackButton("SKIP", source.callback_data("skip", nonce).encode("ascii")),
        ),
    )


def incoming(
    *,
    message_id: int = 10,
    chat_id: int = BOT_ID,
    sender_id: int | None = BOT_ID,
    sender_username: str | None = BOT_USERNAME,
    text: str | None = None,
    sent_at: datetime | None = None,
    edit_date: datetime | None = None,
    forwarded: bool = False,
    reply: bool = False,
    outgoing: bool = False,
    is_private: bool = True,
    buttons: tuple[tuple[CallbackButton, ...], ...] | None = None,
    nonce: str = NONCE,
    seconds: int = 50,
) -> IncomingMessage:
    observed = sent_at or (STARTED + timedelta(seconds=1))
    return IncomingMessage(
        message_id=message_id,
        chat_id=chat_id,
        sender_id=sender_id,
        sender_username=sender_username,
        text=prompt_text(observed, seconds=seconds) if text is None else text,
        sent_at=observed,
        edit_date=edit_date,
        forwarded=forwarded,
        reply=reply,
        outgoing=outgoing,
        is_private=is_private,
        buttons=keyboard(nonce) if buttons is None else buttons,
    )


def open_decider(
    path,
    *,
    live: bool = False,
    now: datetime | None = None,
    started_at: datetime = STARTED,
    confirm: bool = True,
    bot_user_id: int = BOT_ID,
    bot_username: str = BOT_USERNAME,
):
    store = ApprovalStore(path)
    logger = ListLogger()
    clock = FakeClock(now or (started_at + timedelta(seconds=2)))
    decider = Decider(
        store=store,
        bot_user_id=bot_user_id,
        bot_username=bot_username,
        live=live,
        started_at=started_at,
        clock=clock,
        logger=logger,
    )
    if confirm:
        decider.confirm_identity(user_id=bot_user_id, username=bot_username, is_bot=True)
    return decider, logger, clock, store
