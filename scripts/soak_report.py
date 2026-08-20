from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine

from bp_engine.recorder.soak import FeedKey, build_soak_report

REQUIRED_FEEDS = [
    FeedKey("polymarket", "market"),
    FeedKey("bybit", "spot"),
    FeedKey("bybit", "linear"),
    FeedKey("coinbase", "spot"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate recorder soak-run health")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="SQLAlchemy database URL; defaults to DATABASE_URL",
    )
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--minimum-hours", type=float, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL or --database-url is required")
    if args.hours <= 0:
        raise SystemExit("--hours must be greater than zero")
    minimum_hours = args.hours if args.minimum_hours is None else args.minimum_hours
    if minimum_hours < 0:
        raise SystemExit("--minimum-hours must be non-negative")

    end = datetime.now(UTC)
    start = end - timedelta(hours=args.hours)
    engine = create_engine(args.database_url)
    with engine.connect() as connection:
        report = build_soak_report(
            connection,
            start_at=start,
            end_at=end,
            required_feeds=REQUIRED_FEEDS,
            minimum_duration_seconds=int(minimum_hours * 3600),
        )
    print(report.model_dump_json(indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
