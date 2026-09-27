from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Mapping

from bp_telegram_auto_approver.config import ConfigError, load_config, repo_root
from bp_telegram_auto_approver.contract import (
    APPROVAL_CONTRACT_BLOB_SHA,
    ContractMismatch,
    verify_approval_contract,
)
from bp_telegram_auto_approver.log import JsonLogger


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Watch the BP Telegram approval bot and optionally press APPROVE."
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate configuration and exit without connecting to Telegram.",
    )
    args = parser.parse_args(argv)
    logger = JsonLogger()
    try:
        verify_approval_contract()
    except ContractMismatch as exc:
        logger.emit(
            "APPROVAL_CONTRACT_MISMATCH",
            expected_blob=APPROVAL_CONTRACT_BLOB_SHA,
            actual_blob=exc.actual_blob,
        )
        return 2
    try:
        config = load_config(os.environ if environ is None else environ, repo_root())
    except ConfigError as exc:
        logger.emit("CONFIG_REJECTED", reason=exc.code)
        return 2
    if args.check_config:
        logger.emit(
            "CONFIG_OK",
            mode="live-auto-approve" if config.live else "dry-run",
            bot_username=config.bot_username,
            bot_user_id=config.bot_user_id,
            operator_user_id=config.operator_user_id,
        )
        return 0
    from bp_telegram_auto_approver.runtime import serve

    return asyncio.run(serve(config))
