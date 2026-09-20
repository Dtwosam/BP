from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import create_engine

from bp_engine.config import Settings
from bp_engine.v3_paper.report import build_v3_paper_report


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
        description="Report isolated frozen V3 paper-trading evidence"
    )
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--recent-limit", type=int, default=20)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    if args.database_url:
        settings = settings.model_copy(update={"database_url": args.database_url})
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        payload = build_v3_paper_report(engine, recent_limit=args.recent_limit)
    finally:
        engine.dispose()
    print(json.dumps(_json_value(payload), indent=2, sort_keys=True))
    return 0
