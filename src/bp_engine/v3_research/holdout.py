from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np
from sqlalchemy import Connection

from bp_engine.calibration.calibrators import apply_calibration
from bp_engine.calibration.models import CalibrationFit
from bp_engine.features.hashing import canonical_hash
from bp_engine.modeling.dataset import load_dataset
from bp_engine.modeling.metrics import evaluate_probabilities
from bp_engine.modeling.models import DatasetSnapshot, SupervisedRow
from bp_engine.modeling.split import equal_market_weights
from bp_engine.v3_research.config import FROZEN_V3_GATE_B_CONFIG, V3GateBConfig
from bp_engine.v3_research.policy import edge_band_report_v3
from bp_engine.v3_research.service import (
    _evaluate_edge,
    _execution_books,
    _final_ids,
    _json_safe_config,
    _rows_at_offset,
    verify_v3_prepare_plan,
)


class V3HoldoutIntegrityError(RuntimeError):
    """Raised when the one-shot V3 final holdout boundary is violated."""


def _verify_hash(payload: Mapping[str, Any], field: str) -> None:
    expected = payload.get(field)
    if not isinstance(expected, str) or len(expected) != 64:
        raise V3HoldoutIntegrityError(f"{field} must be SHA-256")
    body = dict(payload)
    body.pop(field, None)
    if canonical_hash(body) != expected:
        raise V3HoldoutIntegrityError(f"{field} mismatch")


def verify_v3_holdout_inputs(
    *,
    plan: Mapping[str, Any],
    selection: Mapping[str, Any],
    model_bundle: Mapping[str, Any],
    model_sha256: str,
    model_file_name: str,
    model_size_bytes: int,
    config: V3GateBConfig = FROZEN_V3_GATE_B_CONFIG,
) -> None:
    verify_v3_prepare_plan(plan, config)
    _verify_hash(selection, "selection_sha256")

    if selection.get("stage") != "ordinary_selection_frozen":
        raise V3HoldoutIntegrityError("selection is not ordinary-selection frozen")
    if selection.get("plan_sha256") != plan.get("plan_sha256"):
        raise V3HoldoutIntegrityError("selection plan hash mismatch")
    if selection.get("config") != _json_safe_config(config):
        raise V3HoldoutIntegrityError("selection frozen V3 config changed")
    if selection.get("labels_read_non_holdout") is not True:
        raise V3HoldoutIntegrityError("ordinary non-holdout labels were not frozen")
    if selection.get("holdout_labels_read") is not False:
        raise V3HoldoutIntegrityError("selection already records holdout label access")
    if selection.get("holdout_evaluated") is not False:
        raise V3HoldoutIntegrityError("selection already records holdout evaluation")
    if selection.get("training_performed") is not True:
        raise V3HoldoutIntegrityError("selection does not record completed training")
    if selection.get("automatic_promotion") is not False:
        raise V3HoldoutIntegrityError("selection automatic-promotion flag changed")

    holdout_ids = _final_ids(plan, "holdout_condition_ids")
    final = selection.get("final")
    if not isinstance(final, Mapping):
        raise V3HoldoutIntegrityError("selection final block is missing")
    if final.get("holdout_condition_ids") != list(holdout_ids):
        raise V3HoldoutIntegrityError("selection holdout membership changed")

    selected_forecast = final.get("selected_forecast")
    edge_selection = final.get("edge_selection")
    artifact = selection.get("model_artifact")
    if not isinstance(selected_forecast, Mapping):
        raise V3HoldoutIntegrityError("selection forecast is missing")
    if not isinstance(edge_selection, Mapping):
        raise V3HoldoutIntegrityError("selection edge policy is missing")
    if not isinstance(artifact, Mapping):
        raise V3HoldoutIntegrityError("selection model artifact is missing")

    if artifact.get("sha256") != model_sha256:
        raise V3HoldoutIntegrityError("model artifact SHA-256 mismatch")
    if artifact.get("file_name") != model_file_name:
        raise V3HoldoutIntegrityError("model artifact filename mismatch")
    if int(artifact.get("size_bytes", -1)) != model_size_bytes:
        raise V3HoldoutIntegrityError("model artifact size mismatch")

    expected_candidate = str(selected_forecast.get("candidate"))
    expected_offset = int(selected_forecast.get("offset_seconds"))
    expected_family = str(artifact.get("family"))
    if model_bundle.get("research_plan_version") != config.research_plan_version:
        raise V3HoldoutIntegrityError("model research-plan identity changed")
    if model_bundle.get("plan_sha256") != plan.get("plan_sha256"):
        raise V3HoldoutIntegrityError("model plan hash mismatch")
    if model_bundle.get("dataset_sha256_non_holdout") != selection.get(
        "dataset_sha256_non_holdout"
    ):
        raise V3HoldoutIntegrityError("model non-holdout dataset hash mismatch")
    if model_bundle.get("candidate") != expected_candidate:
        raise V3HoldoutIntegrityError("model candidate changed")
    if model_bundle.get("family") != expected_family:
        raise V3HoldoutIntegrityError("model family changed")
    if int(model_bundle.get("offset_seconds", -1)) != expected_offset:
        raise V3HoldoutIntegrityError("model offset changed")
    if model_bundle.get("edge_policy") != edge_selection.get("policy"):
        raise V3HoldoutIntegrityError("model edge policy changed")
    if model_bundle.get("selected_min_edge") != edge_selection.get("min_edge"):
        raise V3HoldoutIntegrityError("model min-edge changed")
    if float(model_bundle.get("fee_rate", -1.0)) != config.fee_rate:
        raise V3HoldoutIntegrityError("model fee rate changed")
    if float(model_bundle.get("slippage_buffer", -1.0)) != config.slippage_buffer:
        raise V3HoldoutIntegrityError("model slippage buffer changed")
    if (
        int(model_bundle.get("max_selected_book_age_seconds", -1))
        != config.max_selected_book_age_seconds
    ):
        raise V3HoldoutIntegrityError("model book-age limit changed")


def _load_holdout_dataset(
    connection: Connection,
    *,
    plan: Mapping[str, Any],
    config: V3GateBConfig,
) -> DatasetSnapshot:
    holdout_ids = _final_ids(plan, "holdout_condition_ids")
    dataset = load_dataset(
        connection,
        start=config.epoch_start,
        end=config.epoch_end,
        horizon_seconds=config.horizon_seconds,
        feature_version=config.feature_version,
        label_version=config.label_version,
        condition_ids=holdout_ids,
        dataset_version=config.dataset_version,
    )
    observed = {row.condition_id for row in dataset.rows}
    expected = set(holdout_ids)
    missing = expected.difference(observed)
    if missing:
        raise V3HoldoutIntegrityError(
            f"missing canonical holdout input for {sorted(missing)[0]}"
        )
    unexpected = observed.difference(expected)
    if unexpected:
        raise V3HoldoutIntegrityError(
            f"holdout dataset contains unrequested market {sorted(unexpected)[0]}"
        )
    offsets: dict[str, set[int]] = {condition_id: set() for condition_id in holdout_ids}
    for row in dataset.rows:
        offsets[row.condition_id].add(row.feature_offset_seconds)
        for name in row.predictors:
            if name.startswith("pm_") or "polymarket" in name.lower():
                raise V3HoldoutIntegrityError(
                    f"Polymarket predictor entered holdout dataset: {name}"
                )
    for condition_id, actual in offsets.items():
        if actual != set(config.feature_offsets_seconds):
            raise V3HoldoutIntegrityError(
                f"{condition_id} does not have exact frozen V3 offsets"
            )
    return dataset


def _raw_model_probabilities(
    rows: tuple[SupervisedRow, ...],
    model_bundle: Mapping[str, Any],
) -> tuple[float, ...]:
    candidate = str(model_bundle["candidate"])
    if candidate == "training_prior":
        probability = float(model_bundle["probability"])
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise V3HoldoutIntegrityError("training prior probability is invalid")
        return tuple(probability for _ in rows)

    names = tuple(str(name) for name in model_bundle["predictor_names"])
    if not names:
        raise V3HoldoutIntegrityError("fitted model predictor names are empty")
    matrix = np.asarray(
        [[row.predictors.get(name) for name in names] for row in rows],
        dtype=float,
    )
    imputer = model_bundle["imputer"]
    estimator = model_bundle["estimator"]
    transformed = imputer.transform(matrix)

    family = str(model_bundle["family"])
    if family == "logistic":
        scaler = model_bundle["scaler"]
        transformed = scaler.transform(transformed)
    elif family != "xgboost":
        raise V3HoldoutIntegrityError(f"unsupported frozen model family: {family}")

    values = estimator.predict_proba(transformed)[:, 1]
    probabilities = tuple(float(value) for value in values)
    if any(
        not math.isfinite(value) or not 0.0 <= value <= 1.0
        for value in probabilities
    ):
        raise V3HoldoutIntegrityError("frozen model produced invalid probabilities")
    return probabilities


def _calibrated_probabilities(
    raw: tuple[float, ...],
    model_bundle: Mapping[str, Any],
) -> tuple[float, ...]:
    payload = model_bundle.get("calibration_fit")
    if not isinstance(payload, Mapping):
        raise V3HoldoutIntegrityError("frozen calibration fit is missing")
    fit = CalibrationFit(
        method=str(payload["method"]),
        intercept=payload.get("intercept"),
        coefficient=payload.get("coefficient"),
    )
    return apply_calibration(fit, raw)


def evaluate_v3_gate_b_holdout(
    connection: Connection,
    *,
    plan: dict[str, Any],
    selection: dict[str, Any],
    model_bundle: dict[str, Any],
    model_sha256: str,
    model_file_name: str,
    model_size_bytes: int,
    config: V3GateBConfig = FROZEN_V3_GATE_B_CONFIG,
) -> dict[str, Any]:
    verify_v3_holdout_inputs(
        plan=plan,
        selection=selection,
        model_bundle=model_bundle,
        model_sha256=model_sha256,
        model_file_name=model_file_name,
        model_size_bytes=model_size_bytes,
        config=config,
    )

    holdout_ids = _final_ids(plan, "holdout_condition_ids")
    dataset = _load_holdout_dataset(connection, plan=plan, config=config)
    selected = selection["final"]["selected_forecast"]
    edge_selection = selection["final"]["edge_selection"]
    offset = int(selected["offset_seconds"])
    rows = _rows_at_offset(dataset.rows, offset)
    if len(rows) != len(holdout_ids):
        raise V3HoldoutIntegrityError("holdout selected-offset market count changed")

    raw = _raw_model_probabilities(rows, model_bundle)
    calibrated = _calibrated_probabilities(raw, model_bundle)
    forecast_metrics = evaluate_probabilities(
        rows,
        calibrated,
        equal_market_weights(rows),
    )
    books = _execution_books(
        connection,
        rows,
        max_age_seconds=config.max_selected_book_age_seconds,
    )
    min_edge = (
        edge_selection["min_edge"]
        if edge_selection["policy"] == "trade_threshold"
        else None
    )
    edge_metrics = _evaluate_edge(
        rows,
        calibrated,
        books,
        min_edge=min_edge,
        config=config,
    )
    probability_map = {
        row.condition_id: probability
        for row, probability in zip(rows, calibrated, strict=True)
    }

    payload: dict[str, Any] = {
        "research_plan_version": config.research_plan_version,
        "stage": "final_holdout_evaluated",
        "dataset_version": config.dataset_version,
        "feature_version": config.feature_version,
        "label_version": config.label_version,
        "plan_sha256": plan["plan_sha256"],
        "selection_sha256": selection["selection_sha256"],
        "model_artifact_sha256": model_sha256,
        "holdout_condition_ids": list(holdout_ids),
        "holdout_dataset_sha256": dataset.dataset_sha256,
        "frozen_selection": {
            "candidate": selected["candidate"],
            "offset_seconds": offset,
            "calibration": selected["calibration"],
            "edge_policy": edge_selection["policy"],
            "min_edge": min_edge,
            "fee_rate": config.fee_rate,
            "slippage_buffer": config.slippage_buffer,
            "max_selected_book_age_seconds": config.max_selected_book_age_seconds,
        },
        "holdout_evaluation": {
            "forecast_metrics": {
                "row_count": forecast_metrics.row_count,
                "market_count": forecast_metrics.market_count,
                "accuracy": forecast_metrics.accuracy,
                "balanced_accuracy": forecast_metrics.balanced_accuracy,
                "log_loss": forecast_metrics.log_loss,
                "brier_score": forecast_metrics.brier_score,
                "ece": forecast_metrics.ece,
                "calibration": list(forecast_metrics.calibration),
                "confidence_coverage": forecast_metrics.confidence_coverage,
            },
            "edge_metrics": edge_metrics,
            "edge_bands": edge_band_report_v3(
                rows,
                probability_map,
                books,
                fee_rate=config.fee_rate,
                slippage_buffer=config.slippage_buffer,
                boundaries=config.min_edge_grid,
            ),
        },
        "labels_read_non_holdout": True,
        "holdout_labels_read": True,
        "holdout_evaluated_once": True,
        "model_refit_performed": False,
        "automatic_promotion": False,
        "activation_performed": False,
    }
    payload["holdout_evidence_sha256"] = canonical_hash(payload)
    return payload
