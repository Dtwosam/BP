from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from bp_engine.execution.telegram_execution_ready import (
    ReadyVerificationError,
    verify_ready_bundle,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only verify a claimed Telegram execution-ready bundle using the "
            "separate approval-origin key."
        )
    )
    parser.add_argument("ready_dir", type=Path)
    parser.add_argument("--origin-key-file", type=Path, required=True)
    parser.add_argument("--origin-key-id", required=True)
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
            raise SystemExit(f"{forbidden} must not be present in ready verifier")

    transport_key_path = os.environ.get("BP_TELEGRAM_TRANSPORT_KEY_FILE", "").strip()
    if transport_key_path:
        try:
            same_key_file = Path(transport_key_path).resolve() == args.origin_key_file.resolve()
        except OSError as exc:
            raise SystemExit("unable to compare transport and origin key paths") from exc
        if same_key_file:
            raise SystemExit("origin key must be separate from transport key")

    try:
        result = verify_ready_bundle(
            ready_dir=args.ready_dir,
            origin_key_path=args.origin_key_file,
            expected_origin_key_id=args.origin_key_id,
            observed_at=_utc_now(),
        )
    except ReadyVerificationError as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error": str(exc),
                    "retry_allowed": False,
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
