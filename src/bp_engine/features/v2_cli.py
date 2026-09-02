from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import Connection, create_engine, select

from bp_engine.config import Settings
from bp_engine.features.v2_models import V2FeatureTarget
from bp_engine.features.v2_service import V2FeatureGenerationStats, generate_v2_features
from bp_engine.storage.schema import metadata, polymarket_markets


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


def load_v2_targets(
    connection: Connection,
    *,
    start: datetime,
    end: datetime,
) -> tuple[V2FeatureTarget, ...]:
    validate_window(start, end)
    rows = connection.execute(
        select(
            polymarket_markets.c.condition_id,
            polymarket_markets.c.slug,
            polymarket_markets.c.horizon_seconds,
            polymarket_markets.c.start_at,
            polymarket_markets.c.end_at,
            polymarket_markets.c.up_token_id,
            polymarket_markets.c.down_token_id,
        )
        .where(
            polymarket_markets.c.horizon_seconds == 300,
            polymarket_markets.c.start_at >= start,
            polymarket_markets.c.start_at < end,
        )
        .order_by(polymarket_markets.c.start_at, polymarket_markets.c.condition_id)
    ).mappings().all()
    return tuple(
        V2FeatureTarget(
            condition_id=str(row["condition_id"]),
            slug=str(row["slug"]),
            horizon_seconds=int(row["horizon_seconds"]),
            market_start_at=row["start_at"],
            market_end_at=row["end_at"],
            up_token_id=str(row["up_token_id"]),
            down_token_id=str(row["down_token_id"]),
        )
        for row in rows
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate immutable forward-only Gate A V2 feature snapshots offline"
    )
    parser.add_argument("--start", required=True, type=parse_datetime)
    parser.add_argument("--end", required=True, type=parse_datetime)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--database-url", default=None)
    parser.add_argument(
        "--preserve-existing",
        action="store_true",
        help="skip already-materialized immutable V2 feature keys before recomputation",
    )
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    if args.database_url:
        settings = settings.model_copy(update={"database_url": args.database_url})
    return settings


def _run(args: argparse.Namespace) -> V2FeatureGenerationStats:
    validate_window(args.start, args.end)
    settings = _settings(args)
    engine = create_engine(settings.database_url)
    metadata.create_all(engine)
    generated_at = datetime.now(UTC)
    with engine.begin() as connection:
        targets = load_v2_targets(connection, start=args.start, end=args.end)
        return generate_v2_features(
            connection,
            targets,
            generated_at=generated_at,
            preserve_existing=args.preserve_existing,
        )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        stats = _run(args)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(asdict(stats), indent=2, sort_keys=True))
    return 0
