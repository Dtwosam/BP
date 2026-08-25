from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine

from bp_engine.backtesting.models import WalkForwardConfig
from bp_engine.backtesting.repository import BacktestRunRepository
from bp_engine.backtesting.service import run_walk_forward_backtest
from bp_engine.config import Settings
from bp_engine.storage.schema import metadata


def parse_datetime(value: str) -> datetime:
    rendered = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(rendered)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO-8601 datetime: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include a timezone")
    return parsed.astimezone(UTC)


def validate_window(start: datetime, end: datetime) -> None:
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("start must be timezone-aware")
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("end must be timezone-aware")
    if end <= start:
        raise ValueError("start must be before end")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the offline Phase 8 BTC Polymarket walk-forward backtest"
    )
    parser.add_argument("--start", type=parse_datetime, required=True)
    parser.add_argument("--end", type=parse_datetime, required=True)
    parser.add_argument(
        "--source-training-run-id",
        action="append",
        required=True,
        help="repeat for each immutable accepted Phase 7 source training run",
    )
    parser.add_argument("--train-hours", type=float, default=8)
    parser.add_argument("--validation-hours", type=float, default=2)
    parser.add_argument("--test-hours", type=float, default=2)
    parser.add_argument("--step-hours", type=float, default=2)
    parser.add_argument("--final-holdout-hours", type=float, default=2)
    parser.add_argument("--embargo-markets", type=int, default=1)
    parser.add_argument("--min-train-markets", type=int, default=24)
    parser.add_argument("--min-validation-markets", type=int, default=6)
    parser.add_argument("--min-test-markets", type=int, default=6)
    parser.add_argument("--min-market-price-coverage", type=float, default=0.80)
    parser.add_argument("--min-prediction-coverage", type=float, default=0.90)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--output-dir", required=True)
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    if args.database_url:
        settings = settings.model_copy(update={"database_url": args.database_url})
    return settings


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _config(args: argparse.Namespace) -> WalkForwardConfig:
    return WalkForwardConfig(
        train_duration=timedelta(hours=args.train_hours),
        validation_duration=timedelta(hours=args.validation_hours),
        test_duration=timedelta(hours=args.test_hours),
        step_duration=timedelta(hours=args.step_hours),
        final_holdout_duration=timedelta(hours=args.final_holdout_hours),
        embargo_markets=args.embargo_markets,
        min_train_markets=args.min_train_markets,
        min_validation_markets=args.min_validation_markets,
        min_test_markets=args.min_test_markets,
        min_market_price_coverage=args.min_market_price_coverage,
        min_prediction_coverage=args.min_prediction_coverage,
    )


def _write_atomic(output_dir: Path, reports: list[dict[str, Any]]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / "phase8-backtest-reports.json"
    temporary_path = output_dir / f".{final_path.name}.{os.getpid()}.tmp"
    try:
        temporary_path.write_text(
            json.dumps(reports, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, final_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return final_path


def _run(args: argparse.Namespace) -> list[dict[str, Any]]:
    validate_window(args.start, args.end)
    config = _config(args)
    source_run_ids = tuple(args.source_training_run_id)
    if not source_run_ids:
        raise ValueError("at least one source training run id is required")
    if len(set(source_run_ids)) != len(source_run_ids):
        raise ValueError("source training run ids must be unique")

    settings = _settings(args)
    engine = create_engine(settings.database_url)
    metadata.create_all(engine)
    repository = BacktestRunRepository()
    reports: list[dict[str, Any]] = []

    for source_run_id in source_run_ids:
        with engine.begin() as connection:
            report = run_walk_forward_backtest(
                connection,
                source_training_run_id=source_run_id,
                start=args.start,
                end=args.end,
                config=config,
                created_at=datetime.now(UTC),
            )
            repository.store(connection, report)
        reports.append(_jsonable(asdict(report)))

    reports.sort(key=lambda item: int(item["horizon_seconds"]))
    _write_atomic(Path(args.output_dir), reports)
    return reports


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        reports = _run(args)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(reports, indent=2, sort_keys=True))
    return 0
