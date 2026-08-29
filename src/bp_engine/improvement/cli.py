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
from bp_engine.improvement import service
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
    return parsed


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
        help="evaluate a registered experiment once a challenger adapter is installed",
    )
    evaluate.add_argument("--experiment-id", required=True)
    return parser


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
    finally:
        engine.dispose()
    raise RuntimeError(f"unsupported database command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "evaluate":
        _emit(
            {
                "ok": False,
                "command": "evaluate",
                "error": "challenger adapter not installed",
                "error_type": "RuntimeError",
            }
        )
        return 2

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
