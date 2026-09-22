from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, create_engine

from bp_engine.config import Settings
from bp_engine.v4_research.plan import build_v4_gate_b_plan
from bp_engine.v4_research.readiness import assess_v4_gate_b_readiness


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("as-of must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("as-of must be timezone-aware")
    return parsed.astimezone(UTC)


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    if args.database_url:
        settings = settings.model_copy(update={"database_url": args.database_url})
    return settings


def _read_only(
    engine: Engine,
    operation: Callable[[Connection], dict[str, Any]],
) -> dict[str, Any]:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            if connection.dialect.name == "postgresql":
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            return operation(connection)
        finally:
            transaction.rollback()


def _write_exclusive(path: str, payload: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Outcome-blind V4 Gate B readiness and planning"
    )
    parser.add_argument("--env-file")
    parser.add_argument("--database-url")
    subparsers = parser.add_subparsers(dest="command", required=True)

    readiness = subparsers.add_parser("readiness")
    readiness.add_argument("--as-of", type=_parse_datetime, required=True)

    plan = subparsers.add_parser("plan")
    plan.add_argument("--as-of", type=_parse_datetime, required=True)
    plan.add_argument("--output", required=True)

    return parser


def _run(args: argparse.Namespace) -> dict[str, Any]:
    settings = _settings(args)
    engine = create_engine(settings.database_url)
    try:
        if args.command == "readiness":
            return _read_only(
                engine,
                lambda connection: assess_v4_gate_b_readiness(
                    connection,
                    as_of=args.as_of,
                ),
            )
        if args.command == "plan":
            payload = _read_only(
                engine,
                lambda connection: build_v4_gate_b_plan(
                    connection,
                    as_of=args.as_of,
                ),
            )
            _write_exclusive(args.output, payload)
            return {
                "research_plan_version": payload["research_plan_version"],
                "plan_sha256": payload["plan_sha256"],
                "market_count": payload["market_count"],
                "labels_read": payload["labels_read"],
                "training_performed": payload["training_performed"],
                "policy_selected": payload["policy_selected"],
                "final_holdout_evaluated": payload["final_holdout_evaluated"],
            }
    finally:
        engine.dispose()
    raise ValueError(f"unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = _run(args)
    except (FileExistsError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
