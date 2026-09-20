from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import joblib
from sqlalchemy import Connection, Engine, create_engine

from bp_engine.config import Settings
from bp_engine.v3_research.plan import build_v3_gate_b_plan
from bp_engine.v3_research.readiness import assess_v3_gate_b_readiness
from bp_engine.v3_research.service import (
    V3PreparedSelection,
    finalize_v3_selection,
    prepare_v3_gate_b,
)


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


def _library_version(family: str) -> str:
    if family == "xgboost":
        try:
            return version("xgboost-cpu")
        except PackageNotFoundError:
            return version("xgboost")
    if family == "logistic":
        return version("scikit-learn")
    return version("joblib")


def _write_model_exclusive(path: str, prepared: V3PreparedSelection) -> dict[str, Any]:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as handle:
        joblib.dump(prepared.model_bundle, handle)
    payload = destination.read_bytes()
    family = str(prepared.model_bundle["family"])
    return {
        "candidate": str(prepared.model_bundle["candidate"]),
        "family": family,
        "file_name": destination.name,
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "library_version": _library_version(family),
    }


def _read_json(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON artifact must contain an object")
    return payload


def _read_only(
    engine: Engine,
    operation: Callable[[Connection], Any],
) -> Any:
    with engine.connect() as connection:
        with connection.begin():
            if connection.dialect.name == "postgresql":
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            return operation(connection)


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
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
    _add_common_arguments(readiness)

    plan = subparsers.add_parser(
        "plan",
        help="freeze the preregistered feature-only V3 Gate B plan",
    )
    _add_common_arguments(plan)
    plan.add_argument("--output", required=True)

    prepare = subparsers.add_parser(
        "prepare",
        help="fit ordinary V3 candidates and freeze non-holdout selection",
    )
    prepare.add_argument("--plan", required=True)
    prepare.add_argument("--output", required=True)
    prepare.add_argument("--model-output", required=True)

    return parser


def _readiness_summary(payload: dict[str, Any]) -> dict[str, Any]:
    coverage = payload["coverage"]
    return {
        "ready": bool(payload["ready"]),
        "blocking_reasons": list(payload["blocking_reasons"]),
        "market_count": int(coverage["market_count"]),
        "coverage_input_sha256": coverage["coverage_input_sha256"],
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
        "readiness_blocking_reasons": [],
    }


def _prepare_summary(payload: dict[str, Any]) -> dict[str, Any]:
    selected = payload["final"]["selected_forecast"]
    edge = payload["final"]["edge_selection"]
    artifact = payload["model_artifact"]
    return {
        "research_plan_version": payload["research_plan_version"],
        "stage": payload["stage"],
        "plan_sha256": payload["plan_sha256"],
        "dataset_sha256_non_holdout": payload["dataset_sha256_non_holdout"],
        "ordinary_fold_count": len(payload["folds"]),
        "ordinary_validation_economics_passed": bool(
            payload["ordinary_validation_economics_passed"]
        ),
        "selected_forecast_candidate": selected["candidate"],
        "selected_offset_seconds": int(selected["offset_seconds"]),
        "selected_edge_policy": edge["policy"],
        "selected_min_edge": edge["min_edge"],
        "model_artifact_sha256": artifact["sha256"],
        "selection_sha256": payload["selection_sha256"],
        "holdout_labels_read": False,
        "holdout_evaluated": False,
    }


def _run(args: argparse.Namespace) -> dict[str, Any]:
    settings = _settings(args)
    engine = create_engine(settings.database_url)

    if args.command == "readiness":
        payload = _read_only(
            engine,
            lambda connection: assess_v3_gate_b_readiness(
                connection,
                as_of=args.as_of,
            ),
        )
        return _readiness_summary(payload)

    if args.command == "plan":
        payload = _read_only(
            engine,
            lambda connection: build_v3_gate_b_plan(
                connection,
                as_of=args.as_of,
            ),
        )
        _write_exclusive(args.output, payload)
        return _plan_summary(payload)

    if args.command == "prepare":
        if Path(args.output).exists():
            raise FileExistsError(args.output)
        if Path(args.model_output).exists():
            raise FileExistsError(args.model_output)
        plan = _read_json(args.plan)
        prepared = _read_only(
            engine,
            lambda connection: prepare_v3_gate_b(
                connection,
                plan=plan,
            ),
        )
        model_artifact = _write_model_exclusive(args.model_output, prepared)
        payload = finalize_v3_selection(
            prepared,
            model_artifact=model_artifact,
        )
        _write_exclusive(args.output, payload)
        return _prepare_summary(payload)

    raise ValueError(f"unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = _run(args)
    except (FileExistsError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0
