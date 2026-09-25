from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx

from bp_engine.execution.telegram_pubsub import PubSubTransportError
from bp_engine.execution.telegram_pubsub_delivery import publish_transport
from bp_engine.execution.telegram_transport import TransportError


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish one prebuilt BP Telegram transport envelope to Pub/Sub."
    )
    parser.add_argument("envelope_path", type=Path)
    parser.add_argument(
        "--receipt-dir",
        type=Path,
        default=Path("/var/lib/bp/phase15-canary-telegram-transport/published"),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if os.environ.get("BP_TELEGRAM_PUBSUB_PUBLISH_ENABLED", "no") != "yes":
        raise SystemExit("Telegram Pub/Sub publishing is not enabled")
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
            raise SystemExit(f"{forbidden} must not be present in Pub/Sub publisher")

    key_path_raw = os.environ.get("BP_TELEGRAM_TRANSPORT_KEY_FILE", "").strip()
    key_id = os.environ.get("BP_TELEGRAM_TRANSPORT_KEY_ID", "").strip()
    project_id = os.environ.get("BP_TELEGRAM_PUBSUB_PROJECT_ID", "").strip()
    topic_id = os.environ.get("BP_TELEGRAM_PUBSUB_TOPIC_ID", "").strip()
    if not key_path_raw or not key_id or not project_id or not topic_id:
        raise SystemExit("Telegram Pub/Sub publisher configuration incomplete")

    try:
        with httpx.Client(trust_env=False) as client:
            result = publish_transport(
                client=client,
                envelope_path=args.envelope_path,
                key_path=Path(key_path_raw),
                expected_key_id=key_id,
                project_id=project_id,
                topic_id=topic_id,
                receipt_dir=args.receipt_dir,
                observed_at=_utc_now(),
            )
    except (TransportError, PubSubTransportError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error": str(exc),
                    "executor_invoked": False,
                    "real_order_submitted": False,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 1

    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
