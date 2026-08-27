from __future__ import annotations

import argparse
import asyncio
import json
import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, create_engine, select

from bp_engine.backfill.polymarket_prices import PolymarketPriceHistoryClient
from bp_engine.config import Settings
from bp_engine.features.hashing import canonical_hash
from bp_engine.live_prediction.models import (
    LIVE_PREDICTION_VERSION,
    LivePolicySpec,
    LivePrediction,
    LivePredictionEvaluation,
)
from bp_engine.live_prediction.policy import load_live_policy
from bp_engine.live_prediction.repository import _ledger_numeric_equal
from bp_engine.live_prediction.service import (
    LivePredictionService,
    ensure_live_prediction_safety,
)
from bp_engine.storage.schema import (
    live_prediction_evaluations,
    live_predictions,
    polymarket_markets,
)

ACCEPTED_POLICY_SOURCES: dict[int, tuple[str, str]] = {
    300: (
        "phase9-300-c9f0e00eb7836af08008c66909f8f179",
        "c9f0e00eb7836af08008c66909f8f179f03089413426508469353c75bcbcae24",
    ),
    900: (
        "phase9-900-15c234f25588b23cce73a12f87a2e2ea",
        "15c234f25588b23cce73a12f87a2e2ea9087490055f203f22f183594b4bcfacd",
    ),
}
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_MAX_LATENESS_SECONDS = 10
_HASH_RECOVERY_MAX_ATTEMPTS = 4096
_HASH_RECOVERY_FLOAT_STEPS = (_HASH_RECOVERY_MAX_ATTEMPTS - 1) // 2
_HASH_RECOVERY_NUMERIC_FIELDS = (
    "training_prior",
    "raw_probability",
    "market_probability",
    "up_best_bid",
    "up_best_ask",
    "down_best_bid",
    "down_best_ask",
)


def _add_environment_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--database-url", default=None)


def _add_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source-calibration-run-id",
        action="append",
        required=True,
        help="repeat for each immutable accepted Phase 9 policy source",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run or inspect the research-only Phase 10 live predictor"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="run the prospective research-only prediction service",
    )
    _add_source_arguments(run_parser)
    _add_environment_arguments(run_parser)
    run_parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
    )
    run_parser.add_argument(
        "--max-lateness-seconds",
        type=int,
        default=DEFAULT_MAX_LATENESS_SECONDS,
    )

    report_parser = subparsers.add_parser(
        "report",
        help="emit a read-only live prediction integrity report",
    )
    _add_source_arguments(report_parser)
    _add_environment_arguments(report_parser)
    report_parser.add_argument(
        "--max-lateness-seconds",
        type=int,
        default=DEFAULT_MAX_LATENESS_SECONDS,
    )
    return parser


def validate_source_run_ids(source_run_ids: tuple[str, ...]) -> tuple[str, ...]:
    if not source_run_ids:
        raise ValueError("at least one source calibration run id is required")
    normalized = tuple(run_id.strip() for run_id in source_run_ids)
    if any(not run_id for run_id in normalized):
        raise ValueError("source calibration run ids must not be empty")
    if len(set(normalized)) != len(normalized):
        raise ValueError("source calibration run ids must be unique")
    return normalized


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    if args.database_url:
        settings = settings.model_copy(update={"database_url": args.database_url})
    return settings


def _horizon_seconds(value: str) -> int:
    rendered = value.strip().lower()
    if not rendered.endswith("m"):
        raise ValueError(f"active horizon must use minute notation: {value!r}")
    try:
        minutes = int(rendered[:-1])
    except ValueError as exc:
        raise ValueError(f"invalid active horizon: {value!r}") from exc
    if minutes <= 0:
        raise ValueError(f"active horizon must be positive: {value!r}")
    return minutes * 60


def _enabled_horizons(settings: Settings) -> tuple[int, ...]:
    horizons = tuple(_horizon_seconds(value) for value in settings.active_horizons)
    if not horizons:
        raise ValueError("at least one active horizon is required")
    if len(set(horizons)) != len(horizons):
        raise ValueError("active horizons must be unique")
    unsupported = sorted(set(horizons) - set(ACCEPTED_POLICY_SOURCES))
    if unsupported:
        raise ValueError(
            "Phase 10 V1 active horizons must be accepted verified horizons: "
            f"{unsupported}"
        )
    return horizons


def load_runtime_policies(
    connection: Connection,
    *,
    settings: Settings,
    source_run_ids: tuple[str, ...],
) -> dict[int, LivePolicySpec]:
    run_ids = validate_source_run_ids(source_run_ids)
    enabled = _enabled_horizons(settings)
    policies: dict[int, LivePolicySpec] = {}
    for run_id in run_ids:
        policy = load_live_policy(connection, run_id)
        horizon_seconds = policy.horizon_seconds
        accepted = ACCEPTED_POLICY_SOURCES.get(horizon_seconds)
        if accepted is None:
            raise ValueError(
                f"unsupported Phase 10 V1 policy horizon: {horizon_seconds}"
            )
        accepted_run_id, accepted_semantic = accepted
        if run_id != accepted_run_id:
            raise ValueError(
                "source calibration run is not the frozen Phase 10 V1 source for "
                f"horizon={horizon_seconds}: {run_id}"
            )
        if policy.source_calibration_semantic_sha256 != accepted_semantic:
            raise ValueError(
                "source calibration semantic does not match the frozen Phase 10 V1 source "
                f"for horizon={horizon_seconds}"
            )
        if horizon_seconds in policies:
            raise ValueError(
                f"exactly one policy source is required for horizon={horizon_seconds}"
            )
        policies[horizon_seconds] = policy

    if set(policies) != set(enabled):
        raise ValueError(
            "exactly one accepted policy source is required for each active horizon; "
            f"active={sorted(enabled)} supplied={sorted(policies)}"
        )
    return policies


def _stored_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _require_aware_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _semantic_values(
    row: Any,
    record_type: type[LivePrediction] | type[LivePredictionEvaluation],
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field in fields(record_type):
        if field.name == "semantic_sha256":
            continue
        value = row[field.name]
        if isinstance(value, datetime):
            value = _stored_utc(value)
        elif isinstance(value, Decimal):
            value = float(value)
        values[field.name] = value
    return values


def _ledger_float_candidates(stored: Any) -> tuple[float, ...]:
    if stored is None or isinstance(stored, bool):
        return ()
    try:
        center = float(stored)
    except (TypeError, ValueError, OverflowError):
        return ()
    if not math.isfinite(center) or not _ledger_numeric_equal(stored, center):
        return ()

    candidates: list[float] = []
    seen: set[str] = set()

    def add(candidate: float) -> bool:
        if not math.isfinite(candidate) or not _ledger_numeric_equal(stored, candidate):
            return False
        key = candidate.hex()
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)
        return True

    add(center)
    if center == 0.0:
        add(-0.0)

    lower = center
    upper = center
    lower_open = True
    upper_open = True
    for _ in range(_HASH_RECOVERY_FLOAT_STEPS):
        if lower_open:
            lower = math.nextafter(lower, -math.inf)
            lower_open = add(lower)
        if upper_open:
            upper = math.nextafter(upper, math.inf)
            upper_open = add(upper)
        if not lower_open and not upper_open:
            break
    return tuple(candidates)


def _recover_calibrated_probability(
    row: Any,
    *,
    side: str,
    side_probability: float,
) -> float | None:
    if side == "up":
        if _ledger_numeric_equal(row["calibrated_probability"], side_probability):
            return side_probability
        return None

    for candidate in _ledger_float_candidates(row["calibrated_probability"]):
        if 1.0 - candidate == side_probability:
            return candidate
    return None


def _storage_recovered_prediction_semantic_values(row: Any) -> dict[str, Any] | None:
    decision = row["edge_decision"]
    if not isinstance(decision, Mapping):
        return None
    side = decision.get("side")
    if side not in {"up", "down"}:
        return None

    expected_target = 1 if side == "up" else 0
    exact_pairs = (
        (row["predicted_target"], decision.get("predicted_target")),
        (row["predicted_target"], expected_target),
        (row["predicted_side"], side),
        (row["selected_side"], side),
        (row["market_probability_observed"], decision.get("market_probability_observed")),
        (row["executable"], decision.get("executable")),
        (row["trade"], decision.get("trade")),
        (row["decision_reason"], decision.get("reason")),
    )
    if any(stored != expected for stored, expected in exact_pairs):
        return None

    values = _semantic_values(row, LivePrediction)
    numeric_pairs = (
        ("min_edge", "min_edge"),
        ("selected_ask", "ask"),
        ("selected_bid", "bid"),
        ("selected_spread", "spread"),
        ("fee", "fee"),
        ("slippage_buffer", "slippage_buffer"),
        ("raw_edge", "raw_edge"),
        ("cost_adjusted_edge", "cost_adjusted_edge"),
        ("decision_min_edge", "min_edge"),
    )
    for stored_name, decision_name in numeric_pairs:
        original = decision.get(decision_name)
        if not _ledger_numeric_equal(row[stored_name], original):
            return None
        values[stored_name] = original

    selected_bid_name = f"{side}_best_bid"
    selected_ask_name = f"{side}_best_ask"
    selected_bid = row[selected_bid_name]
    selected_ask = row[selected_ask_name]
    if not _ledger_numeric_equal(selected_bid, decision.get("bid")):
        return None
    if not _ledger_numeric_equal(selected_ask, decision.get("ask")):
        return None
    values[selected_bid_name] = decision.get("bid")
    values[selected_ask_name] = decision.get("ask")

    side_probability = decision.get("side_probability")
    if isinstance(side_probability, bool):
        return None
    try:
        probability = float(side_probability)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(probability):
        return None
    calibrated = _recover_calibrated_probability(
        row,
        side=side,
        side_probability=probability,
    )
    if calibrated is None:
        return None
    values["calibrated_probability"] = calibrated
    return values


def _hash_candidate_group(
    row: Any,
    names: tuple[str, ...],
    *,
    source_name: str,
) -> tuple[tuple[str, ...], tuple[float, ...]] | None:
    source = row[source_name]
    candidates = _ledger_float_candidates(source)
    if not candidates:
        return None
    if any(not _ledger_numeric_equal(row[name], source) for name in names):
        return None
    return names, candidates


def _ambiguous_prediction_hash_matches(
    row: Any,
    values: dict[str, Any],
) -> bool:
    target = row["semantic_sha256"]
    decision = row["edge_decision"]
    selected_side = decision.get("side") if isinstance(decision, Mapping) else None
    selected_fields = {
        f"{selected_side}_best_bid",
        f"{selected_side}_best_ask",
    }
    ambiguous: list[tuple[tuple[str, ...], tuple[float, ...]]] = []
    handled = set(selected_fields)

    if bool(row.get("market_probability_observed", True)):
        probability_group = _hash_candidate_group(
            row,
            ("raw_probability", "market_probability"),
            source_name="market_probability",
        )
        if probability_group is None:
            return False
        if len(probability_group[1]) > 1:
            ambiguous.append(probability_group)
        handled.update(("raw_probability", "market_probability"))
    else:
        if row["market_probability"] is not None:
            return False
        prior_group = _hash_candidate_group(
            row,
            ("training_prior", "raw_probability"),
            source_name="training_prior",
        )
        if prior_group is None:
            return False
        if len(prior_group[1]) > 1:
            ambiguous.append(prior_group)
        handled.update(("training_prior", "raw_probability", "market_probability"))

    for name in _HASH_RECOVERY_NUMERIC_FIELDS:
        if name in handled or row[name] is None:
            continue
        candidates = _ledger_float_candidates(row[name])
        if not candidates:
            return False
        if len(candidates) > 1:
            ambiguous.append(((name,), candidates))

    if not ambiguous:
        return False

    def is_current_choice(names: tuple[str, ...], candidate: float) -> bool:
        for name in names:
            current = values[name]
            if not isinstance(current, float) or current.hex() != candidate.hex():
                return False
        return True

    choices: list[tuple[tuple[str, ...], tuple[float, ...]]] = []
    for names, candidates in ambiguous:
        alternatives = tuple(
            candidate
            for candidate in candidates
            if not is_current_choice(names, candidate)
        )
        if alternatives:
            choices.append((names, alternatives))

    if not choices:
        return False

    attempts = 0

    def hash_matches() -> bool:
        nonlocal attempts
        if attempts >= _HASH_RECOVERY_MAX_ATTEMPTS:
            return False
        attempts += 1
        return canonical_hash(values) == target

    def search(start: int, remaining_changes: int) -> bool:
        if attempts >= _HASH_RECOVERY_MAX_ATTEMPTS:
            return False
        if remaining_changes == 0:
            return hash_matches()

        final_start = len(choices) - remaining_changes
        for index in range(start, final_start + 1):
            names, candidates = choices[index]
            originals = {name: values[name] for name in names}
            try:
                for candidate in candidates:
                    for name in names:
                        values[name] = candidate
                    if search(index + 1, remaining_changes - 1):
                        return True
            finally:
                values.update(originals)
        return False

    for changed_count in range(1, len(choices) + 1):
        if search(0, changed_count):
            return True
        if attempts >= _HASH_RECOVERY_MAX_ATTEMPTS:
            return False
    return False


def _semantic_hash_matches(
    row: Any,
    record_type: type[LivePrediction] | type[LivePredictionEvaluation],
) -> bool:
    try:
        values = _semantic_values(row, record_type)
        if canonical_hash(values) == row["semantic_sha256"]:
            return True
        if record_type is not LivePrediction:
            return False
        recovered = _storage_recovered_prediction_semantic_values(row)
        if recovered is None:
            return False
        if canonical_hash(recovered) == row["semantic_sha256"]:
            return True
        return _ambiguous_prediction_hash_matches(row, recovered)
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def _valid_market_identity(row: Any) -> bool:
    condition_id = str(row["condition_id"] or "")
    slug = str(row["slug"] or "")
    up_token_id = str(row["up_token_id"] or "")
    down_token_id = str(row["down_token_id"] or "")
    if not condition_id or not slug or not up_token_id or not down_token_id:
        return False
    if up_token_id == down_token_id:
        return False
    start = _stored_utc(row["start_at"])
    end = _stored_utc(row["end_at"])
    return end > start


def build_integrity_report(
    connection: Connection,
    *,
    policies: dict[int, LivePolicySpec],
    now: datetime,
    max_lateness_seconds: int = DEFAULT_MAX_LATENESS_SECONDS,
) -> dict[str, int]:
    current = _require_aware_utc(now, "now")
    if not policies:
        raise ValueError("at least one live prediction policy is required")
    if max_lateness_seconds < 0 or max_lateness_seconds > DEFAULT_MAX_LATENESS_SECONDS:
        raise ValueError("max_lateness_seconds must be between 0 and 10")

    market_rows = connection.execute(
        select(polymarket_markets).where(
            polymarket_markets.c.horizon_seconds.in_(tuple(policies))
        )
    ).mappings().all()
    prediction_rows = connection.execute(
        select(live_predictions).where(
            live_predictions.c.prediction_version == LIVE_PREDICTION_VERSION,
            live_predictions.c.horizon_seconds.in_(tuple(policies)),
        )
    ).mappings().all()
    prediction_ids = {str(row["prediction_id"]) for row in prediction_rows}
    if prediction_ids:
        evaluation_rows = connection.execute(
            select(live_prediction_evaluations).where(
                live_prediction_evaluations.c.prediction_id.in_(prediction_ids)
            )
        ).mappings().all()
    else:
        evaluation_rows = []

    predictions_by_condition = {
        str(row["condition_id"]): row for row in prediction_rows
    }
    scheduled_eligible = 0
    late_or_missed = 0
    for market in market_rows:
        horizon_seconds = int(market["horizon_seconds"])
        policy = policies.get(horizon_seconds)
        if policy is None or not _valid_market_identity(market):
            continue
        start = _stored_utc(market["start_at"])
        end = _stored_utc(market["end_at"])
        scheduled_at = start + timedelta(seconds=policy.selected_offset_seconds)
        if not start < scheduled_at < end or scheduled_at > current:
            continue
        scheduled_eligible += 1
        deadline = scheduled_at + timedelta(seconds=max_lateness_seconds)
        prediction = predictions_by_condition.get(str(market["condition_id"]))
        if prediction is None:
            if current > deadline or current >= end:
                late_or_missed += 1
            continue
        recorded_at = _stored_utc(prediction["recorded_at"])
        if recorded_at < scheduled_at or recorded_at > deadline or recorded_at >= end:
            late_or_missed += 1

    natural_key_counts = Counter(
        (str(row["condition_id"]), str(row["prediction_version"]))
        for row in prediction_rows
    )
    duplicate_natural_keys = sum(
        1 for count in natural_key_counts.values() if count > 1
    )

    invalid_prediction_ids = {
        str(row["prediction_id"])
        for row in prediction_rows
        if not _semantic_hash_matches(row, LivePrediction)
    }
    invalid_evaluations = sum(
        1
        for row in evaluation_rows
        if not _semantic_hash_matches(row, LivePredictionEvaluation)
    )
    semantic_hash_violations = len(invalid_prediction_ids) + invalid_evaluations

    evaluations_by_prediction: dict[str, list[Any]] = {}
    for evaluation in evaluation_rows:
        evaluations_by_prediction.setdefault(
            str(evaluation["prediction_id"]), []
        ).append(evaluation)

    timing_violation_ids: set[str] = set()
    for prediction in prediction_rows:
        prediction_id = str(prediction["prediction_id"])
        recorded_at = _stored_utc(prediction["recorded_at"])
        market_end_at = _stored_utc(prediction["market_end_at"])
        if recorded_at >= market_end_at:
            timing_violation_ids.add(prediction_id)
        for evaluation in evaluations_by_prediction.get(prediction_id, []):
            label_observed_at = _stored_utc(evaluation["label_source_observed_at"])
            if recorded_at >= label_observed_at:
                timing_violation_ids.add(prediction_id)

    evaluated_prediction_ids = set(evaluations_by_prediction)
    prediction_mutation_violations = len(
        invalid_prediction_ids.intersection(evaluated_prediction_ids)
    )

    return {
        "scheduled_eligible_markets": scheduled_eligible,
        "prediction_count": len(prediction_rows),
        "late_or_missed_coverage": late_or_missed,
        "pre_outcome_timing_violations": len(timing_violation_ids),
        "duplicate_natural_keys": duplicate_natural_keys,
        "semantic_hash_violations": semantic_hash_violations,
        "evaluation_count": len(evaluation_rows),
        "prediction_mutation_violations": prediction_mutation_violations,
    }


def _validate_runtime_limits(args: argparse.Namespace) -> None:
    if args.max_lateness_seconds < 0:
        raise ValueError("max_lateness_seconds must be non-negative")
    if args.max_lateness_seconds > DEFAULT_MAX_LATENESS_SECONDS:
        raise ValueError("max_lateness_seconds must not exceed 10")
    if args.command == "run" and args.poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be positive")


def _run_command(args: argparse.Namespace) -> int:
    _validate_runtime_limits(args)
    settings = _settings(args)
    ensure_live_prediction_safety(settings)
    engine = create_engine(settings.database_url)
    with engine.begin() as connection:
        policies = load_runtime_policies(
            connection,
            settings=settings,
            source_run_ids=tuple(args.source_calibration_run_id),
        )
    service = LivePredictionService(
        engine=engine,
        policies=policies,
        client=PolymarketPriceHistoryClient(),
        max_lateness_seconds=args.max_lateness_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
    )
    asyncio.run(service.run())
    return 0


def _report_command(args: argparse.Namespace) -> int:
    _validate_runtime_limits(args)
    settings = _settings(args)
    ensure_live_prediction_safety(settings)
    engine = create_engine(settings.database_url)
    with engine.connect() as connection:
        policies = load_runtime_policies(
            connection,
            settings=settings,
            source_run_ids=tuple(args.source_calibration_run_id),
        )
        report = build_integrity_report(
            connection,
            policies=policies,
            now=datetime.now(UTC),
            max_lateness_seconds=args.max_lateness_seconds,
        )
    print(json.dumps(report, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return _run_command(args)
        if args.command == "report":
            return _report_command(args)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    raise SystemExit(f"unsupported command: {args.command}")
