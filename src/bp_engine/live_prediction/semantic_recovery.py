from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, select

from bp_engine.calibration.calibrators import apply_calibration
from bp_engine.features.calculators import book_state
from bp_engine.features.hashing import canonical_hash
from bp_engine.features.sources import FeatureSourceReader, StateObservation
from bp_engine.live_prediction.inputs import (
    LiveMarketInput,
    _book_descriptor,
    _book_input,
    _merge_book_predictors,
)
from bp_engine.live_prediction.models import LivePolicySpec, LivePrediction
from bp_engine.live_prediction.predictor import build_live_prediction
from bp_engine.live_prediction.repository import _ledger_numeric_equal
from bp_engine.recorder.models import RawEvent
from bp_engine.recorder.state import MarketStateReducer, MarketStateSnapshot
from bp_engine.storage.schema import market_state_1s, raw_market_events

_PROVENANCE_RECOVERY_MAX_ATTEMPTS = 4096
_PROVENANCE_FLOAT_STEPS = (_PROVENANCE_RECOVERY_MAX_ATTEMPTS - 1) // 2


def _stored_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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
    for _ in range(_PROVENANCE_FLOAT_STEPS):
        if lower_open:
            lower = math.nextafter(lower, -math.inf)
            lower_open = add(lower)
        if upper_open:
            upper = math.nextafter(upper, math.inf)
            upper_open = add(upper)
        if not lower_open and not upper_open:
            break
    return tuple(candidates)


def _state_for_prediction(
    connection: Connection,
    row: Any,
    side: str,
) -> tuple[StateObservation | None, str]:
    stored_cutoff = row[f"{side}_book_cutoff_at"]
    if stored_cutoff is None:
        return None, "stored_none"

    state = FeatureSourceReader().latest_state(
        connection,
        source="polymarket",
        stream="market",
        instrument=str(row["condition_id"]),
        asset_id=str(row[f"{side}_token_id"]),
        feature_at=_stored_utc(row["scheduled_at"]),
    )
    if state is None:
        return None, "source_missing"
    if state.effective_at != _stored_utc(stored_cutoff):
        return state, "cutoff_mismatch"
    if bool(state.fresh) != bool(row[f"{side}_book_fresh"]):
        return state, "freshness_mismatch"
    return state, "matched"


def _raw_event(row: Mapping[str, Any]) -> RawEvent:
    source_timestamp = row["source_timestamp"]
    return RawEvent(
        source=str(row["source"]),
        stream=str(row["stream"]),
        instrument=str(row["instrument"]),
        event_type=str(row["event_type"]),
        source_timestamp=(
            _stored_utc(source_timestamp) if source_timestamp is not None else None
        ),
        received_at=_stored_utc(row["received_at"]),
        sequence=(str(row["sequence"]) if row["sequence"] is not None else None),
        market_id=(str(row["market_id"]) if row["market_id"] is not None else None),
        asset_id=(str(row["asset_id"]) if row["asset_id"] is not None else None),
        payload=dict(row["payload"]),
        dedupe_key=str(row["dedupe_key"]),
    )


def _snapshot_observation(
    snapshot: MarketStateSnapshot,
    *,
    feature_at: datetime,
    fresh: bool,
) -> StateObservation:
    scheduled = _stored_utc(feature_at)
    bucket_at = _stored_utc(snapshot.bucket_at)
    last_event_at = _stored_utc(snapshot.last_event_at)
    age_seconds = (scheduled - last_event_at).total_seconds()
    return StateObservation(
        row_id=0,
        bucket_at=bucket_at,
        state_key=snapshot.state_key,
        source=snapshot.source,
        stream=snapshot.stream,
        instrument=snapshot.instrument,
        market_id=snapshot.market_id,
        asset_id=snapshot.asset_id,
        last_event_at=last_event_at,
        state=dict(snapshot.state),
        fresh=fresh,
        age_seconds=age_seconds,
    )


def _replayed_state_candidates(
    connection: Connection,
    row: Any,
    side: str,
) -> tuple[StateObservation, ...]:
    stored_cutoff = row[f"{side}_book_cutoff_at"]
    if stored_cutoff is None:
        return ()

    scheduled = _stored_utc(row["scheduled_at"])
    expected_cutoff = _stored_utc(stored_cutoff)
    asset_id = str(row[f"{side}_token_id"])
    condition_id = str(row["condition_id"])
    expected_fresh = bool(row[f"{side}_book_fresh"])

    compact_rows = connection.execute(
        select(market_state_1s.c.bucket_at)
        .where(
            market_state_1s.c.source == "polymarket",
            market_state_1s.c.stream == "market",
            market_state_1s.c.instrument == condition_id,
            market_state_1s.c.asset_id == asset_id,
            market_state_1s.c.bucket_at <= scheduled,
        )
        .order_by(market_state_1s.c.bucket_at.desc(), market_state_1s.c.id.desc())
        .limit(8)
    ).all()
    candidate_buckets = {
        expected_cutoff.replace(microsecond=0),
        scheduled.replace(microsecond=0),
        *(_stored_utc(item[0]) for item in compact_rows),
    }

    raw_rows = connection.execute(
        select(raw_market_events)
        .where(
            raw_market_events.c.source == "polymarket",
            raw_market_events.c.stream == "market",
            raw_market_events.c.instrument == condition_id,
            raw_market_events.c.received_at <= expected_cutoff,
        )
        .order_by(raw_market_events.c.received_at, raw_market_events.c.id)
    ).mappings().all()
    reducer = MarketStateReducer()
    for raw in raw_rows:
        reducer.observe(_raw_event(raw))

    recovered: dict[str, StateObservation] = {}
    for bucket_at in sorted(candidate_buckets):
        for snapshot in reducer.snapshots(bucket_at):
            if snapshot.asset_id != asset_id:
                continue
            state = _snapshot_observation(
                snapshot,
                feature_at=scheduled,
                fresh=expected_fresh,
            )
            if state.effective_at != expected_cutoff:
                continue
            key = canonical_hash(_book_descriptor(_book_input(state)))
            recovered.setdefault(key, state)
    return tuple(recovered.values())


def _state_candidates_for_prediction(
    connection: Connection,
    row: Any,
    side: str,
) -> tuple[StateObservation | None, ...]:
    state, status = _state_for_prediction(connection, row, side)
    if status == "matched":
        return (state,) if state is not None else (None,)
    if status == "stored_none":
        return (None,)

    replayed = _replayed_state_candidates(connection, row, side)
    if replayed:
        return replayed
    return (state,) if state is not None else (None,)


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
            "up_book": _book_descriptor(_book_input(up_state)),
            "down_book": _book_descriptor(_book_input(down_state)),
            "predictors": predictors,
        }
    )


def _rebuilt_prediction(
    row: Any,
    policy: LivePolicySpec,
    *,
    raw_probability: float,
    up_state: StateObservation | None,
    down_state: StateObservation | None,
) -> LivePrediction:
    observed = bool(row["market_probability_observed"])
    market_probability = raw_probability if observed else None
    up_group = book_state("pm_up", up_state)
    down_group = book_state("pm_down", down_state)
    predictors = _merge_book_predictors(market_probability, up_group, down_group)
    live_input = LiveMarketInput(
        condition_id=str(row["condition_id"]),
        up_token_id=str(row["up_token_id"]),
        down_token_id=str(row["down_token_id"]),
        market_start_at=_stored_utc(row["market_start_at"]),
        market_end_at=_stored_utc(row["market_end_at"]),
        scheduled_at=_stored_utc(row["scheduled_at"]),
        downloaded_at=_stored_utc(row["market_probability_downloaded_at"]),
        price_source=str(row["market_probability_source"]),
        price_dataset=str(row["market_probability_dataset"]),
        price_request_params=dict(row["market_probability_request_params"] or {}),
        price_response_sha256=str(row["market_probability_response_sha256"]),
        price_response_payload={},
        market_probability_observed=observed,
        market_probability=market_probability,
        market_probability_observed_at=(
            _stored_utc(row["market_probability_observed_at"])
            if row["market_probability_observed_at"] is not None
            else None
        ),
        up_book=_book_input(up_state),
        down_book=_book_input(down_state),
        predictors=predictors,
        input_fingerprint=str(row["input_fingerprint"]),
    )
    return build_live_prediction(
        policy,
        live_input,
        condition_id=str(row["condition_id"]),
        slug=str(row["slug"]),
        horizon_seconds=int(row["horizon_seconds"]),
        market_start_at=_stored_utc(row["market_start_at"]),
        market_end_at=_stored_utc(row["market_end_at"]),
        up_token_id=str(row["up_token_id"]),
        down_token_id=str(row["down_token_id"]),
        recorded_at=_stored_utc(row["recorded_at"]),
    )


def provenance_prediction_hash_matches(
    connection: Connection,
    row: Any,
    policy: LivePolicySpec,
) -> bool:
    try:
        up_states = _state_candidates_for_prediction(connection, row, "up")
        down_states = _state_candidates_for_prediction(connection, row, "down")
        raw_candidates = _raw_probability_candidates(row, policy)
        attempts = 0

        for raw_probability in raw_candidates:
            calibrated = apply_calibration(policy.calibration_fit, (raw_probability,))[0]
            if not _ledger_numeric_equal(row["calibrated_probability"], calibrated):
                continue
            if not _side_calibration_matches(row, calibrated):
                continue
            observed_probability = (
                raw_probability if bool(row["market_probability_observed"]) else None
            )
            for up_state in up_states:
                for down_state in down_states:
                    if attempts >= _PROVENANCE_RECOVERY_MAX_ATTEMPTS:
                        return False
                    attempts += 1
                    if _input_fingerprint(
                        row,
                        probability=observed_probability,
                        up_state=up_state,
                        down_state=down_state,
                    ) != str(row["input_fingerprint"]):
                        continue
                    rebuilt = _rebuilt_prediction(
                        row,
                        policy,
                        raw_probability=raw_probability,
                        up_state=up_state,
                        down_state=down_state,
                    )
                    if rebuilt.semantic_sha256 == str(row["semantic_sha256"]):
                        return True
        return False
    except (KeyError, RuntimeError, TypeError, ValueError, OverflowError):
        return False
