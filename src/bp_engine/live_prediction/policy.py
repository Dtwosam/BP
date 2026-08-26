from __future__ import annotations

import math
import string
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, select

from bp_engine.backtesting.predictor import (
    ModelSpecIntegrityError,
    SourceTrainingRunNotFound,
    load_model_spec,
)
from bp_engine.calibration.models import (
    CALIBRATION_VERSION,
    EDGE_POLICY_VERSION,
    CalibrationFit,
    EdgeConfig,
)
from bp_engine.calibration.repository import CalibrationEdgeRunRepository
from bp_engine.calibration.source import (
    BacktestSourceIntegrityError,
    BacktestSourceNotFound,
    load_backtest_source_spec,
)
from bp_engine.features.hashing import canonical_hash
from bp_engine.live_prediction.models import LIVE_INPUT_VERSION as _LIVE_INPUT_VERSION
from bp_engine.live_prediction.models import (
    LIVE_PREDICTION_VERSION as _LIVE_PREDICTION_VERSION,
)
from bp_engine.live_prediction.models import LivePolicySpec
from bp_engine.storage.schema import market_labels

LIVE_PREDICTION_VERSION = _LIVE_PREDICTION_VERSION
LIVE_INPUT_VERSION = _LIVE_INPUT_VERSION


class LivePolicyNotFound(LookupError):
    """Raised when the requested immutable Phase 9 policy source is absent."""


class LivePolicyIntegrityError(ValueError):
    """Raised when a prospective live policy source violates frozen provenance."""


def _mapping(name: str, value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LivePolicyIntegrityError(f"{name} must be a mapping")
    return value


def _sequence(name: str, value: object) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise LivePolicyIntegrityError(f"{name} must be a sequence")
    return value


def _ids(name: str, value: object) -> tuple[str, ...]:
    items = _sequence(name, value)
    result = tuple(str(item) for item in items)
    if not result or any(not item for item in result):
        raise LivePolicyIntegrityError(f"{name} must contain condition ids")
    if len(result) != len(set(result)):
        raise LivePolicyIntegrityError(f"{name} contains duplicate condition ids")
    return result


def _sha256(name: str, value: object) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise LivePolicyIntegrityError(f"{name} must be a 64-character SHA-256")
    if any(char not in string.hexdigits for char in value):
        raise LivePolicyIntegrityError(f"{name} must be hexadecimal SHA-256")
    return value.lower()


def _aware_utc(value: object, name: str) -> datetime:
    if isinstance(value, str):
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise LivePolicyIntegrityError(f"{name} must be an ISO datetime") from exc
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise LivePolicyIntegrityError(f"{name} must be a datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _require_equal(name: str, stored: object, report: object) -> None:
    if stored != report:
        raise LivePolicyIntegrityError(
            f"source report {name} does not match stored immutable column"
        )


def _validate_stored_report(stored: Mapping[str, Any]) -> Mapping[str, Any]:
    report = _mapping("report", stored["report"])
    scalar_fields = (
        "run_id",
        "calibration_version",
        "edge_policy_version",
        "source_backtest_run_id",
        "source_backtest_semantic_sha256",
        "source_training_run_id",
        "source_training_semantic_sha256",
        "dataset_version",
        "feature_version",
        "label_version",
        "horizon_seconds",
        "dataset_sha256",
        "config_sha256",
        "source_plan_sha256",
        "semantic_sha256",
    )
    for field in scalar_fields:
        if field not in report:
            raise LivePolicyIntegrityError(f"source report {field} is missing")
        _require_equal(field, stored[field], report[field])

    _require_equal("config", stored["config"], report.get("config"))
    stored_membership = tuple(
        _sha256("source_fold_membership_sha256 entry", item)
        for item in _sequence(
            "source_fold_membership_sha256",
            stored["source_fold_membership_sha256"],
        )
    )
    report_membership = tuple(
        _sha256("report source_fold_membership_sha256 entry", item)
        for item in _sequence(
            "report source_fold_membership_sha256",
            report.get("source_fold_membership_sha256"),
        )
    )
    _require_equal(
        "source_fold_membership_sha256",
        stored_membership,
        report_membership,
    )

    start = _aware_utc(stored["requested_start"], "requested_start")
    end = _aware_utc(stored["requested_end"], "requested_end")
    if end <= start:
        raise LivePolicyIntegrityError("requested_end must be after requested_start")
    if _aware_utc(report.get("start"), "report start") != start:
        raise LivePolicyIntegrityError(
            "source report start does not match stored immutable column"
        )
    if _aware_utc(report.get("end"), "report end") != end:
        raise LivePolicyIntegrityError(
            "source report end does not match stored immutable column"
        )
    return report


def _edge_config(value: object) -> EdgeConfig:
    payload = _mapping("edge config", value)
    expected_keys = {
        "fee_rate",
        "slippage_buffer",
        "min_edge_grid",
        "min_validation_trades",
        "max_spread",
    }
    if set(payload) != expected_keys:
        raise LivePolicyIntegrityError("edge config has unexpected fields")
    grid = _sequence("edge config min_edge_grid", payload["min_edge_grid"])
    min_validation_trades = payload["min_validation_trades"]
    if isinstance(min_validation_trades, bool) or not isinstance(
        min_validation_trades, int
    ):
        raise LivePolicyIntegrityError(
            "edge config min_validation_trades must be an integer"
        )
    try:
        return EdgeConfig(
            fee_rate=float(payload["fee_rate"]),
            slippage_buffer=float(payload["slippage_buffer"]),
            min_edge_grid=tuple(float(item) for item in grid),
            min_validation_trades=min_validation_trades,
            max_spread=(
                None
                if payload["max_spread"] is None
                else float(payload["max_spread"])
            ),
        )
    except (TypeError, ValueError) as exc:
        raise LivePolicyIntegrityError("edge config is invalid") from exc


def _calibration_fit(value: object) -> CalibrationFit:
    selection = _mapping("calibration selection", value)
    fit_payload = _mapping("calibration fit", selection.get("fit"))
    method = fit_payload.get("method")
    if selection.get("method") != method:
        raise LivePolicyIntegrityError(
            "calibration fit method does not match selected calibration method"
        )
    intercept = fit_payload.get("intercept")
    coefficient = fit_payload.get("coefficient")
    if method == "identity":
        if intercept is not None or coefficient is not None:
            raise LivePolicyIntegrityError(
                "calibration fit identity parameters must be null"
            )
        return CalibrationFit(
            method="identity",
            intercept=None,
            coefficient=None,
        )
    if method != "platt":
        raise LivePolicyIntegrityError("calibration fit method is unsupported")
    if isinstance(intercept, bool) or isinstance(coefficient, bool):
        raise LivePolicyIntegrityError("calibration fit Platt parameters must be finite")
    try:
        numeric_intercept = float(intercept)
        numeric_coefficient = float(coefficient)
    except (TypeError, ValueError) as exc:
        raise LivePolicyIntegrityError(
            "calibration fit Platt parameters must be finite"
        ) from exc
    if not math.isfinite(numeric_intercept) or not math.isfinite(numeric_coefficient):
        raise LivePolicyIntegrityError("calibration fit Platt parameters must be finite")
    if numeric_coefficient <= 0:
        raise LivePolicyIntegrityError(
            "calibration fit Platt coefficient must be positive"
        )
    return CalibrationFit(
        method="platt",
        intercept=numeric_intercept,
        coefficient=numeric_coefficient,
    )


def _edge_policy(
    value: object,
    edge_config: EdgeConfig,
) -> tuple[str, float | None]:
    selection = _mapping("edge policy selection", value)
    policy = selection.get("policy")
    min_edge = selection.get("min_edge")
    if policy == "no_trade":
        if min_edge is not None:
            raise LivePolicyIntegrityError(
                "edge policy no_trade must have min_edge=None"
            )
        return "no_trade", None
    if policy != "trade_threshold":
        raise LivePolicyIntegrityError("edge policy must be trade_threshold or no_trade")
    if isinstance(min_edge, bool):
        raise LivePolicyIntegrityError("edge policy trade_threshold requires min_edge")
    try:
        threshold = float(min_edge)
    except (TypeError, ValueError) as exc:
        raise LivePolicyIntegrityError(
            "edge policy trade_threshold requires min_edge"
        ) from exc
    if not math.isfinite(threshold) or threshold < 0:
        raise LivePolicyIntegrityError(
            "edge policy trade_threshold requires finite non-negative min_edge"
        )
    if threshold not in edge_config.min_edge_grid:
        raise LivePolicyIntegrityError(
            "edge policy min_edge must come from the frozen validation grid"
        )
    return "trade_threshold", threshold


def _training_prior(
    connection: Connection,
    condition_ids: tuple[str, ...],
    *,
    label_version: str,
    horizon_seconds: int,
) -> float:
    rows = connection.execute(
        select(
            market_labels.c.condition_id,
            market_labels.c.official_outcome,
            market_labels.c.horizon_seconds,
        ).where(
            market_labels.c.condition_id.in_(condition_ids),
            market_labels.c.label_version == label_version,
        )
    ).mappings().all()
    by_condition = {str(row["condition_id"]): row for row in rows}
    if set(by_condition) != set(condition_ids):
        raise LivePolicyIntegrityError(
            "training labels must exist for every final training condition"
        )

    targets: list[int] = []
    for condition_id in condition_ids:
        row = by_condition[condition_id]
        if int(row["horizon_seconds"]) != horizon_seconds:
            raise LivePolicyIntegrityError(
                f"training labels horizon mismatch for {condition_id}"
            )
        outcome = row["official_outcome"]
        if outcome == "Up":
            targets.append(1)
        elif outcome == "Down":
            targets.append(0)
        else:
            raise LivePolicyIntegrityError(
                f"training labels contain invalid outcome for {condition_id}"
            )
    if set(targets) != {0, 1}:
        raise LivePolicyIntegrityError(
            "training prior requires both classes in final training labels"
        )
    prior = sum(targets) / len(targets)
    if not math.isfinite(prior) or not 0.0 < prior < 1.0:
        raise LivePolicyIntegrityError("training prior must be finite and within (0, 1)")
    return prior


def _policy_hash_payload(
    *,
    source_calibration_run_id: str,
    source_calibration_semantic_sha256: str,
    source_backtest_run_id: str,
    source_backtest_semantic_sha256: str,
    source_training_run_id: str,
    source_training_semantic_sha256: str,
    calibration_version: str,
    edge_policy_version: str,
    source_feature_version: str,
    label_version: str,
    horizon_seconds: int,
    selected_offset_seconds: int,
    calibration_fit: CalibrationFit,
    edge_config: EdgeConfig,
    edge_policy: str,
    min_edge: float | None,
    training_prior: float,
) -> dict[str, Any]:
    return {
        "source_calibration_run_id": source_calibration_run_id,
        "source_calibration_semantic_sha256": source_calibration_semantic_sha256,
        "source_backtest_run_id": source_backtest_run_id,
        "source_backtest_semantic_sha256": source_backtest_semantic_sha256,
        "source_training_run_id": source_training_run_id,
        "source_training_semantic_sha256": source_training_semantic_sha256,
        "calibration_version": calibration_version,
        "edge_policy_version": edge_policy_version,
        "source_feature_version": source_feature_version,
        "label_version": label_version,
        "horizon_seconds": horizon_seconds,
        "selected_offset_seconds": selected_offset_seconds,
        "calibration_fit": {
            "method": calibration_fit.method,
            "intercept": calibration_fit.intercept,
            "coefficient": calibration_fit.coefficient,
        },
        "edge_config": {
            "fee_rate": edge_config.fee_rate,
            "slippage_buffer": edge_config.slippage_buffer,
            "min_edge_grid": list(edge_config.min_edge_grid),
            "min_validation_trades": edge_config.min_validation_trades,
            "max_spread": edge_config.max_spread,
        },
        "edge_policy": edge_policy,
        "min_edge": min_edge,
        "training_prior": training_prior,
    }


def load_live_policy(connection: Connection, run_id: str) -> LivePolicySpec:
    stored = CalibrationEdgeRunRepository().get(connection, run_id)
    if stored is None:
        raise LivePolicyNotFound(f"live policy source not found: {run_id}")

    report = _validate_stored_report(stored)
    if stored["calibration_version"] != CALIBRATION_VERSION:
        raise LivePolicyIntegrityError(
            f"calibration_version must be {CALIBRATION_VERSION!r}"
        )
    if stored["edge_policy_version"] != EDGE_POLICY_VERSION:
        raise LivePolicyIntegrityError(
            f"edge_policy_version must be {EDGE_POLICY_VERSION!r}"
        )

    horizon_seconds = int(stored["horizon_seconds"])
    if horizon_seconds <= 0:
        raise LivePolicyIntegrityError("horizon_seconds must be positive")
    source_calibration_semantic_sha256 = _sha256(
        "semantic_sha256",
        stored["semantic_sha256"],
    )
    source_backtest_semantic_sha256 = _sha256(
        "source_backtest_semantic_sha256",
        stored["source_backtest_semantic_sha256"],
    )
    source_training_semantic_sha256 = _sha256(
        "source_training_semantic_sha256",
        stored["source_training_semantic_sha256"],
    )

    source_backtest_run_id = str(stored["source_backtest_run_id"])
    try:
        backtest = load_backtest_source_spec(connection, source_backtest_run_id)
    except (BacktestSourceNotFound, BacktestSourceIntegrityError) as exc:
        raise LivePolicyIntegrityError(
            f"source backtest provenance is invalid: {exc}"
        ) from exc
    if backtest.semantic_sha256 != source_backtest_semantic_sha256:
        raise LivePolicyIntegrityError(
            "source backtest semantic does not match Phase 9 provenance"
        )
    if report.get("source_backtest_version") != backtest.backtest_version:
        raise LivePolicyIntegrityError(
            "source backtest version does not match Phase 8 provenance"
        )
    if report.get("source_backtest_config_sha256") != backtest.config_sha256:
        raise LivePolicyIntegrityError(
            "source backtest config does not match Phase 8 provenance"
        )

    source_training_run_id = str(stored["source_training_run_id"])
    try:
        training = load_model_spec(connection, source_training_run_id)
    except (SourceTrainingRunNotFound, ModelSpecIntegrityError) as exc:
        raise LivePolicyIntegrityError(f"training model is invalid: {exc}") from exc

    provenance_checks = (
        (
            "source training semantic",
            training.semantic_sha256,
            source_training_semantic_sha256,
        ),
        (
            "backtest source training run",
            backtest.source_training_run_id,
            source_training_run_id,
        ),
        (
            "backtest source training semantic",
            backtest.source_training_semantic_sha256,
            source_training_semantic_sha256,
        ),
        ("training horizon", training.horizon_seconds, horizon_seconds),
        ("backtest horizon", backtest.horizon_seconds, horizon_seconds),
        ("training feature version", training.feature_version, stored["feature_version"]),
        ("backtest feature version", backtest.feature_version, stored["feature_version"]),
        ("training label version", training.label_version, stored["label_version"]),
        ("backtest label version", backtest.label_version, stored["label_version"]),
        ("training dataset version", training.dataset_version, stored["dataset_version"]),
        ("backtest dataset version", backtest.dataset_version, stored["dataset_version"]),
        ("backtest dataset SHA", backtest.dataset_sha256, stored["dataset_sha256"]),
    )
    for name, actual, expected in provenance_checks:
        if actual != expected:
            raise LivePolicyIntegrityError(
                f"{name} does not match frozen Phase 9 provenance"
            )

    report_membership = tuple(
        _sha256("source_fold_membership_sha256 entry", item)
        for item in _sequence(
            "source_fold_membership_sha256",
            report["source_fold_membership_sha256"],
        )
    )
    if report_membership != backtest.fold_membership_sha256:
        raise LivePolicyIntegrityError(
            "source fold membership does not match Phase 8 provenance"
        )

    final = _mapping("final_holdout", report.get("final_holdout"))
    markers = (
        ("calibration fit", "calibration_selection_fit_partition", "train"),
        ("calibration selection", "calibration_selection_partition", "validation"),
        ("edge selection", "edge_selection_partition", "validation"),
        ("evaluation", "evaluation_partition", "holdout"),
    )
    for label, field, expected in markers:
        if final.get(field) != expected:
            raise LivePolicyIntegrityError(
                f"{label} partition must be {expected!r}"
            )

    selected_offset = final.get("selected_offset_seconds")
    if isinstance(selected_offset, bool) or not isinstance(selected_offset, int):
        raise LivePolicyIntegrityError("selected_offset_seconds must be an integer")
    if selected_offset <= 0 or selected_offset >= horizon_seconds:
        raise LivePolicyIntegrityError(
            "selected_offset_seconds must be within the market horizon"
        )

    train_ids = _ids(
        "final training condition ids",
        final.get("train_condition_ids"),
    )
    validation_ids = _ids(
        "final validation condition ids",
        final.get("validation_condition_ids"),
    )
    holdout_ids = _ids(
        "final holdout condition ids",
        final.get("holdout_condition_ids"),
    )
    phase9_final_context = (
        _sha256(
            "final membership_sha256",
            final.get("membership_sha256"),
        ),
        train_ids,
        validation_ids,
        holdout_ids,
        selected_offset,
    )
    phase8_final_context = (
        backtest.final.membership_sha256,
        backtest.final.train_condition_ids,
        backtest.final.validation_condition_ids,
        backtest.final.holdout_condition_ids,
        backtest.final.selected_offset_seconds,
    )
    if phase9_final_context != phase8_final_context:
        raise LivePolicyIntegrityError(
            "backtest final policy context does not match Phase 9 selection"
        )

    edge_config = _edge_config(stored["config"])
    calibration_fit = _calibration_fit(final.get("calibration_selection"))
    edge_policy, min_edge = _edge_policy(
        final.get("edge_policy_selection"),
        edge_config,
    )
    training_prior = _training_prior(
        connection,
        train_ids,
        label_version=str(stored["label_version"]),
        horizon_seconds=horizon_seconds,
    )

    hash_payload = _policy_hash_payload(
        source_calibration_run_id=str(stored["run_id"]),
        source_calibration_semantic_sha256=source_calibration_semantic_sha256,
        source_backtest_run_id=source_backtest_run_id,
        source_backtest_semantic_sha256=source_backtest_semantic_sha256,
        source_training_run_id=source_training_run_id,
        source_training_semantic_sha256=source_training_semantic_sha256,
        calibration_version=str(stored["calibration_version"]),
        edge_policy_version=str(stored["edge_policy_version"]),
        source_feature_version=str(stored["feature_version"]),
        label_version=str(stored["label_version"]),
        horizon_seconds=horizon_seconds,
        selected_offset_seconds=selected_offset,
        calibration_fit=calibration_fit,
        edge_config=edge_config,
        edge_policy=edge_policy,
        min_edge=min_edge,
        training_prior=training_prior,
    )
    return LivePolicySpec(
        source_calibration_run_id=str(stored["run_id"]),
        source_calibration_semantic_sha256=source_calibration_semantic_sha256,
        source_backtest_run_id=source_backtest_run_id,
        source_backtest_semantic_sha256=source_backtest_semantic_sha256,
        source_training_run_id=source_training_run_id,
        source_training_semantic_sha256=source_training_semantic_sha256,
        calibration_version=str(stored["calibration_version"]),
        edge_policy_version=str(stored["edge_policy_version"]),
        source_feature_version=str(stored["feature_version"]),
        label_version=str(stored["label_version"]),
        horizon_seconds=horizon_seconds,
        selected_offset_seconds=selected_offset,
        calibration_fit=calibration_fit,
        edge_config=edge_config,
        edge_policy=edge_policy,
        min_edge=min_edge,
        training_prior=training_prior,
        policy_sha256=canonical_hash(hash_payload),
    )
