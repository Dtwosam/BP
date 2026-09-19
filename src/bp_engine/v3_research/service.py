from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping

import numpy as np
from sqlalchemy import Connection, select

from bp_engine.calibration.calibrators import apply_calibration, select_calibrator
from bp_engine.features.calculators import book_state
from bp_engine.features.hashing import canonical_hash
from bp_engine.features.sources import FeatureSourceReader
from bp_engine.modeling.baselines import PriorBaseline
from bp_engine.modeling.dataset import load_dataset
from bp_engine.modeling.metrics import evaluate_probabilities
from bp_engine.modeling.models import (
    DatasetSnapshot,
    DatasetSplit,
    MarketPartition,
    SupervisedRow,
)
from bp_engine.modeling.split import equal_market_weights
from bp_engine.modeling.trainers import prepare_matrices, train_logistic, train_xgboost
from bp_engine.storage.schema import polymarket_markets
from bp_engine.v3_research.config import (
    FROZEN_V3_GATE_B_CONFIG,
    V3GateBConfig,
    v3_gate_b_config_payload,
)
from bp_engine.v3_research.policy import V3ExecutionBook, evaluate_edge_policy_v3


class V3PrepareIntegrityError(RuntimeError):
    """Raised when labeled V3 preparation would cross a frozen boundary."""


@dataclass(frozen=True)
class V3PreparedSelection:
    payload: dict[str, Any]
    model_bundle: dict[str, Any]


@dataclass(frozen=True)
class _CandidateFit:
    candidate: str
    offset_seconds: int
    validation_metrics: dict[str, Any]
    calibration: dict[str, Any]
    validation_probabilities: tuple[float, ...]
    test_probabilities: tuple[float, ...]
    model_bundle: dict[str, Any]


_MODEL_COMPLEXITY = {
    "training_prior": 0,
    "single_feature_btc_logistic": 1,
    "full_v3_logistic": 2,
    "full_v3_xgboost": 3,
}


def _verify_hash(payload: Mapping[str, Any], field: str) -> None:
    expected = payload.get(field)
    if not isinstance(expected, str) or len(expected) != 64:
        raise V3PrepareIntegrityError(f"{field} must be SHA-256")
    body = dict(payload)
    body.pop(field, None)
    if canonical_hash(body) != expected:
        raise V3PrepareIntegrityError(f"{field} mismatch")


def _partition_ids(partition: Mapping[str, Any]) -> tuple[str, ...]:
    raw = partition.get("condition_ids")
    if not isinstance(raw, list) or not raw:
        raise V3PrepareIntegrityError("partition condition_ids must be non-empty")
    values = tuple(str(value) for value in raw)
    if len(values) != len(set(values)):
        raise V3PrepareIntegrityError("partition contains duplicate condition ids")
    return values


def _final_ids(plan: Mapping[str, Any], key: str) -> tuple[str, ...]:
    final = plan.get("final")
    if not isinstance(final, Mapping):
        raise V3PrepareIntegrityError("plan final partition is missing")
    raw = final.get(key)
    if not isinstance(raw, list) or not raw:
        raise V3PrepareIntegrityError(f"final {key} must be non-empty")
    values = tuple(str(value) for value in raw)
    if len(values) != len(set(values)):
        raise V3PrepareIntegrityError(f"final {key} contains duplicates")
    return values


def verify_v3_prepare_plan(
    plan: Mapping[str, Any],
    config: V3GateBConfig = FROZEN_V3_GATE_B_CONFIG,
) -> None:
    _verify_hash(plan, "plan_sha256")
    if plan.get("research_plan_version") != config.research_plan_version:
        raise V3PrepareIntegrityError("unexpected V3 research plan")
    if plan.get("dataset_version") != config.dataset_version:
        raise V3PrepareIntegrityError("unexpected V3 dataset version")
    if plan.get("feature_version") != config.feature_version:
        raise V3PrepareIntegrityError("unexpected V3 feature version")
    if int(plan.get("horizon_seconds", 0)) != config.horizon_seconds:
        raise V3PrepareIntegrityError("unexpected V3 horizon")
    if tuple(plan.get("feature_offsets_seconds", ())) != config.feature_offsets_seconds:
        raise V3PrepareIntegrityError("frozen V3 offsets changed")
    if len(plan.get("folds", ())) != config.ordinary_fold_count:
        raise V3PrepareIntegrityError("ordinary fold count changed")
    if plan.get("labels_read") is not False:
        raise V3PrepareIntegrityError("plan must remain feature-only")
    if plan.get("training_performed") is not False:
        raise V3PrepareIntegrityError("plan already records training")
    if plan.get("final_holdout_evaluated") is not False:
        raise V3PrepareIntegrityError("plan already records holdout evaluation")
    config_hash = plan.get("config_sha256")
    if not isinstance(config_hash, str) or len(config_hash) != 64:
        raise V3PrepareIntegrityError("config_sha256 must be SHA-256")
    non_holdout_condition_ids(plan)


def non_holdout_condition_ids(plan: Mapping[str, Any]) -> tuple[str, ...]:
    holdout = set(_final_ids(plan, "holdout_condition_ids"))
    values: set[str] = set()
    folds = plan.get("folds")
    if not isinstance(folds, list):
        raise V3PrepareIntegrityError("plan folds must be a list")
    for fold in folds:
        for name in ("train", "validation", "test"):
            values.update(_partition_ids(fold[name]))
    values.update(_final_ids(plan, "train_condition_ids"))
    values.update(_final_ids(plan, "validation_condition_ids"))
    overlap = values.intersection(holdout)
    if overlap:
        raise V3PrepareIntegrityError(
            f"prepare partitions overlap final holdout: {sorted(overlap)[0]}"
        )
    return tuple(sorted(values))


def model_predictor_names(
    row_predictors: Mapping[str, float | None],
    candidate: str,
    config: V3GateBConfig = FROZEN_V3_GATE_B_CONFIG,
) -> tuple[str, ...]:
    missing = tuple(
        sorted(
            name
            for name in row_predictors
            if name.startswith("missing__")
            and "pm_" not in name
            and "polymarket" not in name.lower()
        )
    )
    if candidate == "single_feature_btc_logistic":
        required_missing = tuple(
            name for name in missing if name.startswith("missing__coinbase_")
        )
        return ("coinbase_return_from_market_start", *required_missing)
    if candidate in ("full_v3_logistic", "full_v3_xgboost"):
        return (*config.predictor_names, *missing)
    if candidate == "training_prior":
        return ()
    raise ValueError(f"unsupported probability candidate: {candidate}")


def xgboost_replacement_eligible(
    logistic_metrics: Mapping[str, Any],
    xgboost_metrics: Mapping[str, Any],
) -> bool:
    return (
        float(xgboost_metrics["log_loss"]) < float(logistic_metrics["log_loss"])
        and float(xgboost_metrics["brier_score"])
        < float(logistic_metrics["brier_score"])
    )


def validation_economics_pass(
    fold_metrics: list[Mapping[str, Any]],
    config: V3GateBConfig = FROZEN_V3_GATE_B_CONFIG,
) -> bool:
    if len(fold_metrics) != config.ordinary_fold_count:
        return False
    if any(
        int(item["trade_count"]) < config.min_validation_trades_per_fold
        for item in fold_metrics
    ):
        return False
    pnl = [float(item["realized_pnl_after_assumed_costs"]) for item in fold_metrics]
    if sum(value >= 0.0 for value in pnl) < config.required_non_negative_validation_folds:
        return False
    if config.require_positive_aggregate_validation_pnl and sum(pnl) <= 0.0:
        return False
    return True


def _load_non_holdout_dataset(
    connection: Connection,
    *,
    plan: Mapping[str, Any],
    config: V3GateBConfig,
) -> DatasetSnapshot:
    requested = non_holdout_condition_ids(plan)
    dataset = load_dataset(
        connection,
        start=config.epoch_start,
        end=config.epoch_end,
        horizon_seconds=config.horizon_seconds,
        feature_version=config.feature_version,
        label_version=config.label_version,
        condition_ids=requested,
        dataset_version=config.dataset_version,
    )
    observed = {row.condition_id for row in dataset.rows}
    missing = set(requested).difference(observed)
    if missing:
        raise V3PrepareIntegrityError(
            f"missing canonical non-holdout input for {sorted(missing)[0]}"
        )
    unexpected = observed.difference(requested)
    if unexpected:
        raise V3PrepareIntegrityError(
            f"dataset contains unrequested market {sorted(unexpected)[0]}"
        )
    holdout = set(_final_ids(plan, "holdout_condition_ids"))
    if observed.intersection(holdout):
        raise V3PrepareIntegrityError("prepare loaded a final-holdout label")
    offsets: dict[str, set[int]] = {condition_id: set() for condition_id in requested}
    for row in dataset.rows:
        offsets[row.condition_id].add(row.feature_offset_seconds)
        for name in row.predictors:
            if name.startswith("pm_") or "polymarket" in name.lower():
                raise V3PrepareIntegrityError(
                    f"Polymarket predictor entered V3 dataset: {name}"
                )
    for condition_id, actual in offsets.items():
        if actual != set(config.feature_offsets_seconds):
            raise V3PrepareIntegrityError(
                f"{condition_id} does not have exact frozen V3 offsets"
            )
    return dataset


def _rows_for_ids(
    dataset: DatasetSnapshot, condition_ids: tuple[str, ...]
) -> tuple[SupervisedRow, ...]:
    allowed = set(condition_ids)
    return tuple(row for row in dataset.rows if row.condition_id in allowed)


def _rows_at_offset(
    rows: tuple[SupervisedRow, ...], offset_seconds: int
) -> tuple[SupervisedRow, ...]:
    selected = tuple(
        row for row in rows if row.feature_offset_seconds == offset_seconds
    )
    if len({row.condition_id for row in selected}) != len(selected):
        raise V3PrepareIntegrityError(
            f"duplicate market at V3 offset {offset_seconds}"
        )
    return selected


def _restricted_rows(
    rows: tuple[SupervisedRow, ...],
    names: tuple[str, ...],
) -> tuple[SupervisedRow, ...]:
    return tuple(
        replace(
            row,
            predictors={name: row.predictors.get(name) for name in names},
        )
        for row in rows
    )


def _split_for_candidate(
    *,
    dataset_sha256: str,
    train_rows: tuple[SupervisedRow, ...],
    validation_rows: tuple[SupervisedRow, ...],
    test_rows: tuple[SupervisedRow, ...],
    candidate: str,
    offset_seconds: int,
) -> DatasetSplit:
    payload = {
        "dataset_sha256": dataset_sha256,
        "candidate": candidate,
        "offset_seconds": offset_seconds,
        "train": [row.condition_id for row in train_rows],
        "validation": [row.condition_id for row in validation_rows],
        "test": [row.condition_id for row in test_rows],
    }
    return DatasetSplit(
        split_version="v3-gate-b-frozen-plan-v1",
        dataset_sha256=dataset_sha256,
        train=MarketPartition(
            name="train",
            condition_ids=tuple(row.condition_id for row in train_rows),
            rows=train_rows,
        ),
        validation=MarketPartition(
            name="validation",
            condition_ids=tuple(row.condition_id for row in validation_rows),
            rows=validation_rows,
        ),
        test=MarketPartition(
            name="test",
            condition_ids=tuple(row.condition_id for row in test_rows),
            rows=test_rows,
        ),
        embargo_condition_ids=(),
        split_sha256=canonical_hash(payload),
    )


def _candidate_fit(
    *,
    dataset_sha256: str,
    train_rows: tuple[SupervisedRow, ...],
    validation_rows: tuple[SupervisedRow, ...],
    test_rows: tuple[SupervisedRow, ...],
    candidate: str,
    offset_seconds: int,
) -> _CandidateFit:
    if {row.target for row in train_rows} != {0, 1}:
        raise V3PrepareIntegrityError("V3 training partition must contain both classes")
    names = model_predictor_names(train_rows[0].predictors, candidate)
    restricted_train = _restricted_rows(train_rows, names)
    restricted_validation = _restricted_rows(validation_rows, names)
    restricted_test = _restricted_rows(test_rows, names)

    if candidate == "training_prior":
        prior = PriorBaseline()
        prior.fit(restricted_train, equal_market_weights(restricted_train))
        assert prior.probability is not None
        train_raw = prior.predict_proba(restricted_train)
        validation_raw = prior.predict_proba(restricted_validation)
        test_raw = prior.predict_proba(restricted_test)
        model_bundle: dict[str, Any] = {
            "candidate": candidate,
            "family": "prior",
            "offset_seconds": offset_seconds,
            "probability": prior.probability,
            "predictor_names": (),
            "config": {"weighted_market_prior": True},
        }
    else:
        split = _split_for_candidate(
            dataset_sha256=dataset_sha256,
            train_rows=restricted_train,
            validation_rows=restricted_validation,
            test_rows=restricted_test,
            candidate=candidate,
            offset_seconds=offset_seconds,
        )
        prepared = prepare_matrices(split)
        if candidate in ("single_feature_btc_logistic", "full_v3_logistic"):
            trained = train_logistic(split, prepared)
            train_raw = tuple(
                float(value)
                for value in trained.estimator.predict_proba(
                    prepared.x_train_scaled
                )[:, 1]
            )
            model_bundle = {
                "candidate": candidate,
                "family": "logistic",
                "offset_seconds": offset_seconds,
                "predictor_names": prepared.predictor_names,
                "dropped_all_missing": prepared.dropped_all_missing,
                "imputer": prepared.imputer,
                "scaler": prepared.scaler,
                "estimator": trained.estimator,
                "config": trained.config,
            }
        elif candidate == "full_v3_xgboost":
            trained = train_xgboost(split, prepared)
            train_raw = tuple(
                float(value)
                for value in trained.estimator.predict_proba(prepared.x_train)[:, 1]
            )
            model_bundle = {
                "candidate": candidate,
                "family": "xgboost",
                "offset_seconds": offset_seconds,
                "predictor_names": prepared.predictor_names,
                "dropped_all_missing": prepared.dropped_all_missing,
                "imputer": prepared.imputer,
                "estimator": trained.estimator,
                "config": trained.config,
            }
        else:
            raise ValueError(f"unsupported V3 candidate: {candidate}")
        validation_raw = trained.validation_probabilities
        test_raw = trained.test_probabilities

    calibration = select_calibrator(
        restricted_train,
        train_raw,
        restricted_validation,
        validation_raw,
    )
    validation_calibrated = apply_calibration(calibration.fit, validation_raw)
    test_calibrated = apply_calibration(calibration.fit, test_raw)
    metrics = asdict(
        evaluate_probabilities(
            restricted_validation,
            validation_calibrated,
            equal_market_weights(restricted_validation),
        )
    )
    calibration_payload = {
        "method": calibration.method,
        "fit": asdict(calibration.fit),
        "validation_metrics": asdict(calibration.validation_metrics),
        "candidates": [asdict(item) for item in calibration.candidates],
    }
    model_bundle["calibration_fit"] = asdict(calibration.fit)
    return _CandidateFit(
        candidate=candidate,
        offset_seconds=offset_seconds,
        validation_metrics=metrics,
        calibration=calibration_payload,
        validation_probabilities=tuple(validation_calibrated),
        test_probabilities=tuple(test_calibrated),
        model_bundle=model_bundle,
    )


def _momentum_diagnostic(
    rows: tuple[SupervisedRow, ...],
) -> dict[str, Any]:
    covered = correct = 0
    for row in rows:
        value = row.predictors.get("coinbase_return_from_market_start")
        if value is None or float(value) == 0.0:
            continue
        covered += 1
        predicted = 1 if float(value) > 0.0 else 0
        correct += int(predicted == row.target)
    return {
        "covered_markets": covered,
        "total_markets": len(rows),
        "coverage": covered / len(rows) if rows else 0.0,
        "directional_accuracy": correct / covered if covered else None,
        "probability_candidate": False,
    }


def _fit_forecast_selection(
    *,
    dataset_sha256: str,
    train_rows: tuple[SupervisedRow, ...],
    validation_rows: tuple[SupervisedRow, ...],
    test_rows: tuple[SupervisedRow, ...],
    config: V3GateBConfig,
) -> tuple[_CandidateFit, list[dict[str, Any]], dict[str, Any]]:
    fits: list[_CandidateFit] = []
    momentum: dict[str, Any] = {}
    probability_candidates = (
        "training_prior",
        "single_feature_btc_logistic",
        "full_v3_logistic",
        "full_v3_xgboost",
    )
    for offset in config.offset_candidates_seconds:
        train_offset = _rows_at_offset(train_rows, offset)
        validation_offset = _rows_at_offset(validation_rows, offset)
        test_offset = _rows_at_offset(test_rows, offset)
        momentum[str(offset)] = _momentum_diagnostic(validation_offset)
        for candidate in probability_candidates:
            try:
                fits.append(
                    _candidate_fit(
                        dataset_sha256=dataset_sha256,
                        train_rows=train_offset,
                        validation_rows=validation_offset,
                        test_rows=test_offset,
                        candidate=candidate,
                        offset_seconds=offset,
                    )
                )
            except (ValueError, V3PrepareIntegrityError):
                if candidate == "training_prior":
                    raise

    full_by_offset = {
        fit.offset_seconds: fit
        for fit in fits
        if fit.candidate == "full_v3_logistic"
    }
    reports: list[dict[str, Any]] = []
    selectable: list[_CandidateFit] = []
    for fit in fits:
        eligible = True
        reason = "eligible"
        if fit.candidate == "full_v3_xgboost":
            baseline = full_by_offset.get(fit.offset_seconds)
            eligible = baseline is not None and xgboost_replacement_eligible(
                baseline.validation_metrics, fit.validation_metrics
            )
            if not eligible:
                reason = "xgboost_replacement_rule_not_met"
        reports.append(
            {
                "candidate": fit.candidate,
                "offset_seconds": fit.offset_seconds,
                "eligible_for_selection": eligible,
                "eligibility_reason": reason,
                "validation_metrics": fit.validation_metrics,
                "calibration": fit.calibration,
            }
        )
        if eligible:
            selectable.append(fit)
    if not selectable:
        raise V3PrepareIntegrityError("no V3 probability candidate is selectable")
    selected = min(
        selectable,
        key=lambda fit: (
            float(fit.validation_metrics["log_loss"]),
            float(fit.validation_metrics["brier_score"]),
            float(fit.validation_metrics["ece"]),
            _MODEL_COMPLEXITY[fit.candidate],
            fit.offset_seconds,
        ),
    )
    return selected, reports, momentum


def _token_ids(
    connection: Connection,
    condition_ids: tuple[str, ...],
) -> dict[str, tuple[str, str]]:
    rows = connection.execute(
        select(
            polymarket_markets.c.condition_id,
            polymarket_markets.c.up_token_id,
            polymarket_markets.c.down_token_id,
        ).where(polymarket_markets.c.condition_id.in_(condition_ids))
    ).mappings().all()
    result = {
        str(row["condition_id"]): (
            str(row["up_token_id"]),
            str(row["down_token_id"]),
        )
        for row in rows
    }
    missing = set(condition_ids).difference(result)
    if missing:
        raise V3PrepareIntegrityError(
            f"missing static market token mapping for {sorted(missing)[0]}"
        )
    return result


def _execution_books(
    connection: Connection,
    rows: tuple[SupervisedRow, ...],
    *,
    max_age_seconds: int,
) -> dict[str, V3ExecutionBook]:
    ids = tuple(row.condition_id for row in rows)
    tokens = _token_ids(connection, ids)
    reader = FeatureSourceReader(state_fresh_seconds=float(max_age_seconds))
    result: dict[str, V3ExecutionBook] = {}
    for row in rows:
        up_token, down_token = tokens[row.condition_id]
        up = reader.latest_state(
            connection,
            source="polymarket",
            stream="market",
            instrument=row.condition_id,
            feature_at=row.feature_at,
            asset_id=up_token,
        )
        down = reader.latest_state(
            connection,
            source="polymarket",
            stream="market",
            instrument=row.condition_id,
            feature_at=row.feature_at,
            asset_id=down_token,
        )
        up_group = book_state("pm_up", up)
        down_group = book_state("pm_down", down)
        up_missing = bool(up_group.missing_flags["pm_up_book_missing"])
        up_stale = bool(up_group.missing_flags["pm_up_book_stale"])
        down_missing = bool(down_group.missing_flags["pm_down_book_missing"])
        down_stale = bool(down_group.missing_flags["pm_down_book_stale"])
        result[row.condition_id] = V3ExecutionBook(
            up_best_bid=up_group.values["pm_up_best_bid"],
            up_best_ask=up_group.values["pm_up_best_ask"],
            up_fresh=not up_missing and not up_stale,
            down_best_bid=down_group.values["pm_down_best_bid"],
            down_best_ask=down_group.values["pm_down_best_ask"],
            down_fresh=not down_missing and not down_stale,
        )
    return result


def _edge_selection(
    rows: tuple[SupervisedRow, ...],
    probabilities: tuple[float, ...],
    books: dict[str, V3ExecutionBook],
    *,
    config: V3GateBConfig,
) -> dict[str, Any]:
    probability_map = {
        row.condition_id: probability
        for row, probability in zip(rows, probabilities, strict=True)
    }
    candidates: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    for min_edge in config.min_edge_grid:
        metrics = evaluate_edge_policy_v3(
            rows,
            probability_map,
            books,
            fee_rate=config.fee_rate,
            slippage_buffer=config.slippage_buffer,
            min_edge=min_edge,
        )
        item = {
            "min_edge": min_edge,
            "metrics": asdict(metrics),
            "minimum_trade_count_met": (
                metrics.trade_count >= config.min_validation_trades_per_fold
            ),
        }
        candidates.append(item)
        if item["minimum_trade_count_met"]:
            eligible.append(item)
    if not eligible:
        no_trade = evaluate_edge_policy_v3(
            rows,
            probability_map,
            books,
            fee_rate=config.fee_rate,
            slippage_buffer=config.slippage_buffer,
            min_edge=None,
        )
        return {
            "policy": "no_trade",
            "reason": "no_threshold_meets_minimum_validation_trades",
            "min_edge": None,
            "validation_metrics": asdict(no_trade),
            "candidates": candidates,
        }
    selected = max(
        eligible,
        key=lambda item: (
            item["metrics"]["realized_pnl_after_assumed_costs"],
            item["metrics"]["mean_realized_pnl_after_assumed_costs"],
            item["metrics"]["trade_count"],
            item["min_edge"],
        ),
    )
    return {
        "policy": "trade_threshold",
        "reason": "validation_selected",
        "min_edge": selected["min_edge"],
        "validation_metrics": selected["metrics"],
        "candidates": candidates,
    }


def _evaluate_edge(
    rows: tuple[SupervisedRow, ...],
    probabilities: tuple[float, ...],
    books: dict[str, V3ExecutionBook],
    *,
    min_edge: float | None,
    config: V3GateBConfig,
) -> dict[str, Any]:
    mapping = {
        row.condition_id: probability
        for row, probability in zip(rows, probabilities, strict=True)
    }
    return asdict(
        evaluate_edge_policy_v3(
            rows,
            mapping,
            books,
            fee_rate=config.fee_rate,
            slippage_buffer=config.slippage_buffer,
            min_edge=min_edge,
        )
    )


def _candidate_summary(fit: _CandidateFit) -> dict[str, Any]:
    return {
        "candidate": fit.candidate,
        "offset_seconds": fit.offset_seconds,
        "validation_metrics": fit.validation_metrics,
        "calibration": fit.calibration,
        "model_config": dict(fit.model_bundle["config"]),
    }



def _json_safe_config(config: V3GateBConfig) -> dict[str, Any]:
    payload = v3_gate_b_config_payload(config)
    result: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, tuple):
            result[key] = list(value)
        elif hasattr(value, "isoformat"):
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result

def prepare_v3_gate_b(
    connection: Connection,
    *,
    plan: dict[str, Any],
    config: V3GateBConfig = FROZEN_V3_GATE_B_CONFIG,
) -> V3PreparedSelection:
    verify_v3_prepare_plan(plan, config)
    dataset = _load_non_holdout_dataset(connection, plan=plan, config=config)
    holdout_ids = _final_ids(plan, "holdout_condition_ids")
    fold_reports: list[dict[str, Any]] = []
    validation_edge_metrics: list[Mapping[str, Any]] = []

    for fold in plan["folds"]:
        train_ids = _partition_ids(fold["train"])
        validation_ids = _partition_ids(fold["validation"])
        test_ids = _partition_ids(fold["test"])
        train_rows = _rows_for_ids(dataset, train_ids)
        validation_rows = _rows_for_ids(dataset, validation_ids)
        test_rows = _rows_for_ids(dataset, test_ids)
        selected, candidates, momentum = _fit_forecast_selection(
            dataset_sha256=dataset.dataset_sha256,
            train_rows=train_rows,
            validation_rows=validation_rows,
            test_rows=test_rows,
            config=config,
        )
        offset = selected.offset_seconds
        validation_offset = _rows_at_offset(validation_rows, offset)
        test_offset = _rows_at_offset(test_rows, offset)
        validation_books = _execution_books(
            connection,
            validation_offset,
            max_age_seconds=config.max_selected_book_age_seconds,
        )
        test_books = _execution_books(
            connection,
            test_offset,
            max_age_seconds=config.max_selected_book_age_seconds,
        )
        edge = _edge_selection(
            validation_offset,
            selected.validation_probabilities,
            validation_books,
            config=config,
        )
        validation_edge_metrics.append(edge["validation_metrics"])
        test_edge = _evaluate_edge(
            test_offset,
            selected.test_probabilities,
            test_books,
            min_edge=edge["min_edge"] if edge["policy"] == "trade_threshold" else None,
            config=config,
        )
        fold_reports.append(
            {
                "index": int(fold["index"]),
                "membership_sha256": fold["membership_sha256"],
                "train_condition_ids": list(train_ids),
                "validation_condition_ids": list(validation_ids),
                "test_condition_ids": list(test_ids),
                "forecast_candidates": candidates,
                "momentum_diagnostic_by_offset": momentum,
                "selected_forecast": _candidate_summary(selected),
                "edge_selection": edge,
                "ordinary_test": {
                    "forecast_metrics": asdict(
                        evaluate_probabilities(
                            test_offset,
                            selected.test_probabilities,
                            equal_market_weights(test_offset),
                        )
                    ),
                    "edge_metrics": test_edge,
                },
            }
        )

    economics_passed = validation_economics_pass(validation_edge_metrics, config)

    final_train_ids = _final_ids(plan, "train_condition_ids")
    final_validation_ids = _final_ids(plan, "validation_condition_ids")
    final_train_rows = _rows_for_ids(dataset, final_train_ids)
    final_validation_rows = _rows_for_ids(dataset, final_validation_ids)
    final_selected, final_candidates, final_momentum = _fit_forecast_selection(
        dataset_sha256=dataset.dataset_sha256,
        train_rows=final_train_rows,
        validation_rows=final_validation_rows,
        test_rows=final_validation_rows,
        config=config,
    )
    final_offset = final_selected.offset_seconds
    final_validation_offset = _rows_at_offset(final_validation_rows, final_offset)
    final_books = _execution_books(
        connection,
        final_validation_offset,
        max_age_seconds=config.max_selected_book_age_seconds,
    )
    final_edge = _edge_selection(
        final_validation_offset,
        final_selected.validation_probabilities,
        final_books,
        config=config,
    )
    if not economics_passed:
        final_edge = {
            **final_edge,
            "policy": "no_trade",
            "reason": "ordinary_validation_economics_gate_failed",
            "min_edge": None,
        }

    model_bundle = dict(final_selected.model_bundle)
    model_bundle.update(
        {
            "research_plan_version": config.research_plan_version,
            "plan_sha256": plan["plan_sha256"],
            "dataset_sha256_non_holdout": dataset.dataset_sha256,
            "selected_min_edge": final_edge["min_edge"],
            "edge_policy": final_edge["policy"],
            "fee_rate": config.fee_rate,
            "slippage_buffer": config.slippage_buffer,
            "max_selected_book_age_seconds": config.max_selected_book_age_seconds,
        }
    )
    payload: dict[str, Any] = {
        "research_plan_version": config.research_plan_version,
        "stage": "ordinary_selection_frozen",
        "dataset_version": config.dataset_version,
        "feature_version": config.feature_version,
        "label_version": config.label_version,
        "plan_sha256": plan["plan_sha256"],
        "readiness_input_sha256": plan["readiness_input_sha256"],
        "feature_manifest_sha256": plan["feature_manifest_sha256"],
        "dataset_sha256_non_holdout": dataset.dataset_sha256,
        "config": _json_safe_config(config),
        "folds": fold_reports,
        "ordinary_validation_economics_passed": economics_passed,
        "final": {
            "membership_sha256": plan["final"]["membership_sha256"],
            "train_condition_ids": list(final_train_ids),
            "validation_condition_ids": list(final_validation_ids),
            "holdout_condition_ids": list(holdout_ids),
            "forecast_candidates": final_candidates,
            "momentum_diagnostic_by_offset": final_momentum,
            "selected_forecast": _candidate_summary(final_selected),
            "edge_selection": final_edge,
        },
        "labels_read_non_holdout": True,
        "holdout_labels_read": False,
        "holdout_evaluated": False,
        "training_performed": True,
        "automatic_promotion": False,
    }
    return V3PreparedSelection(payload=payload, model_bundle=model_bundle)


def finalize_v3_selection(
    prepared: V3PreparedSelection,
    *,
    model_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    payload = dict(prepared.payload)
    payload["model_artifact"] = dict(model_artifact)
    payload["selection_sha256"] = canonical_hash(payload)
    return payload
