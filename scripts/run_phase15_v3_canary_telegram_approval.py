from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from bp_engine.execution.telegram_approval import (
    ApprovalError,
    approval_record,
    build_prompt,
    callback_data,
    new_pending,
    validate_callback,
    validate_prepared,
)

API_ROOT = "https://api.telegram.org"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _api(token: str, method: str, payload: dict[str, Any], *, timeout: int = 20) -> dict[str, Any]:
    form: dict[str, object] = {}
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            form[key] = json.dumps(value, separators=(",", ":"))
        else:
            form[key] = value
    request = Request(
        f"{API_ROOT}/bot{token}/{method}",
        data=urlencode(form).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    if body.get("ok") is not True:
        raise RuntimeError(f"telegram_{method}_failed")
    return body


def _send_prompt(
    *,
    token: str,
    chat_id: int,
    prepared: dict[str, Any],
    pending: dict[str, Any],
) -> int:
    markup = {
        "inline_keyboard": [
            [
                {
                    "text": "APPROVE",
                    "callback_data": callback_data("approve", str(pending["nonce"])),
                },
                {
                    "text": "SKIP",
                    "callback_data": callback_data("skip", str(pending["nonce"])),
                },
            ]
        ]
    }
    response = _api(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": build_prompt(prepared, observed_at=_utc_now()),
            "reply_markup": markup,
            "disable_notification": False,
        },
    )
    result = response.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("message_id"), int):
        raise RuntimeError("telegram_sendMessage_result_missing")
    return int(result["message_id"])


def _answer_callback(token: str, callback_query_id: str, text: str) -> None:
    _api(
        token,
        "answerCallbackQuery",
        {"callback_query_id": callback_query_id, "text": text, "show_alert": False},
    )


def _edit_result(token: str, *, chat_id: int, message_id: int, text: str) -> None:
    _api(
        token,
        "editMessageText",
        {"chat_id": chat_id, "message_id": message_id, "text": text},
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--prepare-state-root",
        default="/var/lib/bp/phase15-canary-prepare-watch",
    )
    parser.add_argument(
        "--approval-state-root",
        default="/var/lib/bp/phase15-canary-telegram-approval",
    )
    parser.add_argument("--poll-seconds", type=float, default=0.25)
    return parser.parse_args()


def _current_run(state_root: Path) -> Path | None:
    current = state_root / "current-run"
    if not current.is_file():
        return None
    raw = current.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    run_dir = Path(raw)
    runs_root = (state_root / "runs").resolve()
    try:
        run_dir.resolve().relative_to(runs_root)
    except ValueError as exc:
        raise RuntimeError("current run path escapes prepare state root") from exc
    return run_dir


def _expiry_record(pending: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "expired",
        "intent_id": str(pending["intent_id"]),
        "prediction_id": str(pending["prediction_id"]),
        "paper_order_id": str(pending["paper_order_id"]),
        "request_sha256": str(pending["request_sha256"]),
        "updated_at": _utc_now().isoformat(),
    }


def _wait_for_decision(
    *,
    token: str,
    prepared_path: Path,
    pending: dict[str, Any],
    message_id: int,
    state_dir: Path,
) -> dict[str, Any]:
    offset = 0
    while True:
        try:
            updates = _api(
                token,
                "getUpdates",
                {"offset": offset, "timeout": 10, "allowed_updates": ["callback_query"]},
                timeout=15,
            ).get("result", [])
        except Exception as exc:
            _atomic_json(
                state_dir / "status.json",
                {
                    "status": "telegram_poll_error",
                    "intent_id": pending["intent_id"],
                    "error_type": type(exc).__name__,
                    "updated_at": _utc_now().isoformat(),
                    "real_order_submitted": False,
                },
            )
            time.sleep(1)
            continue

        if not isinstance(updates, list):
            updates = []
        for update in updates:
            if not isinstance(update, dict):
                continue
            update_id = int(update.get("update_id", -1))
            if update_id >= 0:
                offset = max(offset, update_id + 1)
            callback = update.get("callback_query")
            if not isinstance(callback, dict):
                continue
            callback_query_id = str(callback.get("id") or "")
            try:
                action = validate_callback(update, pending=pending, observed_at=_utc_now())
            except ApprovalError:
                if callback_query_id:
                    try:
                        _answer_callback(token, callback_query_id, "This approval is not valid.")
                    except Exception:
                        pass
                continue

            record = approval_record(
                action=action,
                pending=pending,
                callback_query_id=callback_query_id,
                approved_at=_utc_now(),
            )
            _atomic_json(state_dir / "approval.json", record)
            if callback_query_id:
                try:
                    _answer_callback(
                        token,
                        callback_query_id,
                        "Approved." if action == "approve" else "Skipped.",
                    )
                except Exception:
                    pass
            try:
                _edit_result(
                    token,
                    chat_id=int(pending["telegram_chat_id"]),
                    message_id=message_id,
                    text=(
                        f"BP V3 trade {record['status'].upper()}\n"
                        f"Intent: {record['intent_id']}\n"
                        f"Decision time: {record['approved_at']}"
                    ),
                )
            except Exception:
                pass
            return record

        try:
            prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
            validate_prepared(prepared, observed_at=_utc_now(), minimum_seconds_remaining=10)
        except Exception:
            record = _expiry_record(pending)
            _atomic_json(state_dir / "approval.json", record)
            try:
                _edit_result(
                    token,
                    chat_id=int(pending["telegram_chat_id"]),
                    message_id=message_id,
                    text=f"BP V3 trade EXPIRED\nIntent: {pending['intent_id']}",
                )
            except Exception:
                pass
            return record


def main() -> int:
    args = _parse_args()
    if not 0.1 <= args.poll_seconds <= 5:
        raise SystemExit("poll seconds must be within 0.1..5")

    token = os.environ.get("BP_TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BP_TELEGRAM_BOT_TOKEN is required")
    try:
        user_id = int(os.environ["BP_TELEGRAM_USER_ID"])
        chat_id = int(os.environ["BP_TELEGRAM_CHAT_ID"])
    except (KeyError, ValueError) as exc:
        raise SystemExit(
            "BP_TELEGRAM_USER_ID and BP_TELEGRAM_CHAT_ID are required integers"
        ) from exc

    prepare_root = Path(args.prepare_state_root)
    approval_root = Path(args.approval_state_root)
    approval_root.mkdir(parents=True, exist_ok=True)
    os.chmod(approval_root, 0o700)

    while True:
        run_dir = _current_run(prepare_root)
        if run_dir is None:
            time.sleep(args.poll_seconds)
            continue
        prepared_path = run_dir / "prepared.json"
        if not prepared_path.is_file():
            time.sleep(args.poll_seconds)
            continue

        try:
            prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
            validated = validate_prepared(prepared, observed_at=_utc_now())
        except (ApprovalError, json.JSONDecodeError, OSError):
            time.sleep(args.poll_seconds)
            continue

        intent_id = str(validated["intent_id"])
        state_dir = approval_root / intent_id
        approval_path = state_dir / "approval.json"
        if approval_path.is_file():
            time.sleep(args.poll_seconds)
            continue

        pending_path = state_dir / "pending.json"
        pending: dict[str, Any]
        message_id: int
        if pending_path.is_file():
            pending = json.loads(pending_path.read_text(encoding="utf-8"))
            if str(pending.get("request_sha256") or "") != str(validated["request_sha256"]):
                raise RuntimeError("prepared request changed after Telegram prompt")
            message_id = int(pending["telegram_message_id"])
        else:
            pending = new_pending(
                prepared,
                telegram_user_id=user_id,
                telegram_chat_id=chat_id,
                created_at=_utc_now(),
            )
            state_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(state_dir, 0o700)
            message_id = _send_prompt(
                token=token,
                chat_id=chat_id,
                prepared=prepared,
                pending=pending,
            )
            pending["telegram_message_id"] = message_id
            _atomic_json(pending_path, pending)
            _atomic_json(
                state_dir / "status.json",
                {
                    "status": "awaiting_human_approval",
                    "intent_id": intent_id,
                    "request_sha256": pending["request_sha256"],
                    "telegram_message_id": message_id,
                    "expires_at": pending["expires_at"],
                    "updated_at": _utc_now().isoformat(),
                    "real_order_submitted": False,
                },
            )

        record = _wait_for_decision(
            token=token,
            prepared_path=prepared_path,
            pending=pending,
            message_id=message_id,
            state_dir=state_dir,
        )
        print(json.dumps(record, sort_keys=True), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
