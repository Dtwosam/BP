from __future__ import annotations

import argparse
import json

from sqlalchemy import Engine, create_engine

from bp_engine.config import Settings
from bp_engine.features.v2_coverage import build_v2_coverage_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Report Gate A V2 feature coverage read-only")
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--database-url", default=None)
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    if args.database_url:
        settings = settings.model_copy(update={"database_url": args.database_url})
    return settings


def run_read_only_report(engine: Engine) -> dict[str, object]:
    with engine.connect() as connection:
        with connection.begin():
            if connection.dialect.name == "postgresql":
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            return build_v2_coverage_report(connection)


def _run(args: argparse.Namespace) -> dict[str, object]:
    settings = _settings(args)
    engine = create_engine(settings.database_url)
    return run_read_only_report(engine)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = _run(args)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0
