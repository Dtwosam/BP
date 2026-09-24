from __future__ import annotations

import argparse
import json
import os
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bp_engine.execution.telegram_dispatch_ticket import (
    DispatchTicketError,
    claim_dispatch_ticket,
    create_dispatch_ticket,
)


MAX_JSON_BYTES = 256 * 1024


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _load_private_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise DispatchTicketError(f"{label} is not readable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise DispatchTicketError(f"{label} must be a regular non-symlink file")
    if stat.S_IMODE(info.st_mode) not in (0o600, 0o640):
        raise DispatchTicketError(f"{label} mode must be 0600 or 0640")
    if info.st_size <= 0 or info.st_size > MAX_JSON_BYTES:
        raise DispatchTicketError(f"{label} size invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DispatchTicketError(f"{label} JSON invalid") from exc
    if not isinstance(payload, Mapping):
        raise DispatchTicketError(f"{label} must contain a JSON object")
    return dict(payload)


def _load_source_truth_json(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise DispatchTicketError("project state is not readable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise DispatchTicketError("project state must be a regular non-symlink file")
    if stat.S_IMODE(info.st_mode) not in (0o600, 0o640, 0o644):
        raise DispatchTicketError("project state mode must be 0600, 0640, or 0644")
    if info.st_size <= 0 or info.st_size > MAX_JSON_BYTES:
        raise DispatchTicketError("project state size invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DispatchTicketError("project state JSON invalid") from exc
    if not isinstance(payload, Mapping):
        raise DispatchTicketError("project state must contain a JSON object")
    return dict(payload)


def _ensure_private_parent(path: Path) -> None:
    parent = path.parent
    try:
        info = parent.lstat()
    except OSError as exc:
        raise DispatchTicketError("dispatch ticket output directory missing") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise DispatchTicketError(
            "dispatch ticket output directory must be a non-symlink directory"
        )
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise DispatchTicketError("dispatch ticket output directory mode must be 0700")


def _write_once(path: Path, payload: Mapping[str, Any]) -> None:
    _ensure_private_parent(path)
    encoded = (
        json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    )
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise DispatchTicketError("dispatch ticket output already exists") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create or one-shot claim an offline BP Telegram dispatch ticket. "
            "This command never arms or submits an order."
        )
    )
    sub = parser.add_subparsers(dest="action", required=True)

    create = sub.add_parser("create")
    create.add_argument("--pre-execution-report", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)

    claim = sub.add_parser("claim")
    claim.add_argument("--ticket", type=Path, required=True)
    claim.add_argument("--pre-execution-report", type=Path, required=True)
    claim.add_argument("--ready-verification", type=Path, required=True)
    claim.add_argument("--project-state", type=Path, required=True)
    claim.add_argument("--state-dir", type=Path, required=True)
    return parser.parse_args()


def _require_safe_runtime() -> None:
    if os.environ.get("BP_TELEGRAM_DISPATCH_TICKET_ENABLED", "no") != "yes":
        raise SystemExit("Telegram dispatch ticket path is not enabled")
    if os.environ.get("MODE") != "research":
        raise SystemExit("MODE must be research")
    if os.environ.get("LIVE_TRADING_ENABLED") != "false":
        raise SystemExit("LIVE_TRADING_ENABLED must be false")
    if os.environ.get("MAX_TRADE_SIZE_USD") != "0":
        raise SystemExit("MAX_TRADE_SIZE_USD must be 0")
    if os.environ.get("MAX_DAILY_LOSS_USD") != "0":
        raise SystemExit("MAX_DAILY_LOSS_USD must be 0")
    for forbidden in (
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
        "BP_TELEGRAM_BOT_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        if os.environ.get(forbidden):
            raise SystemExit(f"{forbidden} must not be present in dispatch ticket path")


def main() -> int:
    args = _parse_args()
    _require_safe_runtime()
    try:
        if args.action == "create":
            report = _load_private_json(
                args.pre_execution_report,
                label="pre-execution report",
            )
            ticket = create_dispatch_ticket(
                report,
                created_at=_utc_now(),
            )
            _write_once(args.output, ticket)
            result = {
                "status": "dispatch_ticket_written",
                "dispatch_ticket_sha256": ticket["dispatch_ticket_sha256"],
                "authorization_report_sha256": ticket[
                    "authorization_report_sha256"
                ],
                "source_truth_sha256": ticket["source_truth_sha256"],
                "intent_id": ticket["intent_id"],
                "request_sha256": ticket["request_sha256"],
                "expires_at": ticket["expires_at"],
                "output_path": str(args.output),
                "retry_allowed": False,
                "mutation_performed": False,
                "network_action_performed": False,
                "executor_invoked": False,
                "real_order_submitted": False,
            }
        else:
            ticket = _load_private_json(args.ticket, label="dispatch ticket")
            report = _load_private_json(
                args.pre_execution_report,
                label="pre-execution report",
            )
            ready = _load_private_json(
                args.ready_verification,
                label="ready verification",
            )
            state = _load_source_truth_json(args.project_state)
            result = claim_dispatch_ticket(
                ticket,
                pre_execution_report=report,
                ready_verification=ready,
                project_state=state,
                observed_at=_utc_now(),
                state_dir=args.state_dir,
            )
    except DispatchTicketError as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error": str(exc),
                    "retry_allowed": False,
                    "mutation_performed": False,
                    "network_action_performed": False,
                    "executor_invoked": False,
                    "real_order_submitted": False,
                },
                sort_keys=True,
            )
        )
        return 1

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
