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
from bp_engine.v4_research.holdout import evaluate_v4_gate_b_holdout
from bp_engine.v4_research.plan import build_v4_gate_b_plan
from bp_engine.v4_research.readiness import assess_v4_gate_b_readiness
from bp_engine.v4_research.service import (
    V4PreparedSelection,
    finalize_v4_selection,
    prepare_v4_gate_b,
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


def _read_json(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON artifact must contain an object")
    return payload


def _library_version(family: str) -> str:
    if family == "xgboost":
        try:
            return version("xgboost-cpu")
        except PackageNotFoundError:
            return version("xgboost")
    if family == "logistic":
        return version("scikit-learn")
    return version("joblib")


def _write_model_exclusive(
    path: str,
    prepared: V4PreparedSelection,
) -> dict[str, Any]:
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


def _load_model_verified(
    path: str,
    selection: dict[str, Any],
) -> tuple[dict[str, Any], str, int]:
    destination = Path(path)
    payload = destination.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    artifact = selection.get("model_artifact")
    if not isinstance(artifact, dict):
        raise ValueError("selection model artifact is missing")
    if artifact.get("sha256") != digest:
        raise ValueError("model artifact SHA-256 mismatch")
    if artifact.get("file_name") != destination.name:
        raise ValueError("model artifact filename mismatch")
    if int(artifact.get("size_bytes", -1)) != len(payload):
        raise ValueError("model artifact size mismatch")
    value = joblib.load(destination)
    if not isinstance(value, dict):
        raise ValueError("model artifact must contain a mapping")
    return value, digest, len(payload)


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

    prepare = subparsers.add_parser(
        "prepare",
        help="fit frozen V4 candidates using non-holdout labels only",
    )
    prepare.add_argument("--plan", required=True)
    prepare.add_argument("--output", required=True)
    prepare.add_argument("--model-output", required=True)

    holdout = subparsers.add_parser(
        "evaluate-holdout",
        help="evaluate the frozen V4 policy on the final holdout exactly once",
    )
    holdout.add_argument("--plan", required=True)
    holdout.add_argument("--selection", required=True)
    holdout.add_argument("--model", required=True)
    holdout.add_argument("--output", required=True)

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
        if args.command == "prepare":
            if Path(args.output).exists():
                raise FileExistsError(args.output)
            if Path(args.model_output).exists():
                raise FileExistsError(args.model_output)
            plan = _read_json(args.plan)
            prepared = _read_only(
                engine,
                lambda connection: prepare_v4_gate_b(
                    connection,
                    plan=plan,
                ),
            )
            model_artifact = _write_model_exclusive(
                args.model_output,
                prepared,
            )
            payload = finalize_v4_selection(
                prepared,
                model_artifact=model_artifact,
            )
            _write_exclusive(args.output, payload)
            selected = payload["selected_forecast"]
            edge = payload["edge_selection"]
            return {
                "research_plan_version": payload["research_plan_version"],
                "stage": payload["stage"],
                "plan_sha256": payload["plan_sha256"],
                "dataset_sha256_non_holdout": payload[
                    "dataset_sha256_non_holdout"
                ],
                "selected_forecast_candidate": selected["candidate"],
                "selected_offset_seconds": selected["offset_seconds"],
                "selected_calibration_method": selected["calibration_method"],
                "selected_edge_policy": edge["policy"],
                "selected_min_edge": edge["min_edge"],
                "selection_sha256": payload["selection_sha256"],
                "model_artifact_sha256": model_artifact["sha256"],
                "labels_read_non_holdout": True,
                "holdout_labels_read": False,
                "holdout_evaluated": False,
                "training_performed": True,
                "policy_selected": True,
                "automatic_promotion": False,
                "activation_performed": False,
            }

        if args.command == "evaluate-holdout":
            plan_path = Path(args.plan)
            selection_path = Path(args.selection)
            model_path = Path(args.model)
            output_path = Path(args.output)
            evidence_dir = plan_path.parent
            if (
                selection_path.parent != evidence_dir
                or model_path.parent != evidence_dir
                or output_path.parent != evidence_dir
            ):
                raise ValueError(
                    "V4 holdout artifacts must share the frozen evidence directory"
                )
            if output_path.name != "holdout.json":
                raise ValueError(
                    "one-shot V4 holdout output must be named holdout.json"
                )
            if output_path.exists():
                raise FileExistsError(args.output)

            plan = _read_json(args.plan)
            selection = _read_json(args.selection)
            model_bundle, model_sha256, model_size_bytes = _load_model_verified(
                args.model,
                selection,
            )
            payload = _read_only(
                engine,
                lambda connection: evaluate_v4_gate_b_holdout(
                    connection,
                    plan=plan,
                    selection=selection,
                    model_bundle=model_bundle,
                    model_sha256=model_sha256,
                    model_file_name=model_path.name,
                    model_size_bytes=model_size_bytes,
                ),
            )
            _write_exclusive(args.output, payload)
            forecast = payload["holdout_evaluation"]["forecast"]["metrics"]
            economics = payload["holdout_evaluation"]["economics"]["overall"]
            frozen = payload["frozen_selection"]
            return {
                "research_plan_version": payload["research_plan_version"],
                "stage": payload["stage"],
                "plan_sha256": payload["plan_sha256"],
                "selection_sha256": payload["selection_sha256"],
                "model_artifact_sha256": payload["model_artifact_sha256"],
                "holdout_dataset_sha256": payload["holdout_dataset_sha256"],
                "holdout_market_count": int(forecast["market_count"]),
                "selected_forecast_candidate": frozen["candidate"],
                "selected_offset_seconds": int(frozen["offset_seconds"]),
                "selected_calibration_method": frozen["calibration_method"],
                "selected_edge_policy": frozen["edge_policy"],
                "selected_min_edge": frozen["min_edge"],
                "holdout_accuracy": forecast["accuracy"],
                "holdout_log_loss": forecast["log_loss"],
                "holdout_brier_score": forecast["brier_score"],
                "holdout_trade_count": int(economics["trade_count"]),
                "holdout_realized_pnl_after_assumed_costs": economics[
                    "realized_pnl_after_assumed_costs"
                ],
                "holdout_evidence_sha256": payload["holdout_evidence_sha256"],
                "holdout_labels_read": True,
                "holdout_evaluated_once": True,
                "model_refit_performed": False,
                "threshold_tuning_performed": False,
                "automatic_promotion": False,
                "paper_activation_performed": False,
                "live_trading_enabled": False,
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
