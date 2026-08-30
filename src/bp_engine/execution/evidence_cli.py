from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import create_engine

from bp_engine.config import Settings, TradingMode, get_settings
from bp_engine.dashboard.repository import PostgresDashboardRepository
from bp_engine.execution.evidence import (
    PostgresProspectivePaperEvidenceReader,
    summarize_prospective_paper_evidence,
)

_INTERPRETATION = "evidence_only_no_automatic_live_gate_pass"


def _parse_since(value: str) -> datetime:
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--since must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--since must be timezone-aware")
    return parsed.astimezone(UTC)


def _money_disabled(settings: Settings) -> None:
    if settings.mode is not TradingMode.RESEARCH:
        raise RuntimeError("prospective evidence requires MODE=research")
    if settings.live_trading_enabled:
        raise RuntimeError("prospective evidence requires LIVE_TRADING_ENABLED=false")
    if Decimal(str(settings.max_trade_size_usd)) != Decimal("0"):
        raise RuntimeError("prospective evidence requires MAX_TRADE_SIZE_USD=0")
    if Decimal(str(settings.max_daily_loss_usd)) != Decimal("0"):
        raise RuntimeError("prospective evidence requires MAX_DAILY_LOSS_USD=0")


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report read-only prospective paper evidence after an explicit boundary"
    )
    parser.add_argument(
        "--since",
        required=True,
        type=_parse_since,
        help="timezone-aware ISO-8601 start of the prospective evidence window",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=14,
        help="deterministic bootstrap seed",
    )
    parser.add_argument(
        "--resamples",
        type=int,
        default=10_000,
        help="number of deterministic bootstrap resamples",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.resamples <= 0:
        raise SystemExit("--resamples must be positive")

    settings = get_settings()
    _money_disabled(settings)
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        inputs = PostgresProspectivePaperEvidenceReader(engine).load(since=args.since)
        paper_evidence = PostgresDashboardRepository(engine).get_paper_execution_evidence(
            history_limit=1
        )
        reconciliation = paper_evidence["paper_pnl"]["reconciliation"]
        report = summarize_prospective_paper_evidence(
            predictions=inputs.predictions,
            evaluations=inputs.evaluations,
            settled_trades=inputs.settled_trades,
            reconciliation=reconciliation,
            seed=args.seed,
            resamples=args.resamples,
        )
        payload = asdict(report)
        payload["since"] = args.since.isoformat()
        payload["interpretation"] = _INTERPRETATION
        print(
            json.dumps(
                _json_value(payload),
                sort_keys=True,
                separators=(",", ":"),
            ),
            flush=True,
        )
    finally:
        engine.dispose()
    return 0
