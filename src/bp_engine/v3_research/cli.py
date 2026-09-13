from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, create_engine

from bp_engine.config import Settings
from bp_engine.v3_research.exclusions import load_exclusion_manifest
from bp_engine.v3_research.plan import build_v3_gate_b_plan
from bp_engine.v3_research.readiness import assess_v3_gate_b_readiness


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


def _write_exclusive(path: str, payload: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _read_only(
    engine: Engine,
    operation: Callable[[Connection], dict[str, Any]],
) -> dict[str, Any]:
    with engine.connect() as connection:
        with connection.begin():
            if connection.dialect.name == "postgresql":
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            return operation(connection)


def _add_manifest_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--diagnosis-exclusions", required=True)
    parser.add_argument(
        "--consumed-v2-final-holdout-exclusions",
        required=True,
    )
    parser.add_argument("--as-of", type=_parse_datetime, required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the read-only V3 Gate B preregistration package"
    )
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--database-url", default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    readiness = subparsers.add_parser(
        "readiness",
        help="report outcome-blind V3 Gate B readiness",
    )
    _add_manifest_arguments(readiness)

    plan = subparsers.add_parser(
        "plan",
        help="freeze the preregistered feature-only V3 Gate B plan",
    )
    _add_manifest_arguments(plan)
    plan.add_argument("--output", required=True)

    return parser


def _load_manifests(args: argparse.Namespace):
    diagnosis = load_exclusion_manifest(
        args.diagnosis_exclusions,
        expected_kind="diagnosis",
    )
    consumed = load_exclusion_manifest(
        args.consumed_v2_final_holdout_exclusions,
        expected_kind="consumed_v2_final_holdout",
    )
    return diagnosis, consumed


def _readiness_summary(payload: dict[str, Any]) -> dict[str, Any]:
    coverage = payload["coverage"]
    return {
        "ready": bool(payload["ready"]),
        "blocking_reasons": list(payload["blocking_reasons"]),
        "market_count": int(coverage["market_count"]),
        "coverage_input_sha256": coverage["coverage_input_sha256"],
        "diagnosis_exclusion_sha256": payload["diagnosis_exclusion_sha256"],
        "consumed_v2_final_holdout_exclusion_sha256": (
            payload["consumed_v2_final_holdout_exclusion_sha256"]
        ),
        "readiness_input_sha256": payload["readiness_input_sha256"],
    }


def _plan_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "research_plan_version": payload["research_plan_version"],
        "market_count": int(payload["market_count"]),
        "ordinary_fold_count": len(payload["folds"]),
        "final_holdout_market_count": len(payload["final"]["holdout_condition_ids"]),
        "config_sha256": payload["config_sha256"],
        "feature_manifest_sha256": payload["feature_manifest_sha256"],
        "plan_sha256": payload["plan_sha256"],
        "readiness_input_sha256": payload["readiness_input_sha256"],
        "diagnosis_exclusion_sha256": payload["diagnosis_exclusion_sha256"],
        "consumed_v2_final_holdout_exclusion_sha256": (
            payload["consumed_v2_final_holdout_exclusion_sha256"]
        ),
        "readiness_blocking_reasons": [],
    }


def _run(args: argparse.Namespace) -> dict[str, Any]:
    diagnosis, consumed = _load_manifests(args)
    settings = _settings(args)
    engine = create_engine(settings.database_url)

    if args.command == "readiness":
        payload = _read_only(
            engine,
            lambda connection: assess_v3_gate_b_readiness(
                connection,
                as_of=args.as_of,
                diagnosis_exclusions=diagnosis,
                consumed_v2_final_holdout_exclusions=consumed,
            ),
        )
        return _readiness_summary(payload)

    if args.command == "plan":
        payload = _read_only(
            engine,
            lambda connection: build_v3_gate_b_plan(
                connection,
                as_of=args.as_of,
                diagnosis_exclusions=diagnosis,
                consumed_v2_final_holdout_exclusions=consumed,
            ),
        )
        _write_exclusive(args.output, payload)
        return _plan_summary(payload)

    raise ValueError(f"unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = _run(args)
    except (FileExistsError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0
