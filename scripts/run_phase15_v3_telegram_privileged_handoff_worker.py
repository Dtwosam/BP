from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import time
from datetime import UTC, datetime
from pathlib import Path

from bp_engine.execution.telegram_privileged_consumer import (
    execute_authorized_package_once,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _executor_sha256(path: Path) -> str:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SystemExit("executor must be a regular non-symlink file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Privileged Johannesburg consumer for exactly one fully verified "
            "Phase 15 Telegram handoff. Any attempt is terminal and never retried."
        )
    )
    parser.add_argument(
        "--package-root",
        type=Path,
        default=Path("/var/lib/bp-canary/telegram-execution-authorized"),
    )
    parser.add_argument(
        "--processed-root",
        type=Path,
        default=Path("/var/lib/bp-canary/telegram-execution-auth-processed"),
    )
    parser.add_argument(
        "--state-root",
        type=Path,
        default=Path("/var/lib/bp-canary/telegram-live-handoff"),
    )
    parser.add_argument(
        "--executor-path",
        type=Path,
        default=Path("/opt/bp-canary/executor.py"),
    )
    parser.add_argument(
        "--executor-wrapper",
        type=Path,
        default=Path("/opt/bp-canary/executor.sh"),
    )
    parser.add_argument(
        "--release-manifest",
        type=Path,
        default=Path("/opt/bp-telegram-transport/current/RELEASE-MANIFEST.json"),
    )
    parser.add_argument(
        "--activation-path",
        type=Path,
        default=Path("/etc/bp-canary/activation.json"),
    )
    parser.add_argument(
        "--kill-switch",
        type=Path,
        default=Path("/etc/bp-canary/KILL"),
    )
    parser.add_argument("--poll-seconds", type=float, default=0.05)
    return parser.parse_args()


def _require_runtime() -> None:
    if os.environ.get("BP_TELEGRAM_PRIVILEGED_HANDOFF_ENABLED") != "yes":
        raise SystemExit("privileged handoff is not explicitly enabled")
    for name in (
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
        "BP_TELEGRAM_BOT_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        if os.environ.get(name):
            raise SystemExit(f"{name} must not be inherited by privileged consumer")


def main() -> int:
    args = _parse_args()
    _require_runtime()
    if not 0.02 <= args.poll_seconds <= 5:
        raise SystemExit("poll seconds must be within 0.02..5")

    expected_executor_sha256 = _executor_sha256(args.executor_path)
    while True:
        for package_dir in sorted(args.package_root.iterdir()):
            if not package_dir.is_dir() or package_dir.name.startswith("."):
                continue
            processed = args.processed_root / f"{package_dir.name}.json"
            if not processed.is_file():
                continue
            result = execute_authorized_package_once(
                package_dir=package_dir,
                processed_receipt_path=processed,
                executor_path=args.executor_path,
                executor_wrapper_path=args.executor_wrapper,
                release_manifest_path=args.release_manifest,
                activation_path=args.activation_path,
                kill_switch_path=args.kill_switch,
                state_root=args.state_root,
                expected_executor_sha256=expected_executor_sha256,
                observed_at=_utc_now(),
                expected_owner_uid=0,
            )
            if result.get("status") != "already_terminal":
                print(json.dumps(result, sort_keys=True), flush=True)
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
