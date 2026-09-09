from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, create_engine

from bp_engine.config import Settings
from bp_engine.v2_research.models import GateBPlanConfig, GateBResearchConfig
from bp_engine.v2_research.plan import assess_gate_b_readiness, build_gate_b_plan
from bp_engine.v2_research.service import (
    evaluate_gate_b_holdout,
    prepare_gate_b,
)


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    if args.database_url:
        settings = settings.model_copy(update={"database_url": args.database_url})
    return settings


def _load_json(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _write_exclusive(path: str, payload: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _read_only(engine: Engine, operation: Callable[[Connection], dict[str, Any]]) -> dict[str, Any]:
    with engine.connect() as connection:
        with connection.begin():
            if connection.dialect.name == "postgresql":
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            return operation(connection)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the read-only Phase 14 V2 Gate B research package"
    )
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--database-url", default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "readiness",
        help="report feature-only Gate B readiness without writing artifacts",
    )

    plan = subparsers.add_parser(
        "plan",
        help="build the unlabeled chronological Gate B partition plan",
    )
    plan.add_argument("--output", required=True)
    plan.add_argument("--train-hours", type=float, default=8)
    plan.add_argument("--validation-hours", type=float, default=2)
    plan.add_argument("--test-hours", type=float, default=2)
    plan.add_argument("--step-hours", type=float, default=2)
    plan.add_argument("--final-holdout-hours", type=float, default=2)
    plan.add_argument("--embargo-markets", type=int, default=1)
    plan.add_argument("--min-train-markets", type=int, default=24)
    plan.add_argument("--min-validation-markets", type=int, default=6)
    plan.add_argument("--min-test-markets", type=int, default=6)
    plan.add_argument("--fee-rate", type=float, default=0.07)
    plan.add_argument("--slippage-buffer", type=float, default=0.01)
    plan.add_argument(
        "--min-edge",
        action="append",
        type=float,
        default=None,
        help="repeat to replace the pre-labeled V2 Gate B min-edge candidate grid",
    )
    plan.add_argument("--min-validation-trades", type=int, default=8)
    plan.add_argument("--min-train-eligible-markets", type=int, default=24)
    plan.add_argument("--min-validation-eligible-markets", type=int, default=8)

    prepare = subparsers.add_parser(
        "prepare",
        help="select/freeze V2 policy on train+validation and evaluate ordinary test only",
    )
    prepare.add_argument("--plan", required=True)
    prepare.add_argument("--output", required=True)

    holdout = subparsers.add_parser(
        "evaluate-holdout",
        help="evaluate the already-frozen final policy on the final holdout exactly once",
    )
    holdout.add_argument("--plan", required=True)
    holdout.add_argument("--selection", required=True)
    holdout.add_argument("--output", required=True)

    return parser


def _run(args: argparse.Namespace) -> dict[str, Any]:
    settings = _settings(args)
    engine = create_engine(settings.database_url)

    if args.command == "readiness":
        payload = _read_only(
            engine,
            lambda connection: assess_gate_b_readiness(
                connection,
                GateBPlanConfig(),
                GateBResearchConfig(),
            ),
        )
    elif args.command == "plan":
        config = GateBPlanConfig(
            train_duration=timedelta(hours=args.train_hours),
            validation_duration=timedelta(hours=args.validation_hours),
            test_duration=timedelta(hours=args.test_hours),
            step_duration=timedelta(hours=args.step_hours),
            final_holdout_duration=timedelta(hours=args.final_holdout_hours),
            embargo_markets=args.embargo_markets,
            min_train_markets=args.min_train_markets,
            min_validation_markets=args.min_validation_markets,
            min_test_markets=args.min_test_markets,
        )
        research_config = GateBResearchConfig(
            fee_rate=args.fee_rate,
            slippage_buffer=args.slippage_buffer,
            min_edge_grid=(
                tuple(args.min_edge)
                if args.min_edge is not None
                else GateBResearchConfig().min_edge_grid
            ),
            min_validation_trades=args.min_validation_trades,
            min_train_eligible_markets=args.min_train_eligible_markets,
            min_validation_eligible_markets=args.min_validation_eligible_markets,
        )
        payload = _read_only(
            engine,
            lambda connection: build_gate_b_plan(
                connection,
                config,
                research_config,
            ),
        )
    elif args.command == "prepare":
        plan = _load_json(args.plan)
        payload = _read_only(
            engine,
            lambda connection: prepare_gate_b(
                connection,
                plan=plan,
            ),
        )
    elif args.command == "evaluate-holdout":
        plan = _load_json(args.plan)
        selection = _load_json(args.selection)
        payload = _read_only(
            engine,
            lambda connection: evaluate_gate_b_holdout(
                connection,
                plan=plan,
                selection=selection,
            ),
        )
    else:
        raise ValueError(f"unsupported command: {args.command}")

    if args.command != "readiness":
        _write_exclusive(args.output, payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = _run(args)
    except (FileExistsError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0
