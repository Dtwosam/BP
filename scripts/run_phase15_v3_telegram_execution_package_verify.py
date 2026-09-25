from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from bp_engine.execution.telegram_execution_package import (
    ExecutionPackageError,
    verify_execution_authorization_package,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only verification of one completed BP Telegram execution "
            "authorization package. This command never arms or submits."
        )
    )
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--processed-receipt", type=Path, required=True)
    return parser.parse_args()


def _require_safe_runtime() -> None:
    for name in (
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
        "BP_TELEGRAM_BOT_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        if os.environ.get(name):
            raise SystemExit(
                f"{name} must not be present in package verifier runtime"
            )


def main() -> int:
    args = _parse_args()
    _require_safe_runtime()
    try:
        result = verify_execution_authorization_package(
            package_dir=args.package_dir,
            processed_receipt_path=args.processed_receipt,
            observed_at=_utc_now(),
            expected_owner_uid=0,
        )
    except ExecutionPackageError as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error": str(exc),
                    "retry_allowed": False,
                    "mutation_performed": False,
                    "network_action_performed": False,
                    "handoff_invoked": False,
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
