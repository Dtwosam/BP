from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
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
    validate_approved_handoff,
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


def _handoff_command() -> Path | None:
    raw = os.environ.get("BP_TELEGRAM_HANDOFF_COMMAND", "").strip()
    if not raw:
        return None
    command = Path(raw)
    if not command.is_absolute():
        raise SystemExit("BP_TELEGRAM_HANDOFF_COMMAND must be an absolute path")
    return command


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path.name} must contain a JSON object")
    return payload


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _dispatch_approved_handoff(
    *,
    command: Path | None,
    prepared_path: Path,
    approval_path: Path,
    state_dir: Path,
) -> dict[str, Any]:
    approval = _load_json(approval_path)
    if approval.get("status") != "approved":
        return {"status": "not_approved", "intent_id": approval.get("intent_id")}

    prepared = _load_json(prepared_path)
    binding = validate_approved_handoff(
        prepared,
        approval=approval,
        observed_at=_utc_now(),
    )
    result_path = state_dir / "handoff-result.json"
    if result_path.is_file():
        return _load_json(result_path)

    attempt_path = state_dir / "handoff-attempt.json"
    if attempt_path.is_file():
        ambiguous = {
            "schema_version": 1,
            "status": "handoff_ambiguous_no_retry",
            "intent_id": binding["intent_id"],
            "request_sha256": binding["request_sha256"],
            "updated_at": _utc_now().isoformat(),
            "retry_allowed": False,
        }
        _atomic_json(result_path, ambiguous)
        _atomic_json(state_dir / "status.json", ambiguous)
        return ambiguous

    if command is None:
        disabled = {
            "status": "approved_handoff_not_configured",
            "intent_id": binding["intent_id"],
            "request_sha256": binding["request_sha256"],
            "updated_at": _utc_now().isoformat(),
            "real_order_submitted": False,
        }
        _atomic_json(state_dir / "status.json", disabled)
        return disabled
    if not command.is_file() or not os.access(command, os.X_OK):
        invalid = {
            "status": "handoff_command_invalid",
            "intent_id": binding["intent_id"],
            "request_sha256": binding["request_sha256"],
            "updated_at": _utc_now().isoformat(),
            "real_order_submitted": False,
        }
        _atomic_json(state_dir / "status.json", invalid)
        return invalid

    handoff_prepared_path = state_dir / "handoff-prepared.json"
    _atomic_json(handoff_prepared_path, prepared)
    frozen_binding = validate_approved_handoff(
        _load_json(handoff_prepared_path),
        approval=approval,
        observed_at=_utc_now(),
    )
    if frozen_binding["request_sha256"] != binding["request_sha256"]:
        raise RuntimeError("handoff prepared snapshot changed")

    attempt = {
        "schema_version": 1,
        "status": "handoff_started",
        "intent_id": binding["intent_id"],
        "prediction_id": binding["prediction_id"],
        "paper_order_id": binding["paper_order_id"],
        "request_sha256": binding["request_sha256"],
        "approved_at": binding["approved_at"],
        "started_at": _utc_now().isoformat(),
        "command": str(command),
        "retry_allowed": False,
    }
    _atomic_json(attempt_path, attempt)
    _atomic_json(state_dir / "status.json", attempt)

    environment = os.environ.copy()
    environment["BP_APPROVED_INTENT_ID"] = str(binding["intent_id"])
    environment["BP_APPROVED_REQUEST_SHA256"] = str(binding["request_sha256"])
    try:
        completed = subprocess.run(
            [str(command), str(handoff_prepared_path), str(approval_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            env=environment,
        )
    except Exception as exc:
        result = {
            "schema_version": 1,
            "status": "handoff_ambiguous_no_retry",
            "intent_id": binding["intent_id"],
            "request_sha256": binding["request_sha256"],
            "error_type": type(exc).__name__,
            "completed_at": _utc_now().isoformat(),
            "retry_allowed": False,
        }
    else:
        result = {
            "schema_version": 1,
            "status": "handoff_completed" if completed.returncode == 0 else "handoff_failed_no_retry",
            "intent_id": binding["intent_id"],
            "request_sha256": binding["request_sha256"],
            "command_exit_code": completed.returncode,
            "stdout_sha256": _text_sha256(completed.stdout),
            "stderr_sha256": _text_sha256(completed.stderr),
            "completed_at": _utc_now().isoformat(),
            "retry_allowed": False,
        }
    _atomic_json(result_path, result)
    _atomic_json(state_dir / "status.json", result)
    return result


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
    handoff_command = _handoff_command()

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
            try:
                existing_approval = _load_json(approval_path)
                if existing_approval.get("status") == "approved":
                    handoff = _dispatch_approved_handoff(
                        command=handoff_command,
                        prepared_path=prepared_path,
                        approval_path=approval_path,
                        state_dir=state_dir,
                    )
                    print(json.dumps(handoff, sort_keys=True), flush=True)
            except (ApprovalError, json.JSONDecodeError, OSError, RuntimeError) as exc:
                _atomic_json(
                    state_dir / "status.json",
                    {
                        "status": "handoff_validation_failed",
                        "intent_id": intent_id,
                        "error_type": type(exc).__name__,
                        "updated_at": _utc_now().isoformat(),
                        "real_order_submitted": False,
                    },
                )
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
        if record.get("status") == "approved":
            handoff = _dispatch_approved_handoff(
                command=handoff_command,
                prepared_path=prepared_path,
                approval_path=approval_path,
                state_dir=state_dir,
            )
            print(json.dumps(handoff, sort_keys=True), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
