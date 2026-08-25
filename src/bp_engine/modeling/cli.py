from __future__ import annotations

import argparse
from datetime import UTC, datetime


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
    return parser
