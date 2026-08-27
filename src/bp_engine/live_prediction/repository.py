from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, insert, select

from bp_engine.live_prediction.models import LivePrediction, LivePredictionEvaluation
from bp_engine.storage.schema import live_prediction_evaluations, live_predictions

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LEDGER_QUANTUM = Decimal("0.000000000000000001")
_PREDICTION_NUMERIC_FIELDS = frozenset(
    {
        "min_edge",
        "training_prior",
        "raw_probability",
        "calibrated_probability",
        "market_probability",
        "up_best_bid",
        "up_best_ask",
        "down_best_bid",
        "down_best_ask",
        "selected_ask",
        "selected_bid",
        "selected_spread",
        "fee",
        "slippage_buffer",
        "raw_edge",
        "cost_adjusted_edge",
        "decision_min_edge",
    }
)
_EVALUATION_NUMERIC_FIELDS = (
    "raw_log_loss",
    "raw_brier",
    "calibrated_log_loss",
    "calibrated_brier",
    "hypothetical_gross_pnl",
    "hypothetical_assumed_cost_pnl",
)
_LEDGER_NUMERIC_FIELDS = _PREDICTION_NUMERIC_FIELDS | frozenset(
    _EVALUATION_NUMERIC_FIELDS
)


class LivePredictionConflict(RuntimeError):
    """Raised when an immutable live prediction would be rewritten."""


class LivePredictionEvaluationConflict(RuntimeError):
    """Raised when an immutable live evaluation would be rewritten."""


@dataclass(frozen=True)
class LiveLedgerStoreResult:
    created: bool
    existing: bool


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_sha256(value: str, name: str) -> None:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be a 64-character lowercase hex SHA-256 digest")


def _finite(value: float, name: str) -> float:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite")
    return numeric


def _probability(value: float, name: str, *, strict: bool = False) -> float:
    numeric = _finite(value, name)
    valid = 0.0 < numeric < 1.0 if strict else 0.0 <= numeric <= 1.0
    if not valid:
        bounds = "(0, 1)" if strict else "[0, 1]"
        raise ValueError(f"{name} must be within {bounds}")
    return numeric


def _ledger_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError("ledger numeric values must not be booleans")
    numeric = value if isinstance(value, Decimal) else Decimal(str(value))
    if not numeric.is_finite():
        raise ValueError("ledger numeric values must be finite")
    return numeric.quantize(_LEDGER_QUANTUM)


def _ledger_numeric_equal(stored: Any, expected: Any) -> bool:
    if stored is None or expected is None:
        return stored is expected
    try:
        return _ledger_decimal(stored) == _ledger_decimal(expected)
    except (ArithmeticError, TypeError, ValueError):
        return False


def _normalized(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return Decimal(str(float(value)))
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, Mapping):
        return {str(key): _normalized(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalized(item) for item in value]
    return value


def _record_values(record: LivePrediction | LivePredictionEvaluation) -> dict[str, Any]:
    return asdict(record)


def _evaluation_record_values(evaluation: LivePredictionEvaluation) -> dict[str, Any]:
    values = _record_values(evaluation)
    for name in _EVALUATION_NUMERIC_FIELDS:
        value = values[name]
        if value is not None:
            values[name] = Decimal(str(value))
    return values


def _stored_values(row: Mapping[str, Any], columns: set[str]) -> dict[str, Any]:
    return {name: row[name] for name in columns}


def _semantically_equal(
    row: Mapping[str, Any],
    values: Mapping[str, Any],
) -> bool:
    stored = _stored_values(row, set(values))
    for name, expected in values.items():
        actual = stored[name]
        if name in _LEDGER_NUMERIC_FIELDS:
            if not _ledger_numeric_equal(actual, expected):
                return False
        elif _normalized(actual) != _normalized(expected):
            return False
    return True


class LivePredictionRepository:
    def get(
        self,
        connection: Connection,
        *,
        condition_id: str,
        prediction_version: str,
    ) -> Mapping[str, Any] | None:
        return connection.execute(
            select(live_predictions).where(
                live_predictions.c.condition_id == condition_id,
                live_predictions.c.prediction_version == prediction_version,
            )
        ).mappings().one_or_none()

    def get_by_id(
        self,
        connection: Connection,
        prediction_id: str,
    ) -> Mapping[str, Any] | None:
        return connection.execute(
            select(live_predictions).where(
                live_predictions.c.prediction_id == prediction_id
            )
        ).mappings().one_or_none()

    def store(
        self,
        connection: Connection,
        prediction: LivePrediction,
    ) -> LiveLedgerStoreResult:
        self._validate(prediction)
        values = _record_values(prediction)
        existing = self.get(
            connection,
            condition_id=prediction.condition_id,
            prediction_version=prediction.prediction_version,
        )
        if existing is not None:
            if not _semantically_equal(existing, values):
                raise LivePredictionConflict(
                    "conflicting live prediction "
                    f"condition_id={prediction.condition_id} "
                    f"prediction_version={prediction.prediction_version}"
                )
            return LiveLedgerStoreResult(created=False, existing=True)

        existing_id = self.get_by_id(connection, prediction.prediction_id)
        if existing_id is not None:
            raise LivePredictionConflict(
                f"conflicting live prediction prediction_id={prediction.prediction_id}"
            )

        connection.execute(insert(live_predictions).values(**values))
        return LiveLedgerStoreResult(created=True, existing=False)

    @staticmethod
    def _validate(prediction: LivePrediction) -> None:
        if not prediction.condition_id:
            raise ValueError("condition_id must not be empty")
        if not prediction.prediction_version:
            raise ValueError("prediction_version must not be empty")
        if not prediction.live_input_version:
            raise ValueError("live_input_version must not be empty")
        if not prediction.up_token_id or not prediction.down_token_id:
            raise ValueError("Up and Down token ids must not be empty")
        if prediction.up_token_id == prediction.down_token_id:
            raise ValueError("Up and Down token ids must be distinct")
        if prediction.horizon_seconds <= 0:
            raise ValueError("horizon_seconds must be positive")
        if prediction.selected_offset_seconds <= 0:
            raise ValueError("selected_offset_seconds must be positive")

        required_times = (
            ("market_start_at", prediction.market_start_at),
            ("market_end_at", prediction.market_end_at),
            ("scheduled_at", prediction.scheduled_at),
            ("recorded_at", prediction.recorded_at),
            ("market_probability_downloaded_at", prediction.market_probability_downloaded_at),
        )
        for name, value in required_times:
            _require_aware(value, name)
        for name, value in (
            ("market_probability_observed_at", prediction.market_probability_observed_at),
            ("up_book_cutoff_at", prediction.up_book_cutoff_at),
            ("down_book_cutoff_at", prediction.down_book_cutoff_at),
        ):
            if value is not None:
                _require_aware(value, name)

        if prediction.market_end_at <= prediction.market_start_at:
            raise ValueError("market_end_at must be after market_start_at")
        if not prediction.market_start_at < prediction.scheduled_at < prediction.market_end_at:
            raise ValueError("scheduled_at must be strictly inside market window")
        if prediction.recorded_at < prediction.scheduled_at:
            raise ValueError("recorded_at must not precede scheduled_at")
        if prediction.recorded_at >= prediction.market_end_at:
            raise ValueError("recorded_at must be before market_end_at")
        if not 0 <= prediction.lateness_ms <= 10_000:
            raise ValueError("lateness_ms must be between 0 and 10000")
        actual_lateness_ms = int(
            (prediction.recorded_at - prediction.scheduled_at).total_seconds() * 1000
        )
        if actual_lateness_ms != prediction.lateness_ms:
            raise ValueError("lateness_ms must match recorded_at minus scheduled_at")

        hashes = (
            ("prediction_id", prediction.prediction_id),
            ("semantic_sha256", prediction.semantic_sha256),
            (
                "source_calibration_semantic_sha256",
                prediction.source_calibration_semantic_sha256,
            ),
            ("source_backtest_semantic_sha256", prediction.source_backtest_semantic_sha256),
            ("source_training_semantic_sha256", prediction.source_training_semantic_sha256),
            ("policy_sha256", prediction.policy_sha256),
            ("calibration_fit_sha256", prediction.calibration_fit_sha256),
            ("edge_config_sha256", prediction.edge_config_sha256),
            (
                "market_probability_response_sha256",
                prediction.market_probability_response_sha256,
            ),
            ("input_fingerprint", prediction.input_fingerprint),
        )
        for name, value in hashes:
            _require_sha256(value, name)

        _probability(prediction.training_prior, "training_prior", strict=True)
        _probability(prediction.raw_probability, "raw_probability")
        _probability(prediction.calibrated_probability, "calibrated_probability")
        if prediction.market_probability is not None:
            _probability(prediction.market_probability, "market_probability")
        if prediction.predicted_target not in {0, 1}:
            raise ValueError("predicted_target must be 0 or 1")
        if prediction.predicted_side not in {"up", "down"}:
            raise ValueError("predicted_side must be up or down")
        if prediction.selected_side not in {"up", "down"}:
            raise ValueError("selected_side must be up or down")
        if prediction.market_probability_observed:
            if (
                prediction.market_probability is None
                or prediction.market_probability_observed_at is None
            ):
                raise ValueError("observed market probability requires value and timestamp")
        elif (
            prediction.market_probability is not None
            or prediction.market_probability_observed_at is not None
        ):
            raise ValueError("missing market probability must keep value and timestamp null")

        for name, value in (
            ("min_edge", prediction.min_edge),
            ("up_best_bid", prediction.up_best_bid),
            ("up_best_ask", prediction.up_best_ask),
            ("down_best_bid", prediction.down_best_bid),
            ("down_best_ask", prediction.down_best_ask),
            ("selected_ask", prediction.selected_ask),
            ("selected_bid", prediction.selected_bid),
            ("selected_spread", prediction.selected_spread),
            ("raw_edge", prediction.raw_edge),
            ("cost_adjusted_edge", prediction.cost_adjusted_edge),
            ("decision_min_edge", prediction.decision_min_edge),
        ):
            if value is not None:
                _finite(value, name)
        _finite(prediction.fee, "fee")
        _finite(prediction.slippage_buffer, "slippage_buffer")


class LivePredictionEvaluationRepository:
    def get(
        self,
        connection: Connection,
        *,
        prediction_id: str,
        label_version: str,
    ) -> Mapping[str, Any] | None:
        return connection.execute(
            select(live_prediction_evaluations).where(
                live_prediction_evaluations.c.prediction_id == prediction_id,
                live_prediction_evaluations.c.label_version == label_version,
            )
        ).mappings().one_or_none()

    def store(
        self,
        connection: Connection,
        evaluation: LivePredictionEvaluation,
    ) -> LiveLedgerStoreResult:
        self._validate(evaluation)
        values = _evaluation_record_values(evaluation)
        existing = self.get(
            connection,
            prediction_id=evaluation.prediction_id,
            label_version=evaluation.label_version,
        )
        if existing is not None:
            if not _semantically_equal(existing, values):
                raise LivePredictionEvaluationConflict(
                    "conflicting live prediction evaluation "
                    f"prediction_id={evaluation.prediction_id} "
                    f"label_version={evaluation.label_version}"
                )
            return LiveLedgerStoreResult(created=False, existing=True)

        parent = connection.execute(
            select(live_predictions.c.prediction_id).where(
                live_predictions.c.prediction_id == evaluation.prediction_id
            )
        ).scalar_one_or_none()
        if parent is None:
            raise ValueError(
                f"prediction_id does not reference stored prediction: {evaluation.prediction_id}"
            )

        connection.execute(insert(live_prediction_evaluations).values(**values))
        return LiveLedgerStoreResult(created=True, existing=False)

    @staticmethod
    def _validate(evaluation: LivePredictionEvaluation) -> None:
        if not evaluation.label_version:
            raise ValueError("label_version must not be empty")
        if not evaluation.label_source:
            raise ValueError("label_source must not be empty")
        _require_sha256(evaluation.prediction_id, "prediction_id")
        _require_sha256(
            evaluation.label_source_snapshot_sha256,
            "label_source_snapshot_sha256",
        )
        _require_sha256(evaluation.semantic_sha256, "semantic_sha256")
        _require_aware(evaluation.label_source_observed_at, "label_source_observed_at")
        _require_aware(evaluation.evaluated_at, "evaluated_at")
        if evaluation.evaluated_at < evaluation.label_source_observed_at:
            raise ValueError("evaluated_at must not precede label_source_observed_at")
        if evaluation.official_outcome not in {"Up", "Down"}:
            raise ValueError("official_outcome must be Up or Down")
        expected_target = 1 if evaluation.official_outcome == "Up" else 0
        if evaluation.official_target != expected_target:
            raise ValueError("official_target must match official_outcome")
        for name, value in (
            ("raw_log_loss", evaluation.raw_log_loss),
            ("raw_brier", evaluation.raw_brier),
            ("calibrated_log_loss", evaluation.calibrated_log_loss),
            ("calibrated_brier", evaluation.calibrated_brier),
        ):
            if _finite(value, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        for name, value in (
            ("hypothetical_gross_pnl", evaluation.hypothetical_gross_pnl),
            (
                "hypothetical_assumed_cost_pnl",
                evaluation.hypothetical_assumed_cost_pnl,
            ),
        ):
            if value is not None:
                _finite(value, name)
