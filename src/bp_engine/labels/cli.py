from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import create_engine

from bp_engine.config import Settings
from bp_engine.labels.service import generate_labels
from bp_engine.storage.schema import metadata


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


def validate_window(start: datetime, end: datetime) -> None:
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("start must be timezone-aware")
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("end must be timezone-aware")
    if start >= end:
        raise ValueError("start must be before end")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate immutable official Polymarket outcome labels offline"
    )
    parser.add_argument("--start", required=True, type=parse_datetime)
    parser.add_argument("--end", required=True, type=parse_datetime)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--database-url", default=None)
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    if args.database_url:
        settings = settings.model_copy(update={"database_url": args.database_url})
    return settings


def _run(args: argparse.Namespace):
    validate_window(args.start, args.end)
    settings = _settings(args)
    engine = create_engine(settings.database_url)
    metadata.create_all(engine)
    generated_at = datetime.now(UTC)
    with engine.begin() as connection:
        return generate_labels(
            connection,
            start=args.start,
            end=args.end,
            generated_at=generated_at,
        )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        stats = _run(args)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(asdict(stats), indent=2, sort_keys=True))
    return 0
