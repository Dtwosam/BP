from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from bp_telegram_auto_approver.contract import (
    CallbackButton,
    approval_deadline,
    classify_keyboard,
    parse_listener_approval_edit,
    parse_prompt,
    verify_approval_contract,
)
from bp_telegram_auto_approver.log import EventLogger
from bp_telegram_auto_approver.state import ApprovalStore, NewApproval

_FUTURE_SKEW = timedelta(seconds=30)


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True)
class IncomingMessage:
    message_id: int
    chat_id: int
    sender_id: int | None
    sender_username: str | None
    text: str | None
    sent_at: datetime
    edit_date: datetime | None
    forwarded: bool
    reply: bool
    outgoing: bool
    is_private: bool
    buttons: tuple[tuple[CallbackButton, ...], ...] | None


@dataclass(frozen=True)
class Decision:
    event: str
    reason: str | None = None
    status: str | None = None
    telegram_message_id: int | None = None
    nonce: str | None = None


@dataclass(frozen=True)
class PreparedClick:
    row_id: int
    telegram_chat_id: int
    telegram_message_id: int
    nonce: str
    callback_data: bytes


@dataclass(frozen=True)
class ClickResult:
    accepted: bool
    detail: str = ""


class IdentityMismatch(RuntimeError):
    pass


class Decider:
    def __init__(
        self,
        *,
        store: ApprovalStore,
        bot_user_id: int,
        bot_username: str,
        live: bool,
        started_at: datetime,
        clock: Clock,
        logger: EventLogger,
    ) -> None:
        if started_at.tzinfo is None or started_at.utcoffset() is None:
            raise ValueError("started_at must be timezone-aware")
        self._store = store
        self._bot_user_id = int(bot_user_id)
        self._bot_username = bot_username.lower()
        self._live = live
        self._started_at = started_at.astimezone(UTC)
        self._clock = clock
        self._logger = logger
        self._identity_ok = False

    @property
    def store(self) -> ApprovalStore:
        return self._store

    def confirm_identity(self, *, user_id: int, username: str | None, is_bot: bool) -> None:
        resolved = (username or "").lstrip("@").strip().lower()
        matches = (
            is_bot
            and int(user_id) == self._bot_user_id
            and resolved == self._bot_username
        )
        self._identity_ok = matches
        if not matches:
            self._logger.emit(
                "BOT_IDENTITY_MISMATCH",
                resolved_user_id=int(user_id),
                resolved_username=resolved,
                expected_user_id=self._bot_user_id,
                expected_username=self._bot_username,
                is_bot=bool(is_bot),
            )
            raise IdentityMismatch("resolved bot identity does not match configuration")
        self._logger.emit(
            "BOT_IDENTITY_CONFIRMED",
            bot_user_id=self._bot_user_id,
            bot_username=self._bot_username,
        )

    def startup(self) -> int:
        swept = self._store.sweep_unconfirmed(self._now().isoformat())
        if swept:
            self._logger.emit(
                "APPROVAL_FAILED_OR_UNKNOWN",
                reason="startup_found_unconfirmed_reservation",
                swept=swept,
            )
        return swept

    def prepare(self, message: IncomingMessage) -> Decision | PreparedClick:
        verify_approval_contract()
        received = self._decision_base(message)
        self._logger.emit("APPROVAL_MESSAGE_RECEIVED", **received)
        if message.message_id <= 0:
            return self._reject(message, "APPROVAL_REJECTED", "message_id_invalid")
        if not self._identity_ok:
            return self._reject(message, "BOT_IDENTITY_MISMATCH", "identity_not_confirmed")
        identity = self._identity_rejection(message)
        if identity is not None:
            return identity
        if message.forwarded:
            return self._reject(message, "APPROVAL_REJECTED", "forwarded")
        if message.reply:
            return self._reject(message, "APPROVAL_REJECTED", "reply")
        if message.edit_date is not None:
            return self._reject(message, "APPROVAL_REJECTED", "edited")
        if message.sent_at.tzinfo is None or message.sent_at.utcoffset() is None:
            return self._reject(message, "APPROVAL_REJECTED", "message_time_unverifiable")
        sent_at = message.sent_at.astimezone(UTC)
        if sent_at <= self._started_at:
            return self._reject(message, "APPROVAL_REJECTED", "historical")
        now = self._now()
        if sent_at > now + _FUTURE_SKEW:
            return self._reject(message, "APPROVAL_REJECTED", "message_time_unverifiable")
        prompt = parse_prompt(message.text)
        if prompt is None:
            return self._reject(message, "UNEXPECTED_MESSAGE_FORMAT", "prompt_mismatch")
        nonce, keyboard_reason = classify_keyboard(message.buttons)
        if nonce is None:
            return self._reject(message, "UNEXPECTED_MESSAGE_FORMAT", keyboard_reason)
        try:
            deadline = approval_deadline(sent_at, prompt.time_remaining)
        except ValueError:
            return self._reject(message, "APPROVAL_EXPIRED", "expired")
        validated = {
            **received,
            "nonce": nonce,
            "intent_id": None,
            "prediction_id": None,
            "request_sha256": None,
            "market": None,
            "selected_side": prompt.side,
            "limit_price": prompt.limit_price,
            "shares": prompt.shares,
            "maximum_spend": prompt.maximum_spend,
            "time_remaining_seconds": format(prompt.time_remaining, "f"),
            "deadline_at": deadline.isoformat(),
        }
        self._logger.emit("APPROVAL_VALIDATED", **validated)
        record = NewApproval(
            telegram_chat_id=message.chat_id,
            telegram_message_id=message.message_id,
            nonce=nonce,
            status="rejected",
            reason=None,
            side=prompt.side,
            limit_price=prompt.limit_price,
            shares=prompt.shares,
            maximum_spend=prompt.maximum_spend,
            time_remaining_seconds=format(prompt.time_remaining, "f"),
            deadline_at=deadline.isoformat(),
            received_at=now.isoformat(),
            approval_at=None,
            result="rejected",
            failure_reason=None,
        )
        if now >= deadline:
            return self._persist_rejection(message, record, "APPROVAL_EXPIRED", "expired")
        if self._live:
            reserved = _replace(record, status="reserved", result="reserved", reason=None)
            inserted = self._store.insert(reserved)
            if not inserted.created or inserted.row_id is None:
                return self._duplicate(message, nonce)
            self._logger.emit(
                "APPROVAL_RESERVED",
                telegram_message_id=message.message_id,
                telegram_chat_id=message.chat_id,
                nonce=nonce,
            )
            source = _approve_bytes(nonce)
            return PreparedClick(
                row_id=inserted.row_id,
                telegram_chat_id=message.chat_id,
                telegram_message_id=message.message_id,
                nonce=nonce,
                callback_data=source,
            )
        would = _replace(
            record,
            status="would_approve",
            result="would_approve",
            approval_at=now.isoformat(),
            reason=None,
            failure_reason=None,
        )
        inserted = self._store.insert(would)
        if not inserted.created:
            return self._duplicate(message, nonce)
        self._logger.emit("WOULD_APPROVE", **validated)
        return Decision(
            "WOULD_APPROVE",
            status="would_approve",
            telegram_message_id=message.message_id,
            nonce=nonce,
        )

    def finish(self, row_id: int, result: ClickResult) -> Decision:
        now = self._now().isoformat()
        if result.accepted:
            changed = self._store.transition_reserved(
                row_id,
                "clicked",
                updated_at=now,
                approval_at=now,
                result=result.detail or "clicked",
                failure_reason=None,
            )
            if not changed:
                return Decision("APPROVAL_ALREADY_PROCESSED", reason="reservation_not_open")
            self._logger.emit("APPROVAL_TRIGGERED", row_id=row_id, result=result.detail)
            return Decision("APPROVAL_TRIGGERED", status="clicked")
        detail = result.detail or "ambiguous_callback_result"
        changed = self._store.transition_reserved(
            row_id,
            "failed_or_unknown",
            updated_at=now,
            approval_at=None,
            result="failed_or_unknown",
            failure_reason=detail,
        )
        if not changed:
            return Decision("APPROVAL_ALREADY_PROCESSED", reason="reservation_not_open")
        self._logger.emit(
            "APPROVAL_FAILED_OR_UNKNOWN",
            row_id=row_id,
            reason=detail,
        )
        return Decision("APPROVAL_FAILED_OR_UNKNOWN", status="failed_or_unknown", reason=detail)

    def handle_message(
        self,
        message: IncomingMessage,
        clicker: Callable[[int, int, bytes], ClickResult] | None = None,
    ) -> Decision:
        outcome = self.prepare(message)
        if not isinstance(outcome, PreparedClick):
            return outcome
        if clicker is None:
            return self.finish(outcome.row_id, ClickResult(False, "clicker_missing"))
        try:
            result = clicker(
                outcome.telegram_chat_id,
                outcome.telegram_message_id,
                outcome.callback_data,
            )
        except Exception as exc:
            return self.finish(outcome.row_id, ClickResult(False, type(exc).__name__))
        if not isinstance(result, ClickResult):
            return self.finish(outcome.row_id, ClickResult(False, "invalid_click_result"))
        return self.finish(outcome.row_id, result)

    def handle_edit(self, message: IncomingMessage) -> Decision:
        verify_approval_contract()
        if not self._identity_ok:
            return self._reject(message, "BOT_IDENTITY_MISMATCH", "identity_not_confirmed")
        identity = self._identity_rejection(message)
        if identity is not None:
            return identity
        if message.forwarded or message.reply or message.outgoing:
            return self._reject(message, "APPROVAL_REJECTED", "edit_not_direct")
        parsed = parse_listener_approval_edit(message.text)
        if parsed is None:
            return self._reject(message, "APPROVAL_REJECTED", "edit_ignored")
        outcome = self._store.record_listener_edit(
            telegram_chat_id=message.chat_id,
            telegram_message_id=message.message_id,
            intent_id=parsed.intent_id,
            listener_decision_at=parsed.decision_at.isoformat(),
            observed_at=self._now().isoformat(),
        )
        if outcome == "missing":
            return self._reject(message, "APPROVAL_REJECTED", "edit_without_reservation")
        if outcome == "conflict":
            return self._reject(message, "APPROVAL_REJECTED", "edit_intent_conflict")
        self._logger.emit(
            "LISTENER_EDIT_OBSERVED",
            telegram_message_id=message.message_id,
            telegram_chat_id=message.chat_id,
            intent_id=parsed.intent_id,
            listener_decision_at=parsed.decision_at.isoformat(),
        )
        return Decision(
            "LISTENER_EDIT_OBSERVED",
            telegram_message_id=message.message_id,
        )

    def _identity_rejection(self, message: IncomingMessage) -> Decision | None:
        if message.outgoing:
            return self._reject(message, "APPROVAL_REJECTED", "outgoing")
        if message.sender_id != self._bot_user_id:
            return self._reject(message, "UNEXPECTED_SENDER", "sender_mismatch")
        if message.chat_id != self._bot_user_id:
            return self._reject(message, "UNEXPECTED_SENDER", "chat_mismatch")
        if not message.is_private:
            return self._reject(message, "UNEXPECTED_SENDER", "chat_not_private")
        username = (message.sender_username or "").lstrip("@").strip().lower()
        if username and username != self._bot_username:
            return self._reject(message, "UNEXPECTED_SENDER", "sender_username_mismatch")
        return None

    def _persist_rejection(
        self,
        message: IncomingMessage,
        record: NewApproval,
        event: str,
        reason: str,
    ) -> Decision:
        rejected = _replace(record, reason=reason, failure_reason=reason, result="rejected")
        inserted = self._store.insert(rejected)
        if not inserted.created:
            return self._duplicate(message, record.nonce)
        self._logger.emit(
            event,
            telegram_message_id=message.message_id,
            telegram_chat_id=message.chat_id,
            nonce=record.nonce,
            reason=reason,
        )
        return Decision(
            event,
            reason=reason,
            status="rejected",
            telegram_message_id=message.message_id,
            nonce=record.nonce,
        )

    def _duplicate(self, message: IncomingMessage, nonce: str) -> Decision:
        self._logger.emit(
            "APPROVAL_ALREADY_PROCESSED",
            telegram_message_id=message.message_id,
            telegram_chat_id=message.chat_id,
            nonce=nonce,
        )
        return Decision(
            "APPROVAL_ALREADY_PROCESSED",
            reason="duplicate",
            telegram_message_id=message.message_id,
            nonce=nonce,
        )

    def _reject(self, message: IncomingMessage, event: str, reason: str) -> Decision:
        self._logger.emit(
            event,
            telegram_message_id=message.message_id,
            telegram_chat_id=message.chat_id,
            sender_id=message.sender_id,
            reason=reason,
        )
        return Decision(
            event,
            reason=reason,
            telegram_message_id=message.message_id,
        )

    def _decision_base(self, message: IncomingMessage) -> dict[str, object]:
        return {
            "telegram_message_id": message.message_id,
            "telegram_chat_id": message.chat_id,
            "sender_id": message.sender_id,
        }

    def _now(self) -> datetime:
        current = self._clock.now()
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("clock must return timezone-aware datetimes")
        return current.astimezone(UTC)


def _approve_bytes(nonce: str) -> bytes:
    from bp_telegram_auto_approver.contract import approval_source, is_exact_approve_callback

    data = approval_source().callback_data("approve", nonce).encode("ascii")
    if not is_exact_approve_callback(data, nonce):
        raise RuntimeError("refusing to prepare a non-approve callback")
    return data


def _replace(record: NewApproval, **changes: object) -> NewApproval:
    values = record.__dict__.copy()
    values.update(changes)
    return NewApproval(**values)
