from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Connection

from bp_engine.features.hashing import canonical_hash
from bp_engine.modeling.artifacts import ModelArtifact, write_model_artifact
from bp_engine.modeling.baselines import MarketPriceBaseline, PriorBaseline
from bp_engine.modeling.dataset import load_dataset
from bp_engine.modeling.metrics import evaluate_probabilities
from bp_engine.modeling.models import (
    DATASET_VERSION,
    SPLIT_VERSION,
    ModelEvaluation,
    SupervisedRow,
    TrainingRunReport,
)
from bp_engine.modeling.repository import ModelTrainingRunRepository
from bp_engine.modeling.split import chronological_market_split, equal_market_weights
from bp_engine.modeling.trainers import prepare_matrices, train_logistic, train_xgboost


def select_validation_champion(evaluations: dict[str, ModelEvaluation]) -> str:
    if not evaluations:
        raise ValueError("evaluations must not be empty")
    return min(
        evaluations,
        key=lambda name: (
            evaluations[name].validation.log_loss,
            evaluations[name].validation.brier_score,
            name,
        ),
    )


def xgboost_promotion_eligible(evaluations: dict[str, ModelEvaluation]) -> bool:
    required = {"prior", "market_price", "logistic", "xgboost"}
    if not required <= set(evaluations):
        return False
    xgb = evaluations["xgboost"]
    simple_names = ("prior", "market_price", "logistic")
    if not all(
        xgb.validation.log_loss < evaluations[name].validation.log_loss
        and xgb.validation.brier_score < evaluations[name].validation.brier_score
        for name in simple_names
    ):
        return False
    simple_champion = min(
        simple_names,
        key=lambda name: (
            evaluations[name].validation.log_loss,
            evaluations[name].validation.brier_score,
            name,
        ),
    )
    simple_test = evaluations[simple_champion].test
    return (
        xgb.test.log_loss < simple_test.log_loss
        and xgb.test.brier_score <= simple_test.brier_score
    )


def gross_execution_diagnostic(
    rows: tuple[SupervisedRow, ...],
    probabilities: tuple[float, ...],
) -> dict[str, float | int]:
    if len(rows) != len(probabilities):
        raise ValueError("rows and probabilities must have equal length")
    gross_pnl = 0.0
    eligible = 0
    for row, probability in zip(rows, probabilities, strict=True):
        predicted = 1 if probability >= 0.5 else 0
        ask_key = "pm_up_best_ask" if predicted == 1 else "pm_down_best_ask"
        ask = row.predictors.get(ask_key)
        if ask is None:
            continue
        ask_value = float(ask)
        if not 0 <= ask_value <= 1:
            raise ValueError(f"{ask_key} must be within [0, 1]")
        payout = 1.0 if row.target == predicted else 0.0
        gross_pnl += payout - ask_value
        eligible += 1
    total = len(rows)
    return {
        "eligible_rows": eligible,
        "total_rows": total,
        "coverage": eligible / total if total else 0.0,
        "gross_execution_pnl_before_costs": gross_pnl,
        "mean_gross_pnl_per_executed_share": gross_pnl / eligible if eligible else 0.0,
    }


def _evaluate(
    family: str,
    config: dict[str, Any],
    *,
    validation_rows: tuple[SupervisedRow, ...],
    test_rows: tuple[SupervisedRow, ...],
    validation_probabilities: tuple[float, ...],
    test_probabilities: tuple[float, ...],
) -> ModelEvaluation:
    return ModelEvaluation(
        family=family,
        config=config,
        validation=evaluate_probabilities(
            validation_rows,
            validation_probabilities,
            equal_market_weights(validation_rows),
        ),
        test=evaluate_probabilities(
            test_rows,
            test_probabilities,
            equal_market_weights(test_rows),
        ),
    )


def _artifact_manifest(artifact: ModelArtifact) -> dict[str, Any]:
    return {
        "family": artifact.family,
        "file_name": artifact.file_name,
        "size_bytes": artifact.size_bytes,
        "sha256": artifact.sha256,
        "library_version": artifact.library_version,
    }


def _offset_metrics(
    rows: tuple[SupervisedRow, ...],
    probabilities: tuple[float, ...],
) -> dict[str, Any]:
    grouped: dict[int, list[tuple[SupervisedRow, float]]] = defaultdict(list)
    for row, probability in zip(rows, probabilities, strict=True):
        grouped[row.feature_offset_seconds].append((row, probability))
    result: dict[str, Any] = {}
    for offset in sorted(grouped):
        entries = grouped[offset]
        offset_rows = tuple(row for row, _ in entries)
        offset_probabilities = tuple(probability for _, probability in entries)
        result[str(offset)] = asdict(
            evaluate_probabilities(
                offset_rows,
                offset_probabilities,
                equal_market_weights(offset_rows),
            )
        )
    return result


def train_horizon(
    connection: Connection,
    *,
    start: datetime,
    end: datetime,
    horizon_seconds: int,
    feature_version: str,
    label_version: str,
    output_dir: Path,
    min_markets: int,
) -> TrainingRunReport:
    dataset = load_dataset(
        connection,
        start=start,
        end=end,
        horizon_seconds=horizon_seconds,
        feature_version=feature_version,
        label_version=label_version,
    )
    split = chronological_market_split(dataset, min_markets=min_markets)
    prepared = prepare_matrices(split)
    prior = PriorBaseline()
    prior.fit(split.train.rows, equal_market_weights(split.train.rows))
    assert prior.probability is not None
    market = MarketPriceBaseline(prior.probability)
    logistic = train_logistic(split, prepared)
    xgboost = train_xgboost(split, prepared)
    validation_probabilities = {
        "prior": prior.predict_proba(split.validation.rows),
        "market_price": market.predict_proba(split.validation.rows),
        "logistic": logistic.validation_probabilities,
        "xgboost": xgboost.validation_probabilities,
    }
    test_probabilities = {
        "prior": prior.predict_proba(split.test.rows),
        "market_price": market.predict_proba(split.test.rows),
        "logistic": logistic.test_probabilities,
        "xgboost": xgboost.test_probabilities,
    }
    model_configs: dict[str, dict[str, Any]] = {
        "prior": {"weighted_market_prior": True},
        "market_price": {
            "predictor": "pm_up_price",
            "missing_fallback": "training_prior",
            "clip_epsilon": 1e-6,
        },
        "logistic": logistic.config,
        "xgboost": xgboost.config,
    }
    evaluations = {
        family: _evaluate(
            family,
            model_configs[family],
            validation_rows=split.validation.rows,
            test_rows=split.test.rows,
            validation_probabilities=validation_probabilities[family],
            test_probabilities=test_probabilities[family],
        )
        for family in ("prior", "market_price", "logistic", "xgboost")
    }
    validation_champion = select_validation_champion(evaluations)
    best_test_result = min(
        evaluations,
        key=lambda name: (
            evaluations[name].test.log_loss,
            evaluations[name].test.brier_score,
            name,
        ),
    )
    boosted_promotion_eligible = xgboost_promotion_eligible(evaluations)
    artifact_identity = canonical_hash(
        {
            "dataset_sha256": dataset.dataset_sha256,
            "split_sha256": split.split_sha256,
            "horizon_seconds": horizon_seconds,
            "prepared_predictors": prepared.predictor_names,
        }
    )[:16]
    artifact_dir = output_dir / f"h{horizon_seconds}-{artifact_identity}"
    logistic_artifact = write_model_artifact(
        {
            "family": "logistic",
            "predictor_names": prepared.predictor_names,
            "imputer": prepared.imputer,
            "scaler": prepared.scaler,
            "estimator": logistic.estimator,
        },
        output_dir=artifact_dir,
        name="logistic",
        family="logistic",
    )
    xgboost_artifact = write_model_artifact(
        {
            "family": "xgboost",
            "predictor_names": prepared.predictor_names,
            "imputer": prepared.imputer,
            "estimator": xgboost.estimator,
        },
        output_dir=artifact_dir,
        name="xgboost",
        family="xgboost",
    )
    artifacts = (
        _artifact_manifest(logistic_artifact),
        _artifact_manifest(xgboost_artifact),
    )
    champion_probabilities = test_probabilities[validation_champion]
    offset_metrics = _offset_metrics(split.test.rows, champion_probabilities)
    gross_diagnostic = gross_execution_diagnostic(split.test.rows, champion_probabilities)
    semantic_payload = {
        "dataset_version": DATASET_VERSION,
        "split_version": SPLIT_VERSION,
        "feature_version": feature_version,
        "label_version": label_version,
        "horizon_seconds": horizon_seconds,
        "start": start,
        "end": end,
        "dataset_sha256": dataset.dataset_sha256,
        "split_sha256": split.split_sha256,
        "predictor_names": prepared.predictor_names,
        "dropped_all_missing": prepared.dropped_all_missing,
        "model_configs": model_configs,
        "validation_champion": validation_champion,
        "best_test_result": best_test_result,
        "boosted_promotion_eligible": boosted_promotion_eligible,
        "evaluations": {name: asdict(value) for name, value in evaluations.items()},
        "offset_metrics": offset_metrics,
        "gross_execution_diagnostic": gross_diagnostic,
        "artifacts": artifacts,
    }
    semantic_sha256 = canonical_hash(semantic_payload)
    report = TrainingRunReport(
        run_id=f"phase7-{horizon_seconds}-{semantic_sha256[:32]}",
        dataset_version=DATASET_VERSION,
        split_version=SPLIT_VERSION,
        feature_version=feature_version,
        label_version=label_version,
        horizon_seconds=horizon_seconds,
        start=start,
        end=end,
        dataset_sha256=dataset.dataset_sha256,
        split_sha256=split.split_sha256,
        predictor_names=prepared.predictor_names,
        dropped_all_missing=prepared.dropped_all_missing,
        model_configs=model_configs,
        validation_champion=validation_champion,
        best_test_result=best_test_result,
        boosted_promotion_eligible=boosted_promotion_eligible,
        evaluations=evaluations,
        offset_metrics=offset_metrics,
        gross_execution_diagnostic=gross_diagnostic,
        artifacts=artifacts,
        semantic_sha256=semantic_sha256,
        created_at=datetime.now(UTC),
    )
    ModelTrainingRunRepository().store(connection, report)
    return report
