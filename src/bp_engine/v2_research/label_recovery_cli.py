from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine

from bp_engine.config import Settings
from bp_engine.live_prediction.service import ensure_live_prediction_safety
from bp_engine.polymarket.gamma import GammaClient
from bp_engine.v2_research.label_recovery import (
    audit_gate_b_non_holdout_labels,
    recover_gate_b_non_holdout_labels,
)


def _add_environment_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--plan", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit or recover canonical labels for an existing frozen Phase 14 "
            "V2 Gate B non-holdout plan in RESEARCH mode only"
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser(
        "audit",
        help="read-only audit of canonical labels for the frozen non-holdout plan",
    )
    _add_environment_arguments(audit)

    recover = subparsers.add_parser(
        "recover",
        help="append missing canonical Gamma snapshots and labels for frozen non-holdout IDs",
    )
    _add_environment_arguments(recover)
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    if args.database_url:
        settings = settings.model_copy(update={"database_url": args.database_url})
    return settings


def ensure_label_recovery_safety(settings: Settings) -> None:
    ensure_live_prediction_safety(settings)


def _load_plan(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("plan must contain a JSON object")
    return payload


def _audit(engine: Engine, *, plan: dict[str, Any]) -> dict[str, Any]:
    with engine.connect() as connection:
        with connection.begin():
            if connection.dialect.name == "postgresql":
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            return audit_gate_b_non_holdout_labels(connection, plan=plan)


async def _recover(engine: Engine, *, plan: dict[str, Any]) -> dict[str, Any]:
    return await recover_gate_b_non_holdout_labels(
        engine,
        GammaClient(),
        plan=plan,
    )


def _run(args: argparse.Namespace) -> dict[str, Any]:
    settings = _settings(args)
    ensure_label_recovery_safety(settings)
    plan = _load_plan(args.plan)
    engine = create_engine(settings.database_url)
    try:
        if args.command == "audit":
            return _audit(engine, plan=plan)
        if args.command == "recover":
            return asyncio.run(_recover(engine, plan=plan))
        raise AssertionError(f"unsupported command: {args.command}")
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = _run(args)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
