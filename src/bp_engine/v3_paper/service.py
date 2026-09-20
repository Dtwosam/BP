from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sqlalchemy import Connection, Engine, select

from bp_engine.calibration.calibrators import apply_calibration
from bp_engine.calibration.models import CalibrationFit
from bp_engine.features.hashing import canonical_hash
from bp_engine.features.sources import FeatureSourceReader, StateObservation
from bp_engine.features.v3_models import V3FeatureTarget
from bp_engine.features.v3_service import build_v3_feature
from bp_engine.live_prediction.models import LivePrediction
from bp_engine.live_prediction.repository import LivePredictionRepository
from bp_engine.storage.schema import live_predictions, polymarket_markets
from bp_engine.v3_research.policy import V3ExecutionBook, edge_decision_v3

V3_PAPER_PREDICTION_VERSION = "v3-frozen-paper-v1"
V3_PAPER_INPUT_VERSION = "v3-frozen-paper-input-v1"
V3_PAPER_EXECUTION_VERSION = "paper-execution-v3-frozen-v1"

FROZEN_RESEARCH_PLAN_VERSION = "v3-gate-b-preregister-v2"
FROZEN_PLAN_SHA256 = "f1480734cb4accf08fe0b3cfd0f15a733786dd008f8e76df75944e9dd39a9022"
FROZEN_SELECTION_SHA256 = "a088c61b291a67866af1c565ee936d9761e6a2936b49f121f616081284dcd508"
FROZEN_MODEL_SHA256 = "124627e15cab3997b8abe54ec5237450d976ab5682953f45a1399a76b6dae0e7"
FROZEN_HOLDOUT_EVIDENCE_SHA256 = "a093de346cde6a98cf056bdbc3f7c901c3570aff611363cda996a04fa8e4d66a"

FROZEN_CANDIDATE = "single_feature_btc_logistic"
FROZEN_FAMILY = "logistic"
FROZEN_OFFSET_SECONDS = 240
FROZEN_EDGE_POLICY = "trade_threshold"
FROZEN_MIN_EDGE = 0.075
FROZEN_FEE_RATE = 0.07
FROZEN_SLIPPAGE_BUFFER = 0.01
FROZEN_MAX_BOOK_AGE_SECONDS = 10
FROZEN_HORIZON_SECONDS = 300
OFFICIAL_LABEL_VERSION = "official-outcome-v1"


class V3PaperIntegrityError(RuntimeError):
    """Raised when the frozen V3 paper contract would drift."""


@dataclass(frozen=True)
class V3PaperActivation:
    activated_at: datetime
    candidate_head: str
    model_sha256: str
    prediction_version: str
    execution_version: str
    paper_starting_cash_usd: str
    paper_target_notional_usd: str


@dataclass(frozen=True)
class V3PaperMarket:
    condition_id: str
    slug: str
    horizon_seconds: int
    market_start_at: datetime
    market_end_at: datetime
    scheduled_at: datetime
    up_token_id: str
    down_token_id: str


@dataclass(frozen=True)
class V3PaperCycleStats:
    cycle_at: datetime
    due_markets: int
    created_predictions: int
    existing_predictions: int
    missed_predictions: int
    failed_markets: int


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise V3PaperIntegrityError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_activation(path: str | Path) -> V3PaperActivation:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise V3PaperIntegrityError("activation manifest must contain an object")
    raw_at = str(payload.get("activated_at", "")).replace("Z", "+00:00")
    try:
        activated_at = datetime.fromisoformat(raw_at)
    except ValueError as exc:
        raise V3PaperIntegrityError("activation activated_at is invalid") from exc
    activated_at = _utc(activated_at, "activated_at")

    activation = V3PaperActivation(
        activated_at=activated_at,
        candidate_head=str(payload.get("candidate_head", "")),
        model_sha256=str(payload.get("model_sha256", "")),
        prediction_version=str(payload.get("prediction_version", "")),
        execution_version=str(payload.get("execution_version", "")),
        paper_starting_cash_usd=str(payload.get("paper_starting_cash_usd", "")),
        paper_target_notional_usd=str(payload.get("paper_target_notional_usd", "")),
    )
    if len(activation.candidate_head) != 40:
        raise V3PaperIntegrityError("activation candidate_head must be exact Git SHA")
    if activation.model_sha256 != FROZEN_MODEL_SHA256:
        raise V3PaperIntegrityError("activation model SHA changed")
    if activation.prediction_version != V3_PAPER_PREDICTION_VERSION:
        raise V3PaperIntegrityError("activation prediction version changed")
    if activation.execution_version != V3_PAPER_EXECUTION_VERSION:
        raise V3PaperIntegrityError("activation execution version changed")
    if activation.paper_starting_cash_usd != "100.00":
        raise V3PaperIntegrityError("paper starting cash changed")
    if activation.paper_target_notional_usd != "5.00":
        raise V3PaperIntegrityError("paper target notional changed")
    return activation


def load_frozen_model(
    path: str | Path,
    *,
    expected_sha256: str = FROZEN_MODEL_SHA256,
) -> dict[str, Any]:
    source = Path(path)
    digest = _sha256_file(source)
    if digest != expected_sha256:
        raise V3PaperIntegrityError(
            f"frozen model SHA mismatch: expected={expected_sha256} actual={digest}"
        )
    bundle = joblib.load(source)
    if not isinstance(bundle, dict):
        raise V3PaperIntegrityError("frozen V3 artifact must contain a mapping")

    expected = {
        "research_plan_version": FROZEN_RESEARCH_PLAN_VERSION,
        "plan_sha256": FROZEN_PLAN_SHA256,
        "candidate": FROZEN_CANDIDATE,
        "family": FROZEN_FAMILY,
        "offset_seconds": FROZEN_OFFSET_SECONDS,
        "edge_policy": FROZEN_EDGE_POLICY,
        "selected_min_edge": FROZEN_MIN_EDGE,
        "fee_rate": FROZEN_FEE_RATE,
        "slippage_buffer": FROZEN_SLIPPAGE_BUFFER,
        "max_selected_book_age_seconds": FROZEN_MAX_BOOK_AGE_SECONDS,
    }
    for key, value in expected.items():
        if bundle.get(key) != value:
            raise V3PaperIntegrityError(
                f"frozen V3 model contract changed for {key}: {bundle.get(key)!r}"
            )
    names = tuple(str(name) for name in bundle.get("predictor_names", ()))
    if not names or names[0] != "coinbase_return_from_market_start":
        raise V3PaperIntegrityError("frozen V3 predictor names changed")
    allowed = {
        "coinbase_return_from_market_start",
        "missing__coinbase_market_start_missing",
        "missing__coinbase_market_start_stale",
        "missing__coinbase_current_missing",
        "missing__coinbase_current_stale",
    }
    if any(name not in allowed for name in names):
        raise V3PaperIntegrityError("unexpected predictor entered frozen V3 model")
    if "calibration_fit" not in bundle:
        raise V3PaperIntegrityError("frozen V3 calibration fit is missing")
    return bundle


def _predictors(feature) -> dict[str, float | None]:
    values: dict[str, float | None] = {}
    for key, value in feature.features.items():
        if key.startswith("pm_") or "polymarket" in key.lower():
            raise V3PaperIntegrityError(f"Polymarket predictor entered V3 features: {key}")
        values[key] = None if value is None else float(value)
    for key, missing in feature.missing_flags.items():
        if key.startswith("pm_") or "polymarket" in key.lower():
            raise V3PaperIntegrityError(f"Polymarket missing flag entered V3 features: {key}")
        values[f"missing__{key}"] = 1.0 if missing else 0.0
    return values


def _model_probability(
    predictors: Mapping[str, float | None],
    bundle: Mapping[str, Any],
) -> tuple[float, float]:
    names = tuple(str(name) for name in bundle["predictor_names"])
    matrix = np.asarray([[predictors.get(name) for name in names]], dtype=float)
    transformed = bundle["imputer"].transform(matrix)
    transformed = bundle["scaler"].transform(transformed)
    raw = float(bundle["estimator"].predict_proba(transformed)[0, 1])
    if not math.isfinite(raw) or not 0.0 <= raw <= 1.0:
        raise V3PaperIntegrityError("frozen V3 model produced invalid probability")
    payload = bundle["calibration_fit"]
    fit = CalibrationFit(
        method=str(payload["method"]),
        intercept=payload.get("intercept"),
        coefficient=payload.get("coefficient"),
    )
    calibrated = float(apply_calibration(fit, (raw,))[0])
    if not math.isfinite(calibrated) or not 0.0 <= calibrated <= 1.0:
        raise V3PaperIntegrityError("frozen V3 calibration produced invalid probability")
    return raw, calibrated


def _quote(observation: StateObservation | None, key: str) -> float | None:
    if observation is None:
        return None
    raw = observation.state.get(key)
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        return None
    return value


def _book_descriptor(observation: StateObservation | None) -> dict[str, Any] | None:
    if observation is None:
        return None
    return {
        "row_id": observation.row_id,
        "bucket_at": observation.bucket_at,
        "last_event_at": observation.last_event_at,
        "asset_id": observation.asset_id,
        "fresh": observation.fresh,
        "age_seconds": observation.age_seconds,
        "state": dict(observation.state),
    }


def _books(
    connection: Connection,
    market: V3PaperMarket,
) -> tuple[V3ExecutionBook, StateObservation | None, StateObservation | None]:
    reader = FeatureSourceReader(state_fresh_seconds=FROZEN_MAX_BOOK_AGE_SECONDS)
    up = reader.latest_state(
        connection,
        source="polymarket",
        stream="market",
        instrument=market.condition_id,
        asset_id=market.up_token_id,
        feature_at=market.scheduled_at,
    )
    down = reader.latest_state(
        connection,
        source="polymarket",
        stream="market",
        instrument=market.condition_id,
        asset_id=market.down_token_id,
        feature_at=market.scheduled_at,
    )
    return (
        V3ExecutionBook(
            up_best_bid=_quote(up, "best_bid"),
            up_best_ask=_quote(up, "best_ask"),
            up_fresh=bool(up is not None and up.fresh),
            down_best_bid=_quote(down, "best_bid"),
            down_best_ask=_quote(down, "best_ask"),
            down_fresh=bool(down is not None and down.fresh),
        ),
        up,
        down,
    )


def _cutoff(observation: StateObservation | None) -> datetime | None:
    if observation is None:
        return None
    return max(
        _stored_utc(observation.bucket_at),
        _stored_utc(observation.last_event_at),
    )


def build_v3_paper_prediction(
    connection: Connection,
    *,
    market: V3PaperMarket,
    bundle: Mapping[str, Any],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> LivePrediction:
    started = _utc(clock(), "clock")
    if started < market.scheduled_at:
        raise V3PaperIntegrityError("prediction cannot run before scheduled time")
    if started > market.scheduled_at + timedelta(seconds=10):
        raise V3PaperIntegrityError("prediction started after prospective deadline")
    if started >= market.market_end_at:
        raise V3PaperIntegrityError("prediction reached market end")

    feature = build_v3_feature(
        connection,
        V3FeatureTarget(
            condition_id=market.condition_id,
            slug=market.slug,
            horizon_seconds=market.horizon_seconds,
            market_start_at=market.market_start_at,
            market_end_at=market.market_end_at,
        ),
        market.scheduled_at,
        generated_at=started,
    )
    predictors = _predictors(feature)
    raw_probability, calibrated_probability = _model_probability(predictors, bundle)
    book, up_state, down_state = _books(connection, market)
    decision = edge_decision_v3(
        calibrated_probability_up=calibrated_probability,
        book=book,
        fee_rate=FROZEN_FEE_RATE,
        slippage_buffer=FROZEN_SLIPPAGE_BUFFER,
        min_edge=FROZEN_MIN_EDGE,
    )

    recorded = _utc(clock(), "clock")
    if recorded > market.scheduled_at + timedelta(seconds=10):
        raise V3PaperIntegrityError("prediction exceeded 10-second prospective deadline")
    if recorded >= market.market_end_at:
        raise V3PaperIntegrityError("prediction reached market end")

    calibration_fit = dict(bundle["calibration_fit"])
    edge_config = {
        "fee_rate": FROZEN_FEE_RATE,
        "slippage_buffer": FROZEN_SLIPPAGE_BUFFER,
        "min_edge": FROZEN_MIN_EDGE,
        "max_selected_book_age_seconds": FROZEN_MAX_BOOK_AGE_SECONDS,
    }
    policy_values = {
        "model_sha256": FROZEN_MODEL_SHA256,
        "selection_sha256": FROZEN_SELECTION_SHA256,
        "candidate": FROZEN_CANDIDATE,
        "offset_seconds": FROZEN_OFFSET_SECONDS,
        "edge_policy": FROZEN_EDGE_POLICY,
        "min_edge": FROZEN_MIN_EDGE,
        "fee_rate": FROZEN_FEE_RATE,
        "slippage_buffer": FROZEN_SLIPPAGE_BUFFER,
        "max_selected_book_age_seconds": FROZEN_MAX_BOOK_AGE_SECONDS,
    }
    books_descriptor = {
        "up": _book_descriptor(up_state),
        "down": _book_descriptor(down_state),
    }
    input_fingerprint = canonical_hash(
        {
            "feature_hash": feature.feature_hash,
            "feature_input_fingerprint": feature.input_fingerprint,
            "books": books_descriptor,
            "model_sha256": FROZEN_MODEL_SHA256,
        }
    )
    price_response_sha256 = canonical_hash(books_descriptor)
    prediction_id = canonical_hash(
        {
            "condition_id": market.condition_id,
            "prediction_version": V3_PAPER_PREDICTION_VERSION,
        }
    )
    edge_decision = asdict(decision)
    edge_decision.update(
        {
            "forecast_source": "frozen_v3_btc_model",
            "polymarket_role": "execution_only",
            "training_prior_not_applicable": True,
        }
    )
    values: dict[str, Any] = {
        "prediction_id": prediction_id,
        "prediction_version": V3_PAPER_PREDICTION_VERSION,
        "live_input_version": V3_PAPER_INPUT_VERSION,
        "condition_id": market.condition_id,
        "slug": market.slug,
        "horizon_seconds": market.horizon_seconds,
        "market_start_at": market.market_start_at,
        "market_end_at": market.market_end_at,
        "scheduled_at": market.scheduled_at,
        "recorded_at": recorded,
        "lateness_ms": int((recorded - market.scheduled_at).total_seconds() * 1000),
        "up_token_id": market.up_token_id,
        "down_token_id": market.down_token_id,
        "source_calibration_run_id": "v3-gate-b-selection",
        "source_calibration_semantic_sha256": FROZEN_SELECTION_SHA256,
        "source_backtest_run_id": "v3-gate-b-holdout",
        "source_backtest_semantic_sha256": FROZEN_HOLDOUT_EVIDENCE_SHA256,
        "source_training_run_id": "v3-gate-b-frozen-model",
        "source_training_semantic_sha256": FROZEN_MODEL_SHA256,
        "calibration_version": f"v3-frozen-{calibration_fit['method']}",
        "edge_policy_version": "v3-gate-b-frozen-edge-v1",
        "source_feature_version": "core-v3-btc-native",
        "source_label_version": OFFICIAL_LABEL_VERSION,
        "selected_offset_seconds": FROZEN_OFFSET_SECONDS,
        "policy_sha256": canonical_hash(policy_values),
        "calibration_fit": calibration_fit,
        "calibration_fit_sha256": canonical_hash(calibration_fit),
        "edge_config": edge_config,
        "edge_config_sha256": canonical_hash(edge_config),
        "edge_policy": FROZEN_EDGE_POLICY,
        "min_edge": FROZEN_MIN_EDGE,
        "training_prior": 0.5,
        "raw_probability": raw_probability,
        "calibrated_probability": calibrated_probability,
        "predicted_target": decision.predicted_target,
        "predicted_side": decision.side,
        "market_probability_observed": False,
        "market_probability": None,
        "market_probability_observed_at": None,
        "market_probability_downloaded_at": recorded,
        "market_probability_source": "polymarket_compact_book",
        "market_probability_dataset": "market_state_1s",
        "market_probability_request_params": {
            "role": "execution_only",
            "scheduled_at": market.scheduled_at.isoformat(),
        },
        "market_probability_response_sha256": price_response_sha256,
        "up_best_bid": book.up_best_bid,
        "up_best_ask": book.up_best_ask,
        "up_book_cutoff_at": _cutoff(up_state),
        "up_book_fresh": book.up_fresh,
        "down_best_bid": book.down_best_bid,
        "down_best_ask": book.down_best_ask,
        "down_book_cutoff_at": _cutoff(down_state),
        "down_book_fresh": book.down_fresh,
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
        "input_fingerprint": input_fingerprint,
    }
    return LivePrediction(
        semantic_sha256=canonical_hash(values),
        **values,
    )


def discover_due_markets(
    connection: Connection,
    *,
    now: datetime,
    activation: V3PaperActivation,
    max_lateness_seconds: int = 10,
) -> tuple[V3PaperMarket, ...]:
    current = _utc(now, "now")
    if max_lateness_seconds < 0:
        raise ValueError("max_lateness_seconds must be non-negative")

    existing = set(
        connection.execute(
            select(live_predictions.c.condition_id).where(
                live_predictions.c.prediction_version
                == V3_PAPER_PREDICTION_VERSION
            )
        ).scalars()
    )
    rows = connection.execute(
        select(polymarket_markets)
        .where(
            polymarket_markets.c.horizon_seconds == FROZEN_HORIZON_SECONDS,
            polymarket_markets.c.start_at >= activation.activated_at,
            polymarket_markets.c.active.is_(True),
            polymarket_markets.c.closed.is_(False),
            polymarket_markets.c.resolved_outcome.is_(None),
        )
        .order_by(polymarket_markets.c.start_at, polymarket_markets.c.condition_id)
    ).mappings()

    due: list[V3PaperMarket] = []
    for row in rows:
        condition_id = str(row["condition_id"] or "")
        if not condition_id or condition_id in existing:
            continue
        start = _stored_utc(row["start_at"])
        end = _stored_utc(row["end_at"])
        scheduled = start + timedelta(seconds=FROZEN_OFFSET_SECONDS)
        deadline = scheduled + timedelta(seconds=max_lateness_seconds)
        if current < scheduled or current > deadline or current >= end:
            continue
        up_token = str(row["up_token_id"] or "")
        down_token = str(row["down_token_id"] or "")
        if not up_token or not down_token or up_token == down_token:
            continue
        due.append(
            V3PaperMarket(
                condition_id=condition_id,
                slug=str(row["slug"]),
                horizon_seconds=FROZEN_HORIZON_SECONDS,
                market_start_at=start,
                market_end_at=end,
                scheduled_at=scheduled,
                up_token_id=up_token,
                down_token_id=down_token,
            )
        )
    return tuple(sorted(due, key=lambda item: (item.scheduled_at, item.condition_id)))


class V3PaperPredictionService:
    def __init__(
        self,
        *,
        engine: Engine,
        activation: V3PaperActivation,
        model_bundle: Mapping[str, Any],
        repository: LivePredictionRepository | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._engine = engine
        self._activation = activation
        self._model_bundle = model_bundle
        self._repository = repository or LivePredictionRepository()
        self._clock = clock

    def run_once(self, *, now: datetime) -> V3PaperCycleStats:
        current = _utc(now, "now")
        with self._engine.begin() as connection:
            due = discover_due_markets(
                connection,
                now=current,
                activation=self._activation,
            )

        created = existing = missed = failed = 0
        for market in due:
            try:
                with self._engine.begin() as connection:
                    prediction = build_v3_paper_prediction(
                        connection,
                        market=market,
                        bundle=self._model_bundle,
                        clock=self._clock,
                    )
                    result = self._repository.store(connection, prediction)
                created += int(result.created)
                existing += int(result.existing)
            except V3PaperIntegrityError as exc:
                if "deadline" in str(exc) or "market end" in str(exc):
                    missed += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

        return V3PaperCycleStats(
            cycle_at=current,
            due_markets=len(due),
            created_predictions=created,
            existing_predictions=existing,
            missed_predictions=missed,
            failed_markets=failed,
        )
