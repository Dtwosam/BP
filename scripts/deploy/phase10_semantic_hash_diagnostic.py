from __future__ import annotations

import argparse
import json
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import create_engine, select

from bp_engine.calibration.calibrators import apply_calibration
from bp_engine.config import Settings
from bp_engine.features.calculators import book_state
from bp_engine.features.hashing import canonical_hash
from bp_engine.features.sources import FeatureSourceReader, StateObservation
from bp_engine.live_prediction.cli import (
    _ledger_float_candidates,
    _semantic_hash_matches,
    _storage_recovered_prediction_semantic_values,
    _stored_utc,
    load_runtime_policies,
)
from bp_engine.live_prediction.inputs import (
    _book_descriptor,
    _book_input,
    _merge_book_predictors,
)
from bp_engine.live_prediction.models import LivePolicySpec, LivePrediction
from bp_engine.live_prediction.predictor import _book_quote
from bp_engine.live_prediction.repository import _ledger_numeric_equal
from bp_engine.live_prediction.service import ensure_live_prediction_safety
from bp_engine.storage.schema import live_predictions


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only Phase 10 diagnostic that reconstructs legacy prediction inputs "
            "from frozen policy provenance and recorder state."
        )
    )
    parser.add_argument("--env-file", required=True)
    parser.add_argument(
        "--source-calibration-run-id",
        action="append",
        required=True,
    )
    parser.add_argument("--expected-head", required=True)
    return parser


def _state_for_prediction(
    connection: Any,
    row: Any,
    side: str,
) -> tuple[StateObservation | None, str]:
    cutoff_name = f"{side}_book_cutoff_at"
    fresh_name = f"{side}_book_fresh"
    stored_cutoff = row[cutoff_name]
    if stored_cutoff is None:
        return None, "stored_none"

    asset_id = str(row[f"{side}_token_id"])
    state = FeatureSourceReader().latest_state(
        connection,
        source="polymarket",
        stream="market",
        instrument=str(row["condition_id"]),
        asset_id=asset_id,
        feature_at=_stored_utc(row["scheduled_at"]),
    )
    if state is None:
        return None, "source_missing"
    if state.effective_at != _stored_utc(stored_cutoff):
        return state, "cutoff_mismatch"
    if bool(state.fresh) != bool(row[fresh_name]):
        return state, "freshness_mismatch"
    return state, "matched"


def _raw_probability_candidates(
    row: Any,
    policy: LivePolicySpec,
) -> tuple[float, ...]:
    prior = float(policy.training_prior)
    if not _ledger_numeric_equal(row["training_prior"], prior):
        return ()

    if not bool(row["market_probability_observed"]):
        if row["market_probability"] is not None:
            return ()
        if not _ledger_numeric_equal(row["raw_probability"], prior):
            return ()
        return (prior,)

    source = row["market_probability"]
    if source is None:
        return ()
    return tuple(
        candidate
        for candidate in _ledger_float_candidates(source)
        if _ledger_numeric_equal(row["raw_probability"], candidate)
    )


def _side_calibration_matches(row: Any, calibrated: float) -> bool:
    decision = row["edge_decision"]
    if not isinstance(decision, Mapping):
        return False
    side = decision.get("side")
    side_probability = decision.get("side_probability")
    if side not in {"up", "down"} or isinstance(side_probability, bool):
        return False
    try:
        expected = float(side_probability)
    except (TypeError, ValueError, OverflowError):
        return False
    if not math.isfinite(expected):
        return False
    actual = calibrated if side == "up" else 1.0 - calibrated
    return actual == expected


def _input_fingerprint(
    row: Any,
    *,
    probability: float | None,
    up_state: StateObservation | None,
    down_state: StateObservation | None,
) -> str:
    up_group = book_state("pm_up", up_state)
    down_group = book_state("pm_down", down_state)
    predictors = _merge_book_predictors(probability, up_group, down_group)
    up_book = _book_input(up_state)
    down_book = _book_input(down_state)
    return canonical_hash(
        {
            "condition_id": str(row["condition_id"]),
            "up_token_id": str(row["up_token_id"]),
            "down_token_id": str(row["down_token_id"]),
            "market_start_at": _stored_utc(row["market_start_at"]),
            "market_end_at": _stored_utc(row["market_end_at"]),
            "scheduled_at": _stored_utc(row["scheduled_at"]),
            "downloaded_at": _stored_utc(row["market_probability_downloaded_at"]),
            "price_source": str(row["market_probability_source"]),
            "price_dataset": str(row["market_probability_dataset"]),
            "price_request_params": dict(row["market_probability_request_params"] or {}),
            "price_response_sha256": str(row["market_probability_response_sha256"]),
            "market_probability_observed": bool(row["market_probability_observed"]),
            "market_probability": probability,
            "market_probability_observed_at": (
                _stored_utc(row["market_probability_observed_at"])
                if row["market_probability_observed_at"] is not None
                else None
            ),
            "up_book": _book_descriptor(up_book),
            "down_book": _book_descriptor(down_book),
            "predictors": predictors,
        }
    )


def _semantic_candidate_matches(
    row: Any,
    policy: LivePolicySpec,
    *,
    raw_probability: float,
    calibrated_probability: float,
    up_state: StateObservation | None,
    down_state: StateObservation | None,
) -> bool:
    values = _storage_recovered_prediction_semantic_values(row)
    if values is None:
        return False

    up_book = _book_input(up_state)
    down_book = _book_input(down_state)
    values["training_prior"] = float(policy.training_prior)
    values["raw_probability"] = raw_probability
    values["calibrated_probability"] = calibrated_probability
    values["market_probability"] = (
        raw_probability if bool(row["market_probability_observed"]) else None
    )
    values["up_best_bid"] = _book_quote(up_book, "best_bid")
    values["up_best_ask"] = _book_quote(up_book, "best_ask")
    values["down_best_bid"] = _book_quote(down_book, "best_bid")
    values["down_best_ask"] = _book_quote(down_book, "best_ask")
    return canonical_hash(values) == str(row["semantic_sha256"])


def _diagnose_row(connection: Any, row: Any, policy: LivePolicySpec) -> dict[str, Any]:
    decision = row["edge_decision"]
    side = decision.get("side") if isinstance(decision, Mapping) else None
    up_state, up_status = _state_for_prediction(connection, row, "up")
    down_state, down_status = _state_for_prediction(connection, row, "down")
    raw_candidates = _raw_probability_candidates(row, policy)

    fingerprint_matches = 0
    semantic_matches = 0
    relation_matches = 0
    for raw_probability in raw_candidates:
        calibrated = apply_calibration(policy.calibration_fit, (raw_probability,))[0]
        if not _ledger_numeric_equal(row["calibrated_probability"], calibrated):
            continue
        if not _side_calibration_matches(row, calibrated):
            continue
        relation_matches += 1
        observed_probability = (
            raw_probability if bool(row["market_probability_observed"]) else None
        )
        fingerprint = _input_fingerprint(
            row,
            probability=observed_probability,
            up_state=up_state,
            down_state=down_state,
        )
        if fingerprint != str(row["input_fingerprint"]):
            continue
        fingerprint_matches += 1
        if _semantic_candidate_matches(
            row,
            policy,
            raw_probability=raw_probability,
            calibrated_probability=calibrated,
            up_state=up_state,
            down_state=down_state,
        ):
            semantic_matches += 1

    return {
        "prediction_id_prefix": str(row["prediction_id"])[:12],
        "horizon_seconds": int(row["horizon_seconds"]),
        "selected_side": side,
        "market_probability_observed": bool(row["market_probability_observed"]),
        "up_source_status": up_status,
        "down_source_status": down_status,
        "raw_candidate_count": len(raw_candidates),
        "calibration_relation_matches": relation_matches,
        "input_fingerprint_matches": fingerprint_matches,
        "semantic_hash_matches": semantic_matches,
    }


def main() -> int:
    args = _parser().parse_args()
    settings = Settings(_env_file=args.env_file)
    ensure_live_prediction_safety(settings)
    engine = create_engine(settings.database_url)

    with engine.connect() as connection:
        policies = load_runtime_policies(
            connection,
            settings=settings,
            source_run_ids=tuple(args.source_calibration_run_id),
        )
        rows = connection.execute(
            select(live_predictions).where(
                live_predictions.c.prediction_version == "live-prediction-v1",
                live_predictions.c.horizon_seconds.in_(tuple(policies)),
            )
        ).mappings().all()
        invalid = [
            row for row in rows if not _semantic_hash_matches(row, LivePrediction)
        ]
        diagnostics = [
            _diagnose_row(connection, row, policies[int(row["horizon_seconds"])])
            for row in invalid
        ]

    reconstructed = sum(1 for item in diagnostics if item["semantic_hash_matches"] > 0)
    fingerprint_recovered = sum(
        1 for item in diagnostics if item["input_fingerprint_matches"] > 0
    )
    unexplained = len(diagnostics) - reconstructed
    payload = {
        "expected_head": args.expected_head,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "prediction_count": len(rows),
        "current_semantic_hash_violations": len(diagnostics),
        "input_fingerprint_recovered": fingerprint_recovered,
        "deterministic_semantic_hash_recovered": reconstructed,
        "unexplained_semantic_hash_violations": unexplained,
        "rows": diagnostics,
    }
    print(json.dumps(payload, sort_keys=True))
    print(f"DIAGNOSTIC_BAD_ROWS={len(diagnostics)}")
    print(f"DIAGNOSTIC_INPUT_FINGERPRINT_RECOVERED={fingerprint_recovered}")
    print(f"DIAGNOSTIC_SEMANTIC_HASH_RECOVERED={reconstructed}")
    print(f"DIAGNOSTIC_UNEXPLAINED={unexplained}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
