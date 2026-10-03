from __future__ import annotations

import argparse
import glob
import json
import math
import statistics
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import report_v3_fresh_book_trades as trade_report
from sqlalchemy import Connection, create_engine, select, text

from bp_engine.config import Settings
from bp_engine.features.hashing import canonical_hash
from bp_engine.features.v3_models import V3FeatureTarget
from bp_engine.features.v3_service import V3_FEATURE_VERSION, build_v3_feature
from bp_engine.storage import schema
from bp_engine.v3_paper.service import (
    FROZEN_CANDIDATE,
    FROZEN_MODEL_SHA256,
    FROZEN_OFFSET_SECONDS,
    V3_PAPER_PREDICTION_VERSION,
    V3PaperMarket,
    _book_descriptor,
    _books,
    _predictors,
)
from bp_engine.v3_research.service import model_predictor_names

FORENSICS_VERSION = "v3-fresh-book-feature-forensics-v1"
EXTREME_EDGE_THRESHOLD = Decimal("0.50")
LOW_EDGE_THRESHOLD = Decimal("0.15")
_ZERO = Decimal("0")

_RETURN_NAMES = (
    "coinbase_return_from_market_start",
    "coinbase_return_30s",
    "coinbase_return_60s",
    "coinbase_return_120s",
    "bybit_spot_return_from_market_start",
    "bybit_spot_return_30s",
    "bybit_spot_return_60s",
    "bybit_spot_return_120s",
    "bybit_linear_return_from_market_start",
    "bybit_linear_return_30s",
    "bybit_linear_return_60s",
    "bybit_linear_return_120s",
)
_MARKET_START_NAMES = (
    "coinbase_return_from_market_start",
    "bybit_spot_return_from_market_start",
    "bybit_linear_return_from_market_start",
)


class V3FeatureForensicsError(RuntimeError):
    """Raised when V3 trade inputs cannot be replayed exactly."""


def _stored_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _decimal(value: object, name: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as exc:
        raise V3FeatureForensicsError(f"{name} must be numeric") from exc
    if not result.is_finite():
        raise V3FeatureForensicsError(f"{name} must be finite")
    return result


def _finite_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _prediction_rows(
    connection: Connection,
    prediction_ids: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    rows = connection.execute(
        select(schema.live_predictions).where(
            schema.live_predictions.c.prediction_id.in_(prediction_ids)
        )
    ).mappings().all()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        values = dict(row)
        prediction_id = str(values["prediction_id"])
        if prediction_id in result:
            raise V3FeatureForensicsError(
                f"duplicate stored prediction_id={prediction_id}"
            )
        result[prediction_id] = values
    missing = sorted(set(prediction_ids).difference(result))
    if missing:
        raise V3FeatureForensicsError(
            f"missing stored prediction for {missing[0]}"
        )
    return result


def _replay_inputs(
    connection: Connection,
    prediction: Mapping[str, Any],
) -> dict[str, Any]:
    prediction_id = str(prediction["prediction_id"])
    if prediction["prediction_version"] != V3_PAPER_PREDICTION_VERSION:
        raise V3FeatureForensicsError(
            f"unexpected prediction version for {prediction_id}"
        )
    if prediction["source_feature_version"] != V3_FEATURE_VERSION:
        raise V3FeatureForensicsError(
            f"unexpected feature version for {prediction_id}"
        )
    if int(prediction["selected_offset_seconds"]) != FROZEN_OFFSET_SECONDS:
        raise V3FeatureForensicsError(
            f"unexpected feature offset for {prediction_id}"
        )
    if prediction["source_training_semantic_sha256"] != FROZEN_MODEL_SHA256:
        raise V3FeatureForensicsError(
            f"unexpected model SHA for {prediction_id}"
        )

    start = _stored_utc(prediction["market_start_at"])
    end = _stored_utc(prediction["market_end_at"])
    scheduled = _stored_utc(prediction["scheduled_at"])
    recorded = _stored_utc(prediction["recorded_at"])
    expected_scheduled = start + timedelta(seconds=FROZEN_OFFSET_SECONDS)
    if scheduled != expected_scheduled:
        raise V3FeatureForensicsError(
            f"scheduled_at drift for {prediction_id}"
        )

    target = V3FeatureTarget(
        condition_id=str(prediction["condition_id"]),
        slug=str(prediction["slug"]),
        horizon_seconds=int(prediction["horizon_seconds"]),
        market_start_at=start,
        market_end_at=end,
    )
    feature = build_v3_feature(
        connection,
        target,
        scheduled,
        generated_at=recorded,
    )
    predictors = _predictors(feature)
    model_names = model_predictor_names(predictors, FROZEN_CANDIDATE)
    model_predictors = {name: predictors.get(name) for name in model_names}

    market = V3PaperMarket(
        condition_id=str(prediction["condition_id"]),
        slug=str(prediction["slug"]),
        horizon_seconds=int(prediction["horizon_seconds"]),
        market_start_at=start,
        market_end_at=end,
        scheduled_at=scheduled,
        up_token_id=str(prediction["up_token_id"]),
        down_token_id=str(prediction["down_token_id"]),
    )
    _, up_state, down_state = _books(connection, market)
    books_descriptor = {
        "up": _book_descriptor(up_state),
        "down": _book_descriptor(down_state),
    }
    reconstructed_book_hash = canonical_hash(books_descriptor)
    reconstructed_input_fingerprint = canonical_hash(
        {
            "feature_hash": feature.feature_hash,
            "feature_input_fingerprint": feature.input_fingerprint,
            "books": books_descriptor,
            "model_sha256": FROZEN_MODEL_SHA256,
        }
    )
    stored_input_fingerprint = str(prediction["input_fingerprint"])
    stored_book_hash = str(prediction["market_probability_response_sha256"])
    fingerprint_match = reconstructed_input_fingerprint == stored_input_fingerprint
    book_hash_match = reconstructed_book_hash == stored_book_hash
    if not fingerprint_match or not book_hash_match:
        raise V3FeatureForensicsError(
            f"input replay mismatch for prediction_id={prediction_id}"
        )

    return {
        "feature_at": feature.feature_at.isoformat(),
        "feature_hash": feature.feature_hash,
        "feature_input_fingerprint": feature.input_fingerprint,
        "reconstructed_input_fingerprint": reconstructed_input_fingerprint,
        "stored_input_fingerprint": stored_input_fingerprint,
        "fingerprint_match": fingerprint_match,
        "reconstructed_book_hash": reconstructed_book_hash,
        "stored_book_hash": stored_book_hash,
        "book_hash_match": book_hash_match,
        "features": dict(feature.features),
        "missing_flags": dict(feature.missing_flags),
        "source_cutoffs": dict(feature.source_cutoffs),
        "model_predictor_names": list(model_names),
        "model_predictors": model_predictors,
    }


def _support(value: object, selected_side: str) -> int | None:
    numeric = _finite_float(value)
    if numeric is None:
        return None
    if numeric == 0.0:
        return 0
    direction = 1 if selected_side == "up" else -1
    sign = 1 if numeric > 0 else -1
    return 1 if sign == direction else -1


def _support_summary(
    features: Mapping[str, Any],
    *,
    selected_side: str,
) -> dict[str, Any]:
    all_support = [_support(features.get(name), selected_side) for name in _RETURN_NAMES]
    market_support = [
        _support(features.get(name), selected_side)
        for name in _MARKET_START_NAMES
    ]
    all_present = [value for value in all_support if value is not None]
    market_present = [value for value in market_support if value is not None]

    def counts(values: list[int]) -> dict[str, int]:
        return {
            "support": sum(value == 1 for value in values),
            "oppose": sum(value == -1 for value in values),
            "zero": sum(value == 0 for value in values),
            "present": len(values),
        }

    coinbase = _finite_float(features.get("coinbase_return_from_market_start"))
    bybit_spot = _finite_float(features.get("bybit_spot_return_from_market_start"))
    bybit_linear = _finite_float(features.get("bybit_linear_return_from_market_start"))
    direction = 1 if selected_side == "up" else -1

    return {
        "market_start_venue_votes": counts(market_present),
        "all_return_votes": counts(all_present),
        "coinbase_selected_side_signed_bps": (
            None if coinbase is None else coinbase * direction * 10000.0
        ),
        "bybit_spot_selected_side_signed_bps": (
            None if bybit_spot is None else bybit_spot * direction * 10000.0
        ),
        "bybit_linear_selected_side_signed_bps": (
            None if bybit_linear is None else bybit_linear * direction * 10000.0
        ),
        "coinbase_abs_move_bps": (
            None if coinbase is None else abs(coinbase) * 10000.0
        ),
    }


def _numeric_summary(values: Iterable[object]) -> dict[str, Any]:
    numeric = [
        value
        for raw in values
        if (value := _finite_float(raw)) is not None
    ]
    if not numeric:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "min": None,
            "max": None,
        }
    return {
        "count": len(numeric),
        "mean": statistics.fmean(numeric),
        "median": statistics.median(numeric),
        "min": min(numeric),
        "max": max(numeric),
    }


def _cohort_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl = sum(
        (_decimal(row["realized_pnl_usd"], "realized_pnl_usd") for row in rows),
        _ZERO,
    )
    wins = sum(bool(row["correct"]) for row in rows)
    losses = len(rows) - wins
    return {
        "trade_count": len(rows),
        "wins": wins,
        "losses": losses,
        "win_rate": None if not rows else wins / len(rows),
        "realized_pnl_usd": str(pnl),
        "coinbase_selected_side_signed_bps": _numeric_summary(
            row["feature_support"]["coinbase_selected_side_signed_bps"]
            for row in rows
        ),
        "bybit_spot_selected_side_signed_bps": _numeric_summary(
            row["feature_support"]["bybit_spot_selected_side_signed_bps"]
            for row in rows
        ),
        "bybit_linear_selected_side_signed_bps": _numeric_summary(
            row["feature_support"]["bybit_linear_selected_side_signed_bps"]
            for row in rows
        ),
        "coinbase_abs_move_bps": _numeric_summary(
            row["feature_support"]["coinbase_abs_move_bps"]
            for row in rows
        ),
        "market_start_support_votes": _numeric_summary(
            row["feature_support"]["market_start_venue_votes"]["support"]
            for row in rows
        ),
        "all_return_support_votes": _numeric_summary(
            row["feature_support"]["all_return_votes"]["support"]
            for row in rows
        ),
    }


def _feature_contrasts(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    names = sorted(
        {
            name
            for row in rows
            for name in row["replayed_inputs"]["features"]
            if _finite_float(row["replayed_inputs"]["features"].get(name)) is not None
        }
    )
    contrasts: dict[str, dict[str, Any]] = {}
    for name in names:
        wins = [
            row["replayed_inputs"]["features"].get(name)
            for row in rows
            if row["correct"]
        ]
        losses = [
            row["replayed_inputs"]["features"].get(name)
            for row in rows
            if not row["correct"]
        ]
        win_summary = _numeric_summary(wins)
        loss_summary = _numeric_summary(losses)
        win_mean = win_summary["mean"]
        loss_mean = loss_summary["mean"]
        contrasts[name] = {
            "wins": win_summary,
            "losses": loss_summary,
            "mean_difference_win_minus_loss": (
                None
                if win_mean is None or loss_mean is None
                else win_mean - loss_mean
            ),
        }
    return contrasts


def build_report(
    connection: Connection,
    evidence_files: tuple[Path, ...],
) -> dict[str, Any]:
    ledger = trade_report.build_trade_ledger(connection, evidence_files)
    if not ledger:
        raise V3FeatureForensicsError("trade ledger is empty")

    prediction_ids = tuple(str(row["prediction_id"]) for row in ledger)
    stored_predictions = _prediction_rows(connection, prediction_ids)
    enriched: list[dict[str, Any]] = []

    for trade in ledger:
        prediction_id = str(trade["prediction_id"])
        prediction = stored_predictions[prediction_id]
        if str(prediction["condition_id"]) != str(trade["condition_id"]):
            raise V3FeatureForensicsError(
                f"condition mismatch for {prediction_id}"
            )
        if str(prediction["predicted_side"]).lower() != trade["selected_side"]:
            raise V3FeatureForensicsError(
                f"side mismatch for {prediction_id}"
            )
        replayed = _replay_inputs(connection, prediction)
        features = replayed["features"]
        enriched.append(
            {
                **trade,
                "stored_raw_probability": str(prediction["raw_probability"]),
                "stored_calibrated_probability": str(
                    prediction["calibrated_probability"]
                ),
                "stored_scheduled_at": _stored_utc(
                    prediction["scheduled_at"]
                ).isoformat(),
                "replayed_inputs": replayed,
                "feature_support": _support_summary(
                    features,
                    selected_side=trade["selected_side"],
                ),
            }
        )

    extreme = [
        row
        for row in enriched
        if _decimal(row["cost_adjusted_edge"], "cost_adjusted_edge")
        > EXTREME_EDGE_THRESHOLD
    ]
    low_edge = [
        row
        for row in enriched
        if _decimal(row["cost_adjusted_edge"], "cost_adjusted_edge")
        <= LOW_EDGE_THRESHOLD
    ]
    normal_edge = [
        row
        for row in enriched
        if LOW_EDGE_THRESHOLD
        < _decimal(row["cost_adjusted_edge"], "cost_adjusted_edge")
        <= EXTREME_EDGE_THRESHOLD
    ]

    cohorts = {
        "all": _cohort_summary(enriched),
        "wins": _cohort_summary([row for row in enriched if row["correct"]]),
        "losses": _cohort_summary([row for row in enriched if not row["correct"]]),
        "edge_gt_0_50": _cohort_summary(extreme),
        "edge_le_0_15": _cohort_summary(low_edge),
        "edge_0_15_to_0_50": _cohort_summary(normal_edge),
        "epoch_1": _cohort_summary([row for row in enriched if row["epoch"] == 1]),
        "epoch_2": _cohort_summary([row for row in enriched if row["epoch"] == 2]),
        "selected_up": _cohort_summary(
            [row for row in enriched if row["selected_side"] == "up"]
        ),
        "selected_down": _cohort_summary(
            [row for row in enriched if row["selected_side"] == "down"]
        ),
    }

    return {
        "report": FORENSICS_VERSION,
        "v3_candidate": FROZEN_CANDIDATE,
        "v3_offset_seconds": FROZEN_OFFSET_SECONDS,
        "v3_model_sha256": FROZEN_MODEL_SHA256,
        "feature_version": V3_FEATURE_VERSION,
        "trade_count": len(enriched),
        "input_replay_match_count": sum(
            row["replayed_inputs"]["fingerprint_match"]
            and row["replayed_inputs"]["book_hash_match"]
            for row in enriched
        ),
        "cohorts": cohorts,
        "feature_contrasts": _feature_contrasts(enriched),
        "trades": enriched,
        "methodology": {
            "feature_source": "replayed_market_state_1s_at_stored_scheduled_at",
            "exact_input_proof": (
                "reconstructed input_fingerprint and book hash must match "
                "the stored live_predictions row"
            ),
            "provider_source_time_claimed": False,
            "v4_holdout_labels_read": False,
            "model_refit_performed": False,
            "threshold_tuning_performed": False,
        },
        "safety": {
            "database_read_only_required": True,
            "database_writes_performed": False,
            "service_mutation_performed": False,
            "order_submission_performed": False,
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replay the exact compact-state V3 inputs for each settled fresh-book "
            "paper trade and emit trade-level/aggregate forensic diagnostics."
        )
    )
    parser.add_argument("--env-file", default="/etc/bp/bp.env")
    parser.add_argument(
        "--evidence-glob",
        default="/var/lib/bp/evidence/v3-fresh-book-shadow-*.jsonl",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    evidence_files = tuple(
        Path(path) for path in sorted(glob.glob(args.evidence_glob))
    )
    if not evidence_files:
        raise SystemExit("no V3 fresh-book evidence files found")

    settings = Settings(_env_file=args.env_file)
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"options": "-c default_transaction_read_only=on"},
    )
    try:
        with engine.connect() as connection:
            read_only = connection.execute(
                text("SHOW default_transaction_read_only")
            ).scalar_one()
            if read_only != "on":
                raise SystemExit("database connection is not read-only")
            report = build_report(connection, evidence_files)
    finally:
        engine.dispose()

    print(json.dumps(report, sort_keys=True, indent=2, default=str))
    print("PHASE15_V3_FEATURE_FORENSICS=PASS")
    print("DATABASE_ACCESS=read_only")
    print("DATABASE_WRITES_PERFORMED=false")
    print("V4_HOLDOUT_LABELS_READ=false")
    print("MODEL_REFIT_PERFORMED=false")
    print("THRESHOLD_TUNING_PERFORMED=false")
    print("ORDER_SUBMISSION_PERFORMED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
