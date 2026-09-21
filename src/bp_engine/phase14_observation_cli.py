from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url

from bp_engine.config import Settings
from bp_engine.phase14_observation import build_phase14_observation_report


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _create_observation_engine(database_url: str) -> Engine:
    engine_kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if make_url(database_url).get_backend_name() == "postgresql":
        engine_kwargs["connect_args"] = {
            "options": "-c default_transaction_read_only=on",
        }
    return create_engine(database_url, **engine_kwargs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Report read-only Phase 14 V3 paper, V4 coverage, and storage observation evidence"
        )
    )
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--recent-limit", type=int, default=20)
    parser.add_argument("--storage-path", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    if args.database_url:
        settings = settings.model_copy(update={"database_url": args.database_url})

    engine = _create_observation_engine(settings.database_url)
    try:
        payload = build_phase14_observation_report(
            engine,
            settings,
            recent_limit=args.recent_limit,
            storage_path=args.storage_path,
        )
    finally:
        engine.dispose()

    print(json.dumps(_json_value(payload), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
