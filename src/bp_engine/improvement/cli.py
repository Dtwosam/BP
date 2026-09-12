from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine

from bp_engine.config import get_settings
from bp_engine.improvement import adaptive, service
from bp_engine.improvement.models import (
    ChampionRef,
    ChangeFamily,
    ImprovementExperimentSpec,
    PromotionDecision,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_datetime(value: object, *, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO-8601 string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _stored_utc(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item) for item in value]
    if hasattr(value, "__dict__"):
        return _json_value(vars(value))
    raise TypeError(f"unsupported JSON output type: {type(value).__name__}")


def _emit(payload: Mapping[str, Any]) -> None:
    print(
        json.dumps(
            _json_value(payload),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )


def _load_experiment_spec(path: Path) -> ImprovementExperimentSpec:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("experiment spec must be a JSON object")

    champion_payload = payload["champion"]
    if not isinstance(champion_payload, dict):
        raise ValueError("champion must be a JSON object")
    champion = ChampionRef(**champion_payload)

    return ImprovementExperimentSpec.build(
        experiment_version=str(payload["experiment_version"]),
        hypothesis=str(payload["hypothesis"]),
        horizon_seconds=int(payload["horizon_seconds"]),
        change_family=ChangeFamily(str(payload["change_family"])),
        champion=champion,
        challenger=dict(payload["challenger"]),
        source_versions=dict(payload["source_versions"]),
        research_start=_parse_datetime(payload["research_start"], name="research_start"),
        research_end=_parse_datetime(payload["research_end"], name="research_end"),
        selection_policy=dict(payload["selection_policy"]),
        confirmation_policy=dict(payload["confirmation_policy"]),
        cost_assumptions=dict(payload["cost_assumptions"]),
        primary_metric=str(payload["primary_metric"]),
        guardrail_metrics=tuple(str(item) for item in payload["guardrail_metrics"]),
        legacy_confirmation_identifiers=tuple(
            str(item) for item in payload["legacy_confirmation_identifiers"]
        ),
        created_at=_parse_datetime(payload["created_at"], name="created_at"),
    )


def _add_adaptive_stream_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--horizon-seconds", type=int, required=True)
    parser.add_argument("--feature-version", required=True)
    parser.add_argument("--label-version", required=True)
    parser.add_argument("--cutoff-at", required=True)
    parser.add_argument("--bootstrap-since-at")
    parser.add_argument(
        "--trigger-count",
        type=int,
        default=adaptive.DEFAULT_ADAPTIVE_TRIGGER_COUNT,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 13 improvement-loop CLI — research/paper only; "
            "never submits live trading requests"
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    register = subparsers.add_parser(
        "register",
        help="register an immutable research experiment",
    )
    register.add_argument("--spec", type=Path, required=True)

    report = subparsers.add_parser(
        "report",
        help="read immutable experiment, evaluation, and decision history",
    )
    report.add_argument("--experiment-id", required=True)

    decide = subparsers.add_parser(
        "decide",
        help="append a deliberate research promotion decision",
    )
    decide.add_argument("--evaluation-id", required=True)
    decide.add_argument(
        "--decision",
        required=True,
        choices=tuple(decision.value for decision in PromotionDecision),
    )
    decide.add_argument("--rationale", required=True)

    evaluate = subparsers.add_parser(
        "evaluate",
        help="evaluate a registered research challenger and store the immutable result",
    )
    evaluate.add_argument("--experiment-id", required=True)

    readiness = subparsers.add_parser(
        "adaptive-readiness",
        help="read adaptive-learning readiness without training or activation",
    )
    _add_adaptive_stream_arguments(readiness)

    train = subparsers.add_parser(
        "adaptive-train",
        help="run one research-only adaptive training cycle when readiness passes",
    )
    _add_adaptive_stream_arguments(train)
    train.add_argument("--training-start-at", required=True)
    train.add_argument("--output-dir", type=Path, required=True)
    train.add_argument("--min-markets", type=int, required=True)
    return parser


def _adaptive_since_at(connection, args: argparse.Namespace) -> datetime:
    repository = adaptive.AdaptiveLearningCycleRepository()
    latest = repository.latest_completed(
        connection,
        horizon_seconds=args.horizon_seconds,
        feature_version=args.feature_version,
        label_version=args.label_version,
    )
    if latest is None:
        if args.bootstrap_since_at is None:
            raise adaptive.AdaptiveLearningError(
                "bootstrap_since_at is required for the first adaptive learning cycle"
            )
        return _parse_datetime(args.bootstrap_since_at, name="bootstrap_since_at")
    if args.bootstrap_since_at is not None:
        raise adaptive.AdaptiveLearningError(
            "bootstrap_since_at must be omitted after an adaptive learning cycle exists"
        )
    return _stored_utc(latest["cutoff_at"], name="cutoff_at")


def _run_database_command(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            if args.command == "register":
                spec = args.experiment_spec
                result = service.register_experiment(connection, spec)
                return {
                    "ok": True,
                    "command": "register",
                    "experiment_id": spec.experiment_id,
                    "created": result.created,
                    "existing": result.existing,
                }
            if args.command == "report":
                report = service.get_experiment_report(connection, args.experiment_id)
                return {"ok": True, "command": "report", "report": report}
            if args.command == "decide":
                record = service.record_decision(
                    connection,
                    evaluation_id=args.evaluation_id,
                    decision=PromotionDecision(args.decision),
                    rationale=args.rationale,
                    created_at=_utc_now(),
                )
                return {"ok": True, "command": "decide", "decision": record}
            if args.command == "evaluate":
                report = service.evaluate_experiment(
                    connection,
                    experiment_id=args.experiment_id,
                    created_at=_utc_now(),
                )
                return {"ok": True, "command": "evaluate", "evaluation": report}
            if args.command == "adaptive-readiness":
                since_at = _adaptive_since_at(connection, args)
                readiness = adaptive.build_adaptive_readiness_report(
                    connection,
                    horizon_seconds=args.horizon_seconds,
                    feature_version=args.feature_version,
                    label_version=args.label_version,
                    since_at=since_at,
                    cutoff_at=_parse_datetime(args.cutoff_at, name="cutoff_at"),
                    trigger_count=args.trigger_count,
                )
                return {
                    "ok": True,
                    "command": "adaptive-readiness",
                    "readiness": readiness,
                }
            if args.command == "adaptive-train":
                readiness, training, cycle = adaptive.run_adaptive_training_cycle(
                    connection,
                    horizon_seconds=args.horizon_seconds,
                    feature_version=args.feature_version,
                    label_version=args.label_version,
                    bootstrap_since_at=(
                        _parse_datetime(
                            args.bootstrap_since_at,
                            name="bootstrap_since_at",
                        )
                        if args.bootstrap_since_at is not None
                        else None
                    ),
                    cutoff_at=_parse_datetime(args.cutoff_at, name="cutoff_at"),
                    training_start_at=_parse_datetime(
                        args.training_start_at,
                        name="training_start_at",
                    ),
                    output_dir=args.output_dir,
                    min_markets=args.min_markets,
                    trigger_count=args.trigger_count,
                    created_at=_utc_now(),
                )
                return {
                    "ok": True,
                    "command": "adaptive-train",
                    "readiness": readiness,
                    "training": {
                        "run_id": training.run_id,
                        "semantic_sha256": training.semantic_sha256,
                    },
                    "cycle": {
                        "cycle_id": cycle.cycle_id,
                        "semantic_sha256": cycle.semantic_sha256,
                    },
                }
    finally:
        engine.dispose()
    raise RuntimeError(f"unsupported database command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.command == "register":
            args.experiment_spec = _load_experiment_spec(args.spec)
        result = _run_database_command(args)
    except Exception as exc:
        _emit(
            {
                "ok": False,
                "command": args.command,
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
        )
        return 2

    _emit(result)
    return 0
