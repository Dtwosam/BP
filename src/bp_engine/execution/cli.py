from __future__ import annotations

import argparse
import json
import signal
import time
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import create_engine

from bp_engine.config import Settings, TradingMode, get_settings
from bp_engine.execution.models import (
    PAPER_EXECUTION_VERSION,
    PaperExecutionConfig,
)
from bp_engine.execution.service import PaperExecutionService, PaperRunReport

_STOP_REQUESTED = False


def _money_disabled(settings: Settings) -> None:
    if settings.mode is not TradingMode.RESEARCH:
        raise RuntimeError("paper execution worker requires MODE=research")
    if settings.live_trading_enabled:
        raise RuntimeError("paper execution worker requires LIVE_TRADING_ENABLED=false")
    if Decimal(str(settings.max_trade_size_usd)) != Decimal("0"):
        raise RuntimeError("paper execution worker requires MAX_TRADE_SIZE_USD=0")
    if Decimal(str(settings.max_daily_loss_usd)) != Decimal("0"):
        raise RuntimeError("paper execution worker requires MAX_DAILY_LOSS_USD=0")


def _decimal_argument(value: str) -> Decimal:
    try:
        numeric = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("value must be a decimal number") from exc
    if not numeric.is_finite():
        raise argparse.ArgumentTypeError("value must be finite")
    return numeric


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _report_json(report: PaperRunReport) -> str:
    return json.dumps(_json_value(asdict(report)), sort_keys=True, separators=(",", ":"))


def _request_stop(_signum: int, _frame: object) -> None:
    global _STOP_REQUESTED
    _STOP_REQUESTED = True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the deterministic Phase 12 paper execution worker"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="process one bounded paper execution pass",
    )
    parser.add_argument("--prediction-version", default=None)
    parser.add_argument(
        "--exclude-prediction-version",
        action="append",
        default=[],
    )
    parser.add_argument("--execution-version", default=PAPER_EXECUTION_VERSION)
    parser.add_argument(
        "--starting-cash-usd",
        type=_decimal_argument,
        default=Decimal("100.00"),
    )
    parser.add_argument(
        "--target-notional-usd",
        type=_decimal_argument,
        default=Decimal("5.00"),
    )
    parser.add_argument("--latency-ms", type=int, default=250)
    parser.add_argument("--order-ttl-ms", type=int, default=2000)
    parser.add_argument("--share-precision", type=int, default=6)
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=5.0,
        help="continuous-mode delay between bounded passes",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    global _STOP_REQUESTED
    args = build_parser().parse_args(argv)
    if args.poll_seconds <= 0:
        raise SystemExit("--poll-seconds must be positive")

    settings = get_settings()
    _money_disabled(settings)
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    config = PaperExecutionConfig(
        starting_cash_usd=args.starting_cash_usd,
        target_notional_usd=args.target_notional_usd,
        latency_ms=args.latency_ms,
        order_ttl_ms=args.order_ttl_ms,
        share_precision=args.share_precision,
        execution_version=args.execution_version,
        prediction_version=args.prediction_version,
        excluded_prediction_versions=tuple(args.exclude_prediction_version),
    )
    service = PaperExecutionService(engine=engine, config=config)

    if args.once:
        report = service.run_once(now=datetime.now(UTC))
        print(_report_json(report), flush=True)
        engine.dispose()
        return 0

    _STOP_REQUESTED = False
    previous_term = signal.getsignal(signal.SIGTERM)
    previous_int = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    try:
        while not _STOP_REQUESTED:
            report = service.run_once(now=datetime.now(UTC))
            print(_report_json(report), flush=True)
            deadline = time.monotonic() + args.poll_seconds
            while not _STOP_REQUESTED and time.monotonic() < deadline:
                time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)
        engine.dispose()
    return 0
