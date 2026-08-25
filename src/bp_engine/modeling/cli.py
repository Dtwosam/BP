from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine

from bp_engine.config import Settings
from bp_engine.modeling.service import train_horizon
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
    parser = argparse.ArgumentParser(description="Train Phase 7 BTC Polymarket baselines")
    parser.add_argument("--start", type=parse_datetime, required=True)
    parser.add_argument("--end", type=parse_datetime, required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--feature-version", default="core-v1")
    parser.add_argument("--label-version", default="official-outcome-v1")
    parser.add_argument(
        "--horizon-seconds",
        action="append",
        type=int,
        help="repeat to train selected horizons; defaults to verified 300 and 900",
    )
    parser.add_argument("--min-markets", type=int, default=30)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--database-url", default=None)
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    if args.database_url:
        settings = settings.model_copy(update={"database_url": args.database_url})
    return settings


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _run(args: argparse.Namespace) -> list[dict[str, Any]]:
    validate_window(args.start, args.end)
    horizons = tuple(args.horizon_seconds or (300, 900))
    if any(horizon <= 0 for horizon in horizons):
        raise ValueError("horizon_seconds must be positive")
    if args.min_markets < 6:
        raise ValueError("min_markets must be at least 6")
    settings = _settings(args)
    engine = create_engine(settings.database_url)
    metadata.create_all(engine)
    output_dir = Path(args.output_dir)
    reports: list[dict[str, Any]] = []
    for horizon in horizons:
        with engine.begin() as connection:
            report = train_horizon(
                connection,
                start=args.start,
                end=args.end,
                horizon_seconds=horizon,
                feature_version=args.feature_version,
                label_version=args.label_version,
                output_dir=output_dir,
                min_markets=args.min_markets,
            )
        reports.append(_jsonable(asdict(report)))
    return reports


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        reports = _run(args)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(reports, indent=2, sort_keys=True))
    return 0
