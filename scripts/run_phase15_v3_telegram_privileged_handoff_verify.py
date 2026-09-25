from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from bp_engine.execution.telegram_privileged_handoff import (
    PrivilegedHandoffContractError,
    verify_privileged_handoff_contract,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only verification of the exact BP Phase 15 privileged-handoff "
            "contract. This command never arms, invokes the executor, or submits."
        )
    )
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--processed-receipt", type=Path, required=True)
    parser.add_argument("--executor-path", type=Path, required=True)
    parser.add_argument("--expected-executor-sha256", required=True)
    parser.add_argument("--expected-owner-uid", type=int, default=0)
    return parser.parse_args()


def _require_safe_runtime() -> None:
    for name in (
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
        "BP_TELEGRAM_BOT_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "PHASE15_ACCEPT_REAL_MONEY",
        "PHASE15_ACCEPT_TELEGRAM_REAL_MONEY",
    ):
        if os.environ.get(name):
            raise SystemExit(
                f"{name} must not be present in handoff-contract verifier runtime"
            )


def main() -> int:
    args = _parse_args()
    _require_safe_runtime()
    try:
        result = verify_privileged_handoff_contract(
            package_dir=args.package_dir,
            processed_receipt_path=args.processed_receipt,
            executor_path=args.executor_path,
            expected_executor_sha256=args.expected_executor_sha256,
            observed_at=_utc_now(),
            expected_owner_uid=args.expected_owner_uid,
        )
    except PrivilegedHandoffContractError as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error": str(exc),
                    "retry_allowed": False,
                    "activation_manifest_created": False,
                    "authorization_id_created": False,
                    "kill_switch_mutated": False,
                    "executor_payload_materialized": False,
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
