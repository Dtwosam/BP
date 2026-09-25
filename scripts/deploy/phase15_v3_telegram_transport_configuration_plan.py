from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path
from typing import Any

from bp_engine.execution.telegram_transport_configuration import (
    DEFAULT_ORIGIN_KEY_ID,
    DEFAULT_PROJECT_ID,
    DEFAULT_SUBSCRIPTION_ID,
    DEFAULT_TOPIC_ID,
    DEFAULT_TRANSPORT_KEY_ID,
    TransportConfigurationError,
    create_configuration_plan,
)

MAX_PROJECT_STATE_BYTES = 2 * 1024 * 1024


def _load_project_state(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise TransportConfigurationError(
            "project state is not readable"
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise TransportConfigurationError(
            "project state must be a regular non-symlink file"
        )
    if info.st_size <= 0 or info.st_size > MAX_PROJECT_STATE_BYTES:
        raise TransportConfigurationError("project state size invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransportConfigurationError(
            "project state JSON invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise TransportConfigurationError(
            "project state must contain a JSON object"
        )
    return payload


def _require_secret_free_runtime() -> None:
    for name in (
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
        "BP_TELEGRAM_BOT_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        if os.environ.get(name):
            raise SystemExit(
                f"{name} must not be present in configuration-plan runtime"
            )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Render a deterministic, secret-free BP Telegram transport "
            "configuration plan. This command performs no cloud or host mutation."
        )
    )
    parser.add_argument(
        "--project-state",
        type=Path,
        default=Path("PROJECT_STATE.json"),
    )
    parser.add_argument("--release-head", required=True)
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--topic-id", default=DEFAULT_TOPIC_ID)
    parser.add_argument("--subscription-id", default=DEFAULT_SUBSCRIPTION_ID)
    parser.add_argument(
        "--transport-key-id",
        default=DEFAULT_TRANSPORT_KEY_ID,
    )
    parser.add_argument("--origin-key-id", default=DEFAULT_ORIGIN_KEY_ID)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _require_secret_free_runtime()
    try:
        state = _load_project_state(args.project_state)
        plan = create_configuration_plan(
            state,
            release_head=args.release_head,
            project_id=args.project_id,
            topic_id=args.topic_id,
            subscription_id=args.subscription_id,
            transport_key_id=args.transport_key_id,
            origin_key_id=args.origin_key_id,
        )
    except TransportConfigurationError as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error": str(exc),
                    "mutation_performed": False,
                    "network_action_performed": False,
                    "secret_provisioning_performed": False,
                    "service_started": False,
                    "service_enabled": False,
                    "executor_invoked": False,
                    "real_order_submitted": False,
                },
                sort_keys=True,
            )
        )
        return 1

    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
