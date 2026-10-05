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
from bp_engine.v3_research.policy import edge_band_report_v3
from bp_engine.v4_research.config import FROZEN_V4_GATE_B_CONFIG, V4GateBConfig
from bp_engine.v4_research.policy import evaluate_economics_v4
from bp_engine.v4_research.service import (
    _economics_slices,
    _execution_books,
    _final_ids,
    _json_safe_config,
    _rows_at_offset,
    probability_slices,
    verify_v4_prepare_plan,
)

AUTHORIZED_V4_GATE_B_V2_SELECTION_SHA256 = (
    "895cb70ae0cdbc22f4e3585c77db3ad20f8186d1ee1992a58025d89bb1e2bb1a"
)
AUTHORIZED_V4_GATE_B_V2_MODEL_SHA256 = (
    "6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf"
)
AUTHORIZED_V4_GATE_B_V2_MODEL_SIZE_BYTES = 230132


class V4HoldoutIntegrityError(RuntimeError):
    """Raised when the one-shot V4 final holdout boundary is violated."""


def _verify_hash(payload: Mapping[str, Any], field: str) -> None:
    expected = payload.get(field)
    if not isinstance(expected, str) or len(expected) != 64:
        raise V4HoldoutIntegrityError(f"{field} must be SHA-256")
    body = dict(payload)
    body.pop(field, None)
    if canonical_hash(body) != expected:
        raise V4HoldoutIntegrityError(f"{field} mismatch")


def verify_v4_holdout_inputs(
    *,
    plan: Mapping[str, Any],
    selection: Mapping[str, Any],
    model_bundle: Mapping[str, Any],
    model_sha256: str,
    model_file_name: str,
    model_size_bytes: int,
    config: V4GateBConfig = FROZEN_V4_GATE_B_CONFIG,
) -> None:
    verify_v4_prepare_plan(plan, config)
    _verify_hash(selection, "selection_sha256")

    if selection.get("selection_sha256") != AUTHORIZED_V4_GATE_B_V2_SELECTION_SHA256:
        raise V4HoldoutIntegrityError(
            "selection is not the explicitly frozen V4 ordinary selection"
        )
    if model_sha256 != AUTHORIZED_V4_GATE_B_V2_MODEL_SHA256:
        raise V4HoldoutIntegrityError(
            "model is not the explicitly frozen V4 model artifact"
        )
    if model_size_bytes != AUTHORIZED_V4_GATE_B_V2_MODEL_SIZE_BYTES:
        raise V4HoldoutIntegrityError("frozen V4 model size mismatch")

    if selection.get("stage") != "ordinary_selection_frozen":
        raise V4HoldoutIntegrityError("selection is not ordinary-selection frozen")
    if selection.get("plan_sha256") != plan.get("plan_sha256"):
        raise V4HoldoutIntegrityError("selection plan hash mismatch")
    if selection.get("config") != _json_safe_config(config):
        raise V4HoldoutIntegrityError("selection frozen V4 config changed")
    if selection.get("labels_read_non_holdout") is not True:
        raise V4HoldoutIntegrityError("ordinary non-holdout labels were not frozen")
    if selection.get("holdout_labels_read") is not False:
        raise V4HoldoutIntegrityError(
            "selection already records final-holdout label access"
        )
    if selection.get("holdout_evaluated") is not False:
        raise V4HoldoutIntegrityError(
            "selection already records final-holdout evaluation"
        )
    if selection.get("training_performed") is not True:
        raise V4HoldoutIntegrityError("selection does not record completed training")
    if selection.get("policy_selected") is not True:
        raise V4HoldoutIntegrityError("selection does not record a frozen policy")
    if selection.get("automatic_promotion") is not False:
        raise V4HoldoutIntegrityError("selection automatic-promotion flag changed")
    if selection.get("activation_performed") is not False:
        raise V4HoldoutIntegrityError("selection activation flag changed")

    holdout_ids = _final_ids(plan, "holdout_condition_ids")
    final = selection.get("final")
    if not isinstance(final, Mapping):
        raise V4HoldoutIntegrityError("selection final block is missing")
    if final.get("holdout_condition_ids") != list(holdout_ids):
        raise V4HoldoutIntegrityError("selection holdout membership changed")
    if final.get("membership_sha256") != plan["final"].get("membership_sha256"):
        raise V4HoldoutIntegrityError("selection final membership hash changed")

    selected_forecast = final.get("selected_forecast")
    edge_selection = final.get("frozen_edge_policy")
    artifact = selection.get("model_artifact")
    if not isinstance(selected_forecast, Mapping):
        raise V4HoldoutIntegrityError("selection forecast is missing")
    if not isinstance(edge_selection, Mapping):
        raise V4HoldoutIntegrityError("selection edge policy is missing")
    if not isinstance(artifact, Mapping):
        raise V4HoldoutIntegrityError("selection model artifact is missing")

    if artifact.get("sha256") != model_sha256:
        raise V4HoldoutIntegrityError("model artifact SHA-256 mismatch")
    if artifact.get("file_name") != model_file_name:
        raise V4HoldoutIntegrityError("model artifact filename mismatch")
    if int(artifact.get("size_bytes", -1)) != model_size_bytes:
        raise V4HoldoutIntegrityError("model artifact size mismatch")

    expected_candidate = str(selected_forecast.get("candidate"))
    expected_offset = int(selected_forecast.get("offset_seconds"))
    expected_calibration = str(selected_forecast.get("calibration_method"))
    expected_family = str(artifact.get("family"))
    expected_min_edge = edge_selection.get("min_edge")

    if expected_candidate != "full_v4_xgboost":
        raise V4HoldoutIntegrityError("frozen V4 candidate changed")
    if expected_offset != 240:
        raise V4HoldoutIntegrityError("frozen V4 offset changed")
    if expected_calibration != "identity":
        raise V4HoldoutIntegrityError("frozen V4 calibration changed")
    if edge_selection.get("policy") != "trade_threshold":
        raise V4HoldoutIntegrityError("frozen V4 edge policy changed")
    if float(expected_min_edge) != 0.05:
        raise V4HoldoutIntegrityError("frozen V4 min-edge changed")

    if model_bundle.get("research_plan_version") != config.research_plan_version:
        raise V4HoldoutIntegrityError("model research-plan identity changed")
    if model_bundle.get("plan_sha256") != plan.get("plan_sha256"):
        raise V4HoldoutIntegrityError("model plan hash mismatch")
    if model_bundle.get("dataset_sha256_non_holdout") != selection.get(
        "dataset_sha256_non_holdout"
    ):
        raise V4HoldoutIntegrityError("model non-holdout dataset hash mismatch")
    if model_bundle.get("candidate") != expected_candidate:
        raise V4HoldoutIntegrityError("model candidate changed")
    if model_bundle.get("family") != expected_family:
        raise V4HoldoutIntegrityError("model family changed")
    if int(model_bundle.get("offset_seconds", -1)) != expected_offset:
        raise V4HoldoutIntegrityError("model offset changed")
    if model_bundle.get("calibration_method") != expected_calibration:
        raise V4HoldoutIntegrityError("model calibration method changed")
    if model_bundle.get("calibration_fit") != selected_forecast.get(
        "calibration_fit"
    ):
        raise V4HoldoutIntegrityError("model calibration fit changed")
    if model_bundle.get("edge_policy") != edge_selection.get("policy"):
        raise V4HoldoutIntegrityError("model edge policy changed")
    if model_bundle.get("selected_min_edge") != expected_min_edge:
        raise V4HoldoutIntegrityError("model min-edge changed")
    if float(model_bundle.get("fee_rate", -1.0)) != config.fee_rate:
        raise V4HoldoutIntegrityError("model fee rate changed")
    if float(model_bundle.get("slippage_buffer", -1.0)) != config.slippage_buffer:
        raise V4HoldoutIntegrityError("model slippage buffer changed")
    if (
        int(model_bundle.get("max_selected_book_age_seconds", -1))
        != config.max_selected_book_age_seconds
    ):
        raise V4HoldoutIntegrityError("model book-age limit changed")


def _load_holdout_dataset(
    connection: Connection,
    *,
    plan: Mapping[str, Any],
    config: V4GateBConfig,
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
        raise V4HoldoutIntegrityError(
            f"missing canonical V4 holdout input for {sorted(missing)[0]}"
        )
    unexpected = observed.difference(expected)
    if unexpected:
        raise V4HoldoutIntegrityError(
            f"V4 holdout dataset contains unrequested market {sorted(unexpected)[0]}"
        )

    offsets: dict[str, set[int]] = {condition_id: set() for condition_id in holdout_ids}
    for row in dataset.rows:
        offsets[row.condition_id].add(row.feature_offset_seconds)
        for name in row.predictors:
            if name.startswith("pm_") or "polymarket" in name.lower():
                raise V4HoldoutIntegrityError(
                    f"Polymarket predictor entered V4 holdout dataset: {name}"
                )
    for condition_id, actual in offsets.items():
        if actual != set(config.feature_offsets_seconds):
            raise V4HoldoutIntegrityError(
                f"{condition_id} does not have exact frozen V4 offsets"
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
            raise V4HoldoutIntegrityError("training prior probability is invalid")
        return tuple(probability for _ in rows)

    names = tuple(str(name) for name in model_bundle["predictor_names"])
    if not names:
        raise V4HoldoutIntegrityError("fitted V4 model predictor names are empty")
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
        raise V4HoldoutIntegrityError(
            f"unsupported frozen V4 model family: {family}"
        )

    values = estimator.predict_proba(transformed)[:, 1]
    probabilities = tuple(float(value) for value in values)
    if any(
        not math.isfinite(value) or not 0.0 <= value <= 1.0
        for value in probabilities
    ):
        raise V4HoldoutIntegrityError(
            "frozen V4 model produced invalid probabilities"
        )
    return probabilities


def _calibrated_probabilities(
    raw: tuple[float, ...],
    model_bundle: Mapping[str, Any],
) -> tuple[float, ...]:
    payload = model_bundle.get("calibration_fit")
    if not isinstance(payload, Mapping):
        raise V4HoldoutIntegrityError("frozen V4 calibration fit is missing")
    fit = CalibrationFit(
        method=str(payload["method"]),
        intercept=payload.get("intercept"),
        coefficient=payload.get("coefficient"),
    )
    return apply_calibration(fit, raw)


def evaluate_v4_gate_b_holdout(
    connection: Connection,
    *,
    plan: dict[str, Any],
    selection: dict[str, Any],
    model_bundle: dict[str, Any],
    model_sha256: str,
    model_file_name: str,
    model_size_bytes: int,
    config: V4GateBConfig = FROZEN_V4_GATE_B_CONFIG,
) -> dict[str, Any]:
    verify_v4_holdout_inputs(
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
    edge_selection = selection["final"]["frozen_edge_policy"]
    offset = int(selected["offset_seconds"])
    rows = _rows_at_offset(dataset.rows, offset)
    if len(rows) != len(holdout_ids):
        raise V4HoldoutIntegrityError(
            "V4 holdout selected-offset market count changed"
        )

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
    probability_map = {
        row.condition_id: probability
        for row, probability in zip(rows, calibrated, strict=True)
    }
    economics = evaluate_economics_v4(
        rows,
        probability_map,
        books,
        fee_rate=config.fee_rate,
        slippage_buffer=config.slippage_buffer,
        min_edge=min_edge,
    )

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
            "calibration_method": selected["calibration_method"],
            "edge_policy": edge_selection["policy"],
            "min_edge": min_edge,
            "fee_rate": config.fee_rate,
            "slippage_buffer": config.slippage_buffer,
            "max_selected_book_age_seconds": config.max_selected_book_age_seconds,
        },
        "holdout_evaluation": {
            "forecast": {
                "metrics": {
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
                "slices": probability_slices(
                    rows,
                    calibrated,
                    config=config,
                ),
            },
            "economics": {
                "overall": economics,
                "slices": _economics_slices(
                    rows,
                    calibrated,
                    books,
                    min_edge=min_edge,
                    config=config,
                ),
                "edge_bands": edge_band_report_v3(
                    rows,
                    probability_map,
                    books,
                    fee_rate=config.fee_rate,
                    slippage_buffer=config.slippage_buffer,
                    boundaries=config.min_edge_grid,
                ),
            },
        },
        "labels_read_non_holdout": True,
        "holdout_labels_read": True,
        "holdout_evaluated_once": True,
        "model_refit_performed": False,
        "threshold_tuning_performed": False,
        "policy_reselection_performed": False,
        "automatic_promotion": False,
        "paper_activation_authorized": False,
        "paper_activation_performed": False,
        "live_trading_enabled": False,
        "real_money_usd": 0,
    }
    payload["holdout_evidence_sha256"] = canonical_hash(payload)
    return payload
