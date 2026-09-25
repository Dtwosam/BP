from __future__ import annotations

import argparse
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bp_engine.execution.telegram_execution_ready import (
    ReadyVerificationError,
    verify_ready_bundle,
)
from bp_engine.execution.telegram_pre_execution import (
    PreExecutionError,
    evaluate_pre_execution_authorization,
)

MAX_PROJECT_STATE_BYTES = 2 * 1024 * 1024


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _load_project_state(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise PreExecutionError("project state is not readable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise PreExecutionError("project state must be a regular non-symlink file")
    if info.st_size <= 0 or info.st_size > MAX_PROJECT_STATE_BYTES:
        raise PreExecutionError("project state file size invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreExecutionError("project state JSON invalid") from exc
    if not isinstance(payload, dict):
        raise PreExecutionError("project state must contain a JSON object")
    return payload


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only gate an origin-verified Telegram ready bundle against exact "
            "BP source-truth authorization."
        )
    )
    parser.add_argument("ready_dir", type=Path)
    parser.add_argument("--origin-key-file", type=Path, required=True)
    parser.add_argument("--origin-key-id", required=True)
    parser.add_argument("--project-state", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    for forbidden in (
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
        "BP_TELEGRAM_BOT_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        if os.environ.get(forbidden):
            raise SystemExit(f"{forbidden} must not be present in pre-execution gate")

    transport_key_path = os.environ.get("BP_TELEGRAM_TRANSPORT_KEY_FILE", "").strip()
    if transport_key_path:
        try:
            same_key_file = Path(transport_key_path).resolve() == args.origin_key_file.resolve()
        except OSError as exc:
            raise SystemExit("unable to compare transport and origin key paths") from exc
        if same_key_file:
            raise SystemExit("origin key must be separate from transport key")

    try:
        ready = verify_ready_bundle(
            ready_dir=args.ready_dir,
            origin_key_path=args.origin_key_file,
            expected_origin_key_id=args.origin_key_id,
            observed_at=_utc_now(),
        )
        state = _load_project_state(args.project_state)
        result = evaluate_pre_execution_authorization(
            ready_verification=ready,
            project_state=state,
        )
    except (ReadyVerificationError, PreExecutionError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error": str(exc),
                    "authorized": False,
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
    return 0 if result["authorized"] is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
