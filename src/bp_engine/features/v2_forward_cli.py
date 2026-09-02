from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import create_engine

from bp_engine.config import Settings, TradingMode
from bp_engine.features.v2_forward import V2ForwardCycleStats, run_v2_forward_cycle


def parse_datetime(value: str) -> datetime:
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO-8601 datetime: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include a timezone offset or Z")
    return parsed.astimezone(UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one research-only outcome-blind V2 forward coverage cycle"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    once = subparsers.add_parser("once", help="run one bounded V2 forward coverage cycle")
    once.add_argument("--env-file", default=None)
    once.add_argument("--database-url", default=None)
    once.add_argument("--cycle-at", type=parse_datetime, default=None)
    return parser


def _zero(value: object) -> bool:
    try:
        return Decimal(str(value)).is_zero()
    except (InvalidOperation, ValueError):
        return False


def require_research_zero_money(settings: Settings) -> None:
    safe = (
        settings.mode == TradingMode.RESEARCH
        and settings.live_trading_enabled is False
        and _zero(settings.max_trade_size_usd)
        and _zero(settings.max_daily_loss_usd)
    )
    if not safe:
        raise ValueError(
            "V2 forward coverage requires RESEARCH/live-disabled/zero-money safety"
        )


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    if args.database_url:
        settings = settings.model_copy(update={"database_url": args.database_url})
    return settings


def _run(args: argparse.Namespace) -> V2ForwardCycleStats:
    settings = _settings(args)
    require_research_zero_money(settings)
    cycle_at = args.cycle_at or datetime.now(UTC)
    engine = create_engine(settings.database_url)
    try:
        with engine.begin() as connection:
            return run_v2_forward_cycle(connection, cycle_at=cycle_at)
    finally:
        engine.dispose()


def _payload(stats: V2ForwardCycleStats) -> dict[str, object]:
    payload = asdict(stats)
    payload["cycle_at"] = stats.cycle_at.isoformat()
    return payload


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "once":
        raise RuntimeError(f"unsupported command: {args.command}")
    try:
        stats = _run(args)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(_payload(stats), indent=2, sort_keys=True))
    return 0
