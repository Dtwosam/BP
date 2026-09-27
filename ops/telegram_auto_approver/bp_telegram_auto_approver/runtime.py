from __future__ import annotations

import asyncio
import os
import signal
import sys
from datetime import UTC, datetime

from bp_telegram_auto_approver.config import ServiceConfig
from bp_telegram_auto_approver.contract import (
    APPROVAL_CONTRACT_BLOB_SHA,
    CallbackButton,
    ContractMismatch,
    is_exact_approve_callback,
    verify_approval_contract,
)
from bp_telegram_auto_approver.decider import (
    ClickResult,
    Decider,
    IdentityMismatch,
    IncomingMessage,
    PreparedClick,
    SystemClock,
)
from bp_telegram_auto_approver.log import JsonLogger
from bp_telegram_auto_approver.state import ApprovalStore


class HistoryScanRefused(RuntimeError):
    pass


def recovery_action(*, connected: bool, was_connected: bool) -> tuple[str | None, bool]:
    """Decide whether to reconcile the single latest message.

    A connected sample always reconciles. A disconnect that starts and ends
    between polls never clears ``was_connected``, so the next connected sample
    must still look at the latest message. Returns ``(reason, disconnected_edge)``.
    """
    if not connected:
        return None, was_connected
    if not was_connected:
        return "reconnect", False
    return "connected_reconcile", False


def should_consume_latest(*, previous_id: int | None, latest_id: int | None, force: bool) -> bool:
    """Consume a newly seen latest message. Never walk older history."""
    if not isinstance(latest_id, int) or latest_id <= 0:
        return False
    if force:
        return True
    return latest_id != previous_id


def only_latest(found: object) -> object | None:
    """Accept one reconnect candidate. Refuse a history batch."""
    if found is None:
        return None
    if isinstance(found, list):
        if len(found) > 1:
            raise HistoryScanRefused("refusing to scan more than the latest message")
        if not found:
            return None
        return found[0]
    return found


def operator_matches(*, user_id: int, is_bot: bool, expected_user_id: int) -> bool:
    return (not is_bot) and int(user_id) == int(expected_user_id)


def adapt_message(message: object, *, chat_id: int, is_private: bool) -> IncomingMessage:
    sender = getattr(message, "sender", None)
    username = getattr(sender, "username", None) if sender is not None else None
    if username is not None and not isinstance(username, str):
        username = None
    text = getattr(message, "message", None)
    if not isinstance(text, str):
        raw_text = getattr(message, "text", None)
        text = raw_text if isinstance(raw_text, str) else None
    sent_at = getattr(message, "date", None)
    if not isinstance(sent_at, datetime):
        sent_at = datetime.min
    edit_raw = getattr(message, "edit_date", None)
    if edit_raw is None:
        edit_date = None
    elif isinstance(edit_raw, datetime):
        edit_date = edit_raw
    else:
        edit_date = datetime.now(UTC)
    message_id = getattr(message, "id", None)
    sender_id = getattr(message, "sender_id", None)
    return IncomingMessage(
        message_id=message_id if isinstance(message_id, int) else -1,
        chat_id=chat_id,
        sender_id=sender_id if isinstance(sender_id, int) else None,
        sender_username=username,
        text=text,
        sent_at=sent_at,
        edit_date=edit_date,
        forwarded=bool(
            getattr(message, "fwd_from", None) or getattr(message, "forward", None)
        ),
        reply=bool(getattr(message, "reply_to", None) or getattr(message, "is_reply", False)),
        outgoing=bool(getattr(message, "out", False)),
        is_private=is_private,
        buttons=buttons_from_markup(getattr(message, "reply_markup", None)),
    )


def buttons_from_markup(
    markup: object,
) -> tuple[tuple[CallbackButton, ...], ...] | None:
    if markup is None:
        return ()
    rows = getattr(markup, "rows", None)
    if rows is None:
        return None
    parsed: list[tuple[CallbackButton, ...]] = []
    for row in rows:
        raw_buttons = getattr(row, "buttons", None)
        if raw_buttons is None:
            return None
        parsed_row: list[CallbackButton] = []
        for button in raw_buttons:
            text = getattr(button, "text", None)
            if not isinstance(text, str):
                return None
            data = getattr(button, "data", None)
            url = getattr(button, "url", None)
            if data is not None and not isinstance(data, (bytes, bytearray)):
                return None
            if url is not None and not isinstance(url, str):
                return None
            parsed_row.append(
                CallbackButton(
                    text=text,
                    callback_data=bytes(data) if data is not None else None,
                    url=url,
                )
            )
        parsed.append(tuple(parsed_row))
    return tuple(parsed)


async def serve(config: ServiceConfig) -> int:
    logger = JsonLogger()
    try:
        verify_approval_contract()
    except ContractMismatch as exc:
        logger.emit(
            "APPROVAL_CONTRACT_MISMATCH",
            expected_blob=APPROVAL_CONTRACT_BLOB_SHA,
            actual_blob=exc.actual_blob,
        )
        return 2

    from telethon import TelegramClient, events
    from telethon.tl.functions.messages import GetBotCallbackAnswerRequest

    os.umask(0o077)
    started_at = datetime.now(UTC)
    store = ApprovalStore(config.state_path)
    decider = Decider(
        store=store,
        bot_user_id=config.bot_user_id,
        bot_username=config.bot_username,
        live=config.live,
        started_at=started_at,
        clock=SystemClock(),
        logger=logger,
    )
    swept = decider.startup()
    stop = asyncio.Event()
    client = TelegramClient(
        str(config.session_path),
        config.api_id,
        config.api_hash,
        auto_reconnect=True,
        connection_retries=None,
        retry_delay=2,
    )
    try:
        await client.connect()
        _tighten_session(config.session_path)
        if not await client.is_user_authorized():
            if not sys.stdin.isatty():
                logger.emit("SESSION_NOT_AUTHORIZED")
                return 2
            await client.start()
            _tighten_session(config.session_path)
            if not await client.is_user_authorized():
                logger.emit("SESSION_NOT_AUTHORIZED")
                return 2
        me = await client.get_me()
        if not operator_matches(
            user_id=int(me.id),
            is_bot=bool(getattr(me, "bot", False)),
            expected_user_id=config.operator_user_id,
        ):
            logger.emit(
                "OPERATOR_IDENTITY_MISMATCH",
                resolved_user_id=int(me.id),
                expected_user_id=config.operator_user_id,
                is_bot=bool(getattr(me, "bot", False)),
            )
            return 2
        entity = await client.get_entity(config.bot_username)
        try:
            decider.confirm_identity(
                user_id=int(entity.id),
                username=getattr(entity, "username", None),
                is_bot=bool(getattr(entity, "bot", False)),
            )
        except IdentityMismatch:
            return 2
        bot_id = int(entity.id)
        lock = asyncio.Lock()

        async def click(prepared: PreparedClick) -> None:
            if not is_exact_approve_callback(prepared.callback_data, prepared.nonce):
                decider.finish(
                    prepared.row_id,
                    ClickResult(False, "refusing_unexpected_callback"),
                )
                return
            try:
                answer = await asyncio.wait_for(
                    client(
                        GetBotCallbackAnswerRequest(
                            peer=bot_id,
                            msg_id=prepared.telegram_message_id,
                            data=prepared.callback_data,
                        )
                    ),
                    timeout=15,
                )
            except Exception as exc:
                decider.finish(prepared.row_id, ClickResult(False, type(exc).__name__))
                return
            if answer is None:
                decider.finish(
                    prepared.row_id,
                    ClickResult(False, "missing_callback_result"),
                )
                return
            detail = "rpc_returned"
            toast = getattr(answer, "message", None)
            if isinstance(toast, str) and toast and len(toast) <= 80 and "\n" not in toast:
                detail = f"rpc_returned:{toast}"
            decider.finish(prepared.row_id, ClickResult(True, detail))

        async def consume(adapted: IncomingMessage, *, edited: bool) -> None:
            async with lock:
                if edited or adapted.edit_date is not None:
                    decider.handle_edit(adapted)
                    return
                outcome = decider.prepare(adapted)
                if isinstance(outcome, PreparedClick):
                    await click(outcome)

        async def on_new(event: object) -> None:
            await consume(
                adapt_message(
                    event.message,
                    chat_id=bot_id,
                    is_private=bool(getattr(event, "is_private", False)),
                ),
                edited=False,
            )

        async def on_edit(event: object) -> None:
            await consume(
                adapt_message(
                    event.message,
                    chat_id=bot_id,
                    is_private=bool(getattr(event, "is_private", False)),
                ),
                edited=True,
            )

        client.add_event_handler(on_new, events.NewMessage(chats=bot_id, incoming=True))
        client.add_event_handler(on_edit, events.MessageEdited(chats=bot_id))

        last_reconciled_id: int | None = None

        async def consider_latest(reason: str, *, force: bool) -> None:
            nonlocal last_reconciled_id
            try:
                found = await client.get_messages(bot_id, limit=1)
                latest = only_latest(found)
            except Exception as exc:
                logger.emit(
                    "LATEST_MESSAGE_CHECK_FAILED",
                    reason=reason,
                    error_type=type(exc).__name__,
                )
                return
            latest_id = getattr(latest, "id", None) if latest is not None else None
            if not should_consume_latest(
                previous_id=last_reconciled_id,
                latest_id=latest_id if isinstance(latest_id, int) else None,
                force=force,
            ):
                return
            logger.emit("LATEST_MESSAGE_CHECKED", reason=reason, telegram_message_id=latest_id)
            await consume(
                adapt_message(latest, chat_id=bot_id, is_private=True),
                edited=False,
            )
            if isinstance(latest_id, int):
                last_reconciled_id = latest_id

        await consider_latest("startup", force=True)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError):
                pass
        logger.emit(
            "SERVICE_STARTED",
            mode="live-auto-approve" if config.live else "dry-run",
            bot_user_id=config.bot_user_id,
            bot_username=config.bot_username,
            swept_unconfirmed=swept,
        )
        was_connected = True
        while not stop.is_set():
            connected = client.is_connected()
            reason, disconnected = recovery_action(
                connected=connected,
                was_connected=was_connected,
            )
            if disconnected:
                logger.emit("TELEGRAM_DISCONNECTED")
            if reason == "reconnect":
                logger.emit("TELEGRAM_RECONNECTED")
                await consider_latest(reason, force=True)
            elif reason == "connected_reconcile":
                await consider_latest(reason, force=False)
            was_connected = connected
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except TimeoutError:
                continue
        logger.emit("SERVICE_STOPPED")
        return 0
    finally:
        store.close()
        _tighten_session(config.session_path)
        try:
            await client.disconnect()
        except Exception:
            pass


def _tighten_session(path: object) -> None:
    session = path if isinstance(path, os.PathLike) else None
    if session is None:
        return
    raw = os.fspath(session)
    for candidate in (raw, f"{raw}-journal"):
        if os.path.exists(candidate):
            os.chmod(candidate, 0o600)
