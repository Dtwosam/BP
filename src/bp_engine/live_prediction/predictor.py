from __future__ import annotations

import math
import re
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from bp_engine.calibration.calibrators import apply_calibration
from bp_engine.calibration.edge import edge_decision_from_predictors
from bp_engine.features.hashing import canonical_hash
from bp_engine.live_prediction.inputs import MAX_LATENESS_SECONDS, LiveBookInput, LiveMarketInput
from bp_engine.live_prediction.models import (
    LIVE_INPUT_VERSION,
    LIVE_PREDICTION_VERSION,
    LivePolicySpec,
    LivePrediction,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PredictionDeadlineError(RuntimeError):
    """Raised when a live prediction is computed outside its allowed window."""


class PredictionIntegrityError(ValueError):
    """Raised when market, policy or live-input provenance is inconsistent."""


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PredictionIntegrityError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _require_sha256(value: str, name: str) -> None:
    if not _SHA256_RE.fullmatch(value):
        raise PredictionIntegrityError(
            f"{name} must be a 64-character lowercase hex SHA-256 digest"
        )


def _probability(value: float, name: str, *, strict: bool = False) -> float:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise PredictionIntegrityError(f"{name} must be finite")
    valid = 0.0 < numeric < 1.0 if strict else 0.0 <= numeric <= 1.0
    if not valid:
        bounds = "(0, 1)" if strict else "[0, 1]"
        raise PredictionIntegrityError(f"{name} must be within {bounds}")
    return numeric


def _validate_deadline(
    recorded_at: datetime,
    *,
    scheduled_at: datetime,
    market_end_at: datetime,
) -> int:
    if recorded_at < scheduled_at:
        raise PredictionDeadlineError("recorded_at precedes scheduled_at")
    if recorded_at > scheduled_at + timedelta(seconds=MAX_LATENESS_SECONDS):
        raise PredictionDeadlineError("recorded_at exceeds live prediction deadline")
    if recorded_at >= market_end_at:
        raise PredictionDeadlineError("recorded_at is at or after market end")
    return int((recorded_at - scheduled_at).total_seconds() * 1000)


def _book_quote(book: LiveBookInput | None, key: str) -> float | None:
    if book is None:
        return None
    value = book.state.get(key)
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
        return None
    return numeric


def _book_cutoff(book: LiveBookInput | None) -> datetime | None:
    if book is None:
        return None
    return max(
        _utc(book.bucket_at, "book bucket_at"),
        _utc(book.last_event_at, "book last_event_at"),
    )


def _validate_policy(policy: LivePolicySpec) -> None:
    if policy.horizon_seconds <= 0:
        raise PredictionIntegrityError("policy horizon_seconds must be positive")
    if policy.selected_offset_seconds <= 0:
        raise PredictionIntegrityError("policy selected_offset_seconds must be positive")
    _probability(policy.training_prior, "training_prior", strict=True)
    _require_sha256(policy.source_calibration_semantic_sha256, "source calibration semantic")
    _require_sha256(policy.source_backtest_semantic_sha256, "source backtest semantic")
    _require_sha256(policy.source_training_semantic_sha256, "source training semantic")
    _require_sha256(policy.policy_sha256, "policy_sha256")
    if policy.edge_policy == "trade_threshold":
        if policy.min_edge is None:
            raise PredictionIntegrityError("trade_threshold policy requires min_edge")
    elif policy.edge_policy == "no_trade":
        if policy.min_edge is not None:
            raise PredictionIntegrityError("no_trade policy requires min_edge=None")
    else:
        raise PredictionIntegrityError("unsupported edge_policy")


def _validate_identity(
    policy: LivePolicySpec,
    live_input: LiveMarketInput,
    *,
    condition_id: str,
    horizon_seconds: int,
    market_start_at: datetime,
    market_end_at: datetime,
    up_token_id: str,
    down_token_id: str,
) -> tuple[datetime, datetime, datetime]:
    if not condition_id:
        raise PredictionIntegrityError("condition_id is required")
    if live_input.condition_id != condition_id:
        raise PredictionIntegrityError("live input condition_id does not match market")
    if policy.horizon_seconds != horizon_seconds:
        raise PredictionIntegrityError("policy horizon_seconds does not match market")
    if horizon_seconds <= 0:
        raise PredictionIntegrityError("horizon_seconds must be positive")
    start = _utc(market_start_at, "market_start_at")
    end = _utc(market_end_at, "market_end_at")
    if end <= start:
        raise PredictionIntegrityError("market_end_at must be after market_start_at")
    if live_input.market_start_at != start or live_input.market_end_at != end:
        raise PredictionIntegrityError("live input market window does not match market")
    if live_input.up_token_id != up_token_id:
        raise PredictionIntegrityError("live input up_token_id does not match market")
    if live_input.down_token_id != down_token_id:
        raise PredictionIntegrityError("live input down_token_id does not match market")
    if not up_token_id or not down_token_id or up_token_id == down_token_id:
        raise PredictionIntegrityError("distinct Up and Down token ids are required")
    scheduled = start + timedelta(seconds=policy.selected_offset_seconds)
    if not start < scheduled < end:
        raise PredictionIntegrityError("policy scheduled_at must be inside market window")
    if live_input.scheduled_at != scheduled:
        raise PredictionIntegrityError("live input scheduled_at does not match policy")
    return start, end, scheduled


def _validate_live_input(live_input: LiveMarketInput, *, scheduled_at: datetime) -> None:
    _require_sha256(live_input.price_response_sha256, "price_response_sha256")
    _require_sha256(live_input.input_fingerprint, "input_fingerprint")
    downloaded_at = _utc(live_input.downloaded_at, "live input downloaded_at")
    if downloaded_at < scheduled_at:
        raise PredictionIntegrityError("live input downloaded_at precedes scheduled_at")
    if downloaded_at > scheduled_at + timedelta(seconds=MAX_LATENESS_SECONDS):
        raise PredictionIntegrityError("live input downloaded_at exceeds prediction deadline")

    predictor_probability = live_input.predictors.get("pm_up_price")
    if live_input.market_probability_observed:
        if live_input.market_probability is None:
            raise PredictionIntegrityError("observed market probability is missing value")
        if live_input.market_probability_observed_at is None:
            raise PredictionIntegrityError("observed market probability is missing timestamp")
        probability = _probability(live_input.market_probability, "market_probability")
        observed_at = _utc(
            live_input.market_probability_observed_at,
            "market_probability_observed_at",
        )
        if observed_at > scheduled_at:
            raise PredictionIntegrityError("market probability observed after scheduled_at")
        if predictor_probability is None or float(predictor_probability) != probability:
            raise PredictionIntegrityError("pm_up_price does not match observed market probability")
    else:
        if live_input.market_probability is not None:
            raise PredictionIntegrityError("unobserved market probability must be null")
        if live_input.market_probability_observed_at is not None:
            raise PredictionIntegrityError("unobserved market probability timestamp must be null")
        if predictor_probability is not None:
            raise PredictionIntegrityError("pm_up_price must be null when market price is missing")

    for name, book, token in (
        ("up", live_input.up_book, live_input.up_token_id),
        ("down", live_input.down_book, live_input.down_token_id),
    ):
        if book is None:
            continue
        if book.asset_id != token:
            raise PredictionIntegrityError(f"{name} book asset_id does not match token")
        if _book_cutoff(book) > scheduled_at:
            raise PredictionIntegrityError(f"{name} book cutoff exceeds scheduled_at")


def build_live_prediction(
    policy: LivePolicySpec,
    live_input: LiveMarketInput,
    *,
    condition_id: str,
    slug: str,
    horizon_seconds: int,
    market_start_at: datetime,
    market_end_at: datetime,
    up_token_id: str,
    down_token_id: str,
    recorded_at: datetime,
) -> LivePrediction:
    _validate_policy(policy)
    start, end, scheduled = _validate_identity(
        policy,
        live_input,
        condition_id=condition_id,
        horizon_seconds=horizon_seconds,
        market_start_at=market_start_at,
        market_end_at=market_end_at,
        up_token_id=up_token_id,
        down_token_id=down_token_id,
    )
    recorded = _utc(recorded_at, "recorded_at")
    lateness_ms = _validate_deadline(
        recorded,
        scheduled_at=scheduled,
        market_end_at=end,
    )
    _validate_live_input(live_input, scheduled_at=scheduled)
    if live_input.downloaded_at.astimezone(UTC) > recorded:
        raise PredictionIntegrityError("live input downloaded_at exceeds recorded_at")

    raw_probability = (
        _probability(live_input.market_probability, "market_probability")
        if live_input.market_probability_observed
        else _probability(policy.training_prior, "training_prior", strict=True)
    )
    calibrated_probability = apply_calibration(
        policy.calibration_fit,
        (raw_probability,),
    )[0]
    decision = edge_decision_from_predictors(
        live_input.predictors,
        calibrated_probability,
        policy.edge_config,
        policy.min_edge,
    )
    calibration_fit = asdict(policy.calibration_fit)
    edge_config = asdict(policy.edge_config)
    edge_decision: dict[str, Any] = asdict(decision)
    calibration_fit_sha256 = canonical_hash(calibration_fit)
    edge_config_sha256 = canonical_hash(edge_config)
    prediction_id = canonical_hash(
        {
            "condition_id": condition_id,
            "prediction_version": LIVE_PREDICTION_VERSION,
        }
    )

    values: dict[str, Any] = {
        "prediction_id": prediction_id,
        "prediction_version": LIVE_PREDICTION_VERSION,
        "live_input_version": LIVE_INPUT_VERSION,
        "condition_id": condition_id,
        "slug": slug,
        "horizon_seconds": horizon_seconds,
        "market_start_at": start,
        "market_end_at": end,
        "scheduled_at": scheduled,
        "recorded_at": recorded,
        "lateness_ms": lateness_ms,
        "up_token_id": up_token_id,
        "down_token_id": down_token_id,
        "source_calibration_run_id": policy.source_calibration_run_id,
        "source_calibration_semantic_sha256": policy.source_calibration_semantic_sha256,
        "source_backtest_run_id": policy.source_backtest_run_id,
        "source_backtest_semantic_sha256": policy.source_backtest_semantic_sha256,
        "source_training_run_id": policy.source_training_run_id,
        "source_training_semantic_sha256": policy.source_training_semantic_sha256,
        "calibration_version": policy.calibration_version,
        "edge_policy_version": policy.edge_policy_version,
        "source_feature_version": policy.source_feature_version,
        "source_label_version": policy.label_version,
        "selected_offset_seconds": policy.selected_offset_seconds,
        "policy_sha256": policy.policy_sha256,
        "calibration_fit": calibration_fit,
        "calibration_fit_sha256": calibration_fit_sha256,
        "edge_config": edge_config,
        "edge_config_sha256": edge_config_sha256,
        "edge_policy": policy.edge_policy,
        "min_edge": policy.min_edge,
        "training_prior": policy.training_prior,
        "raw_probability": raw_probability,
        "calibrated_probability": calibrated_probability,
        "predicted_target": decision.predicted_target,
        "predicted_side": decision.side,
        "market_probability_observed": live_input.market_probability_observed,
        "market_probability": live_input.market_probability,
        "market_probability_observed_at": live_input.market_probability_observed_at,
        "market_probability_downloaded_at": live_input.downloaded_at,
        "market_probability_source": live_input.price_source,
        "market_probability_dataset": live_input.price_dataset,
        "market_probability_request_params": dict(live_input.price_request_params),
        "market_probability_response_sha256": live_input.price_response_sha256,
        "up_best_bid": _book_quote(live_input.up_book, "best_bid"),
        "up_best_ask": _book_quote(live_input.up_book, "best_ask"),
        "up_book_cutoff_at": _book_cutoff(live_input.up_book),
        "up_book_fresh": live_input.up_book.fresh if live_input.up_book else False,
        "down_best_bid": _book_quote(live_input.down_book, "best_bid"),
        "down_best_ask": _book_quote(live_input.down_book, "best_ask"),
        "down_book_cutoff_at": _book_cutoff(live_input.down_book),
        "down_book_fresh": live_input.down_book.fresh if live_input.down_book else False,
        "selected_side": decision.side,
        "executable": decision.executable,
        "trade": decision.trade,
        "decision_reason": decision.reason,
        "selected_ask": decision.ask,
        "selected_bid": decision.bid,
        "selected_spread": decision.spread,
        "fee": decision.fee,
        "slippage_buffer": decision.slippage_buffer,
        "raw_edge": decision.raw_edge,
        "cost_adjusted_edge": decision.cost_adjusted_edge,
        "decision_min_edge": decision.min_edge,
        "edge_decision": edge_decision,
        "input_fingerprint": live_input.input_fingerprint,
    }
    semantic_sha256 = canonical_hash(values)
    return LivePrediction(semantic_sha256=semantic_sha256, **values)
