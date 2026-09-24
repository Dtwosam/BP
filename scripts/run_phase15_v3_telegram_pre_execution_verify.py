from __future__ import annotations

import argparse
import json
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from bp_engine.execution.telegram_pre_execution import (
    PreExecutionError,
    evaluate_pre_execution_authorization,
)

MAX_INPUT_BYTES = 512 * 1024


class PreExecutionCliError(RuntimeError):
    pass


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise PreExecutionCliError(f"{label} is not readable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise PreExecutionCliError(f"{label} must be a regular non-symlink file")
    if info.st_size <= 0 or info.st_size > MAX_INPUT_BYTES:
        raise PreExecutionCliError(f"{label} size invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreExecutionCliError(f"{label} JSON invalid") from exc
    if not isinstance(payload, Mapping):
        raise PreExecutionCliError(f"{label} must contain a JSON object")
    return dict(payload)


def evaluate_files(
    *,
    ready_verification_path: Path,
    project_state_path: Path,
) -> dict[str, Any]:
    ready = _load_json(ready_verification_path, label="ready verification")
    state = _load_json(project_state_path, label="project state")
    try:
        return evaluate_pre_execution_authorization(
            ready_verification=ready,
            project_state=state,
        )
    except PreExecutionError as exc:
        raise PreExecutionCliError(str(exc)) from exc


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only evaluate BP Telegram pre-execution source-truth authorization."
    )
    parser.add_argument("--ready-verification", type=Path, required=True)
    parser.add_argument("--project-state", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        report = evaluate_files(
            ready_verification_path=args.ready_verification,
            project_state_path=args.project_state,
        )
    except PreExecutionCliError as exc:
        print(
            json.dumps(
                {
                    "status": "pre_execution_invalid",
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

    print(json.dumps(report, sort_keys=True))
    return 0 if report["authorized"] is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
