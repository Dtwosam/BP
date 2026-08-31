from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import create_engine

from bp_engine.config import Settings
from bp_engine.live_prediction.service import ensure_live_prediction_safety
from bp_engine.polymarket.gamma import GammaClient
from bp_engine.prospective_outcomes.service import ProspectiveOutcomeSyncService

DEFAULT_POLL_INTERVAL_SECONDS = 60.0


def _add_environment_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--database-url", default=None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Sync official post-resolution evidence for prospective predictions "
            "in RESEARCH mode only"
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    once_parser = subparsers.add_parser(
        "once",
        help="run one money-disabled official outcome sync cycle",
    )
    _add_environment_arguments(once_parser)

    run_parser = subparsers.add_parser(
        "run",
        help="continuously poll official outcomes in money-disabled RESEARCH mode",
    )
    _add_environment_arguments(run_parser)
    run_parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
    )
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    if args.database_url:
        settings = settings.model_copy(update={"database_url": args.database_url})
    return settings


def ensure_outcome_sync_safety(settings: Settings) -> None:
    ensure_live_prediction_safety(settings)


def _validate_poll_interval(value: float) -> float:
    interval = float(value)
    if interval < 1.0 or interval > 3600.0:
        raise ValueError("poll_interval_seconds must be between 1 and 3600")
    return interval


async def _run_once(settings: Settings) -> dict[str, object]:
    ensure_outcome_sync_safety(settings)
    engine = create_engine(settings.database_url)
    try:
        service = ProspectiveOutcomeSyncService(engine=engine, client=GammaClient())
        report = await service.run_once(now=datetime.now(UTC))
        return asdict(report)
    finally:
        engine.dispose()


async def _run_forever(settings: Settings, *, poll_interval_seconds: float) -> None:
    ensure_outcome_sync_safety(settings)
    interval = _validate_poll_interval(poll_interval_seconds)
    engine = create_engine(settings.database_url)
    service = ProspectiveOutcomeSyncService(engine=engine, client=GammaClient())
    try:
        while True:
            report = await service.run_once(now=datetime.now(UTC))
            print(json.dumps(asdict(report), sort_keys=True), flush=True)
            await asyncio.sleep(interval)
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = _settings(args)
    ensure_outcome_sync_safety(settings)

    if args.command == "once":
        report = asyncio.run(_run_once(settings))
        print(json.dumps(report, sort_keys=True))
        return 0
    if args.command == "run":
        asyncio.run(
            _run_forever(
                settings,
                poll_interval_seconds=args.poll_interval_seconds,
            )
        )
        return 0
    raise AssertionError(f"unsupported command: {args.command}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
