from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from typing import Any

from sqlalchemy import Connection

from bp_engine.calibration.calibrators import (
    CalibrationRejected,
    IdentityCalibrator,
    PlattCalibrator,
    apply_calibration,
)
from bp_engine.calibration.models import CalibrationFit
from bp_engine.features.hashing import canonical_hash
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
from bp_engine.v3_research.policy import edge_band_report_v3
from bp_engine.v3_research.service import _execution_books
from bp_engine.v4_research.config import (
    FROZEN_V4_GATE_B_CONFIG,
    V4GateBConfig,
    v4_gate_b_config_payload,
)
from bp_engine.v4_research.plan import _config_payload as _unused_config_payload
from bp_engine.v4_research.policy import evaluate_economics_v4

AUTHORIZED_V4_GATE_B_V2_PLAN_SHA256 = (
    "9c017b1d968925a8cddab18324628227ed8b8b381e43c653f72c0f26366ee557"
)

_MODEL_COMPLEXITY = {
    "training_prior": 0,
    "single_feature_btc_logistic": 1,
    "short_context_v4_logistic": 2,
    "full_v4_logistic": 3,
    "full_v4_xgboost": 4,
}


class V4PrepareIntegrityError(RuntimeError):
    """Raised when V4 labeled preparation would cross a frozen boundary."""


@dataclass(frozen=True)
class V4PreparedSelection:
    payload: dict[str, Any]
    model_bundle: dict[str, Any]


@dataclass(frozen=True)
class _RawFit:
    candidate: str
    offset_seconds: int
    train_rows: tuple[SupervisedRow, ...]
    validation_rows: tuple[SupervisedRow, ...]
    test_rows: tuple[SupervisedRow, ...]
    train_probabilities: tuple[float, ...]
    validation_probabilities: tuple[float, ...]
    test_probabilities: tuple[float, ...]
    model_bundle: dict[str, Any]


@dataclass(frozen=True)
class _CalibrationView:
    method: str
    fit: CalibrationFit
    validation_probabilities: tuple[float, ...]
    test_probabilities: tuple[float, ...]
    validation_metrics: dict[str, Any]
    validation_slices: dict[str, Any]


def _verify_hash(payload: Mapping[str, Any], field: str) -> None:
    expected = payload.get(field)
    if not isinstance(expected, str) or len(expected) != 64:
        raise V4PrepareIntegrityError(f"{field} must be SHA-256")
    body = dict(payload)
    body.pop(field, None)
    if canonical_hash(body) != expected:
        raise V4PrepareIntegrityError(f"{field} mismatch")


def _json_safe_config(config: V4GateBConfig) -> dict[str, Any]:
    payload = v4_gate_b_config_payload(config)
    result: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, tuple):
            result[key] = list(value)
        elif hasattr(value, "isoformat"):
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result


def _partition_ids(partition: Mapping[str, Any]) -> tuple[str, ...]:
    raw = partition.get("condition_ids")
    if not isinstance(raw, list) or not raw:
        raise V4PrepareIntegrityError("partition condition_ids must be non-empty")
    values = tuple(str(value) for value in raw)
    if len(values) != len(set(values)):
        raise V4PrepareIntegrityError("partition contains duplicate condition ids")
    return values


def _final_ids(plan: Mapping[str, Any], key: str) -> tuple[str, ...]:
    final = plan.get("final")
    if not isinstance(final, Mapping):
        raise V4PrepareIntegrityError("plan final partition is missing")
    raw = final.get(key)
    if not isinstance(raw, list) or not raw:
        raise V4PrepareIntegrityError(f"final {key} must be non-empty")
    values = tuple(str(value) for value in raw)
    if len(values) != len(set(values)):
        raise V4PrepareIntegrityError(f"final {key} contains duplicates")
    return values


def verify_v4_prepare_plan(
    plan: Mapping[str, Any],
    config: V4GateBConfig = FROZEN_V4_GATE_B_CONFIG,
) -> None:
    _verify_hash(plan, "plan_sha256")
    if plan.get("plan_sha256") != AUTHORIZED_V4_GATE_B_V2_PLAN_SHA256:
        raise V4PrepareIntegrityError("plan is not the explicitly authorized V4 plan")
    if plan.get("research_plan_version") != config.research_plan_version:
        raise V4PrepareIntegrityError("unexpected V4 research plan")
    if plan.get("dataset_version") != config.dataset_version:
        raise V4PrepareIntegrityError("unexpected V4 dataset version")
    if plan.get("feature_version") != config.feature_version:
        raise V4PrepareIntegrityError("unexpected V4 feature version")
    if plan.get("label_version") != config.label_version:
        raise V4PrepareIntegrityError("unexpected V4 label version")
    if int(plan.get("horizon_seconds", 0)) != config.horizon_seconds:
        raise V4PrepareIntegrityError("unexpected V4 horizon")
    if tuple(plan.get("feature_offsets_seconds", ())) != config.feature_offsets_seconds:
        raise V4PrepareIntegrityError("frozen V4 offsets changed")
    if len(plan.get("folds", ())) != config.ordinary_fold_count:
        raise V4PrepareIntegrityError("ordinary fold count changed")
    if plan.get("labels_read") is not False:
        raise V4PrepareIntegrityError("plan must remain feature-only")
    if plan.get("training_performed") is not False:
        raise V4PrepareIntegrityError("plan already records training")
    if plan.get("policy_selected") is not False:
        raise V4PrepareIntegrityError("plan already records policy selection")
    if plan.get("final_holdout_evaluated") is not False:
        raise V4PrepareIntegrityError("plan already records holdout evaluation")
    expected_config_hash = canonical_hash(v4_gate_b_config_payload(config))
    if plan.get("config_sha256") != expected_config_hash:
        raise V4PrepareIntegrityError("config_sha256 does not match frozen V4 config")
    non_holdout_condition_ids(plan)


def non_holdout_condition_ids(plan: Mapping[str, Any]) -> tuple[str, ...]:
    holdout = set(_final_ids(plan, "holdout_condition_ids"))
    values: set[str] = set()
    folds = plan.get("folds")
    if not isinstance(folds, list):
        raise V4PrepareIntegrityError("plan folds must be a list")
    for fold in folds:
        for name in ("train", "validation", "test"):
            values.update(_partition_ids(fold[name]))
    values.update(_final_ids(plan, "train_condition_ids"))
    values.update(_final_ids(plan, "validation_condition_ids"))
    overlap = values.intersection(holdout)
    if overlap:
        raise V4PrepareIntegrityError(
            f"non-holdout partitions overlap final holdout: {sorted(overlap)[0]}"
        )
    return tuple(sorted(values))


def _load_non_holdout_dataset(
    connection: Connection,
    *,
    plan: Mapping[str, Any],
    config: V4GateBConfig,
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
    expected = set(requested)
    missing = expected.difference(observed)
    if missing:
        raise V4PrepareIntegrityError(
            f"missing canonical non-holdout input for {sorted(missing)[0]}"
        )
    unexpected = observed.difference(expected)
    if unexpected:
        raise V4PrepareIntegrityError(
            f"dataset contains unrequested market {sorted(unexpected)[0]}"
        )
    holdout = set(_final_ids(plan, "holdout_condition_ids"))
    if observed.intersection(holdout):
        raise V4PrepareIntegrityError("prepare loaded a final-holdout label")
    offsets: dict[str, set[int]] = {condition_id: set() for condition_id in requested}
    for row in dataset.rows:
        offsets[row.condition_id].add(row.feature_offset_seconds)
        for name in row.predictors:
            if name.startswith("pm_") or "polymarket" in name.lower():
                raise V4PrepareIntegrityError(
                    f"Polymarket predictor entered V4 dataset: {name}"
                )
    for condition_id, actual in offsets.items():
        if actual != set(config.feature_offsets_seconds):
            raise V4PrepareIntegrityError(
                f"{condition_id} does not have exact frozen V4 offsets"
            )
    return dataset


def _rows_for_ids(
    dataset: DatasetSnapshot,
    condition_ids: tuple[str, ...],
) -> tuple[SupervisedRow, ...]:
    allowed = set(condition_ids)
    return tuple(row for row in dataset.rows if row.condition_id in allowed)


def _rows_at_offset(
    rows: tuple[SupervisedRow, ...],
    offset_seconds: int,
) -> tuple[SupervisedRow, ...]:
    selected = tuple(row for row in rows if row.feature_offset_seconds == offset_seconds)
    if len({row.condition_id for row in selected}) != len(selected):
        raise V4PrepareIntegrityError(f"duplicate market at V4 offset {offset_seconds}")
    return selected


def _missing_predictors(
    row_predictors: Mapping[str, float | None],
    *,
    short_context_only: bool,
) -> tuple[str, ...]:
    values = []
    for name in row_predictors:
        if not name.startswith("missing__"):
            continue
        lowered = name.lower()
        if "pm_" in lowered or "polymarket" in lowered:
            continue
        if short_context_only and (
            "_regime_" in lowered
            or lowered.startswith("missing__regime_")
        ):
            continue
        values.append(name)
    return tuple(sorted(values))


def model_predictor_names(
    row_predictors: Mapping[str, float | None],
    candidate: str,
    config: V4GateBConfig = FROZEN_V4_GATE_B_CONFIG,
) -> tuple[str, ...]:
    if candidate == "training_prior":
        return ()
    if candidate == "single_feature_btc_logistic":
        required_missing_names = (
            "missing__coinbase_market_start_missing",
            "missing__coinbase_market_start_stale",
            "missing__coinbase_current_missing",
            "missing__coinbase_current_stale",
        )
        missing = tuple(
            name for name in required_missing_names if name in row_predictors
        )
        return ("coinbase_return_from_market_start", *missing)
    if candidate == "short_context_v4_logistic":
        missing = _missing_predictors(row_predictors, short_context_only=True)
        return (*config.short_context_predictor_names, *missing)
    if candidate in ("full_v4_logistic", "full_v4_xgboost"):
        missing = _missing_predictors(row_predictors, short_context_only=False)
        return (*config.predictor_names, *missing)
    raise ValueError(f"unsupported V4 probability candidate: {candidate}")


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
        split_version="v4-gate-b-v2-frozen-plan-v1",
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


def _fit_raw_candidate(
    *,
    dataset_sha256: str,
    train_rows: tuple[SupervisedRow, ...],
    validation_rows: tuple[SupervisedRow, ...],
    test_rows: tuple[SupervisedRow, ...],
    candidate: str,
    offset_seconds: int,
    config: V4GateBConfig,
) -> _RawFit:
    if {row.target for row in train_rows} != {0, 1}:
        raise V4PrepareIntegrityError("V4 training partition must contain both classes")
    names = model_predictor_names(train_rows[0].predictors, candidate, config)
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
        bundle: dict[str, Any] = {
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
        if candidate in (
            "single_feature_btc_logistic",
            "short_context_v4_logistic",
            "full_v4_logistic",
        ):
            trained = train_logistic(split, prepared)
            train_raw = tuple(
                float(value)
                for value in trained.estimator.predict_proba(
                    prepared.x_train_scaled
                )[:, 1]
            )
            bundle = {
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
        elif candidate == "full_v4_xgboost":
            trained = train_xgboost(split, prepared)
            train_raw = tuple(
                float(value)
                for value in trained.estimator.predict_proba(prepared.x_train)[:, 1]
            )
            bundle = {
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
            raise ValueError(f"unsupported V4 candidate: {candidate}")
        validation_raw = trained.validation_probabilities
        test_raw = trained.test_probabilities

    return _RawFit(
        candidate=candidate,
        offset_seconds=offset_seconds,
        train_rows=restricted_train,
        validation_rows=restricted_validation,
        test_rows=restricted_test,
        train_probabilities=tuple(train_raw),
        validation_probabilities=tuple(validation_raw),
        test_probabilities=tuple(test_raw),
        model_bundle=bundle,
    )


def _regime(row: SupervisedRow) -> str:
    if row.predictors.get("regime_bull") == 1.0:
        return "bull"
    if row.predictors.get("regime_bear") == 1.0:
        return "bear"
    if row.predictors.get("regime_sideways_mixed") == 1.0:
        return "sideways_mixed"
    return "unknown"


def _metric_dict(
    rows: tuple[SupervisedRow, ...],
    probabilities: tuple[float, ...],
) -> dict[str, Any] | None:
    if not rows:
        return None
    return asdict(
        evaluate_probabilities(
            rows,
            probabilities,
            equal_market_weights(rows),
        )
    )


def _filter_pair(
    rows: tuple[SupervisedRow, ...],
    probabilities: tuple[float, ...],
    predicate,
) -> tuple[tuple[SupervisedRow, ...], tuple[float, ...]]:
    pairs = [
        (row, probability)
        for row, probability in zip(rows, probabilities, strict=True)
        if predicate(row)
    ]
    return tuple(row for row, _ in pairs), tuple(probability for _, probability in pairs)


def probability_slices(
    rows: tuple[SupervisedRow, ...],
    probabilities: tuple[float, ...],
    *,
    config: V4GateBConfig = FROZEN_V4_GATE_B_CONFIG,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    definitions = {
        "overall": lambda row: True,
        "bull": lambda row: _regime(row) == "bull",
        "bear": lambda row: _regime(row) == "bear",
        "sideways_mixed": lambda row: _regime(row) == "sideways_mixed",
        "unknown": lambda row: _regime(row) == "unknown",
        "up": lambda row: row.target == 1,
        "down": lambda row: row.target == 0,
    }
    for name, predicate in definitions.items():
        subset, subset_probabilities = _filter_pair(rows, probabilities, predicate)
        result[name] = {
            "market_count": len(subset),
            "status": (
                "evaluable"
                if name == "overall" or len(subset) >= config.min_reporting_slice_markets
                else "insufficient_slice_evidence"
            ),
            "metrics": _metric_dict(subset, subset_probabilities),
        }

    regime_by_side: dict[str, Any] = {}
    for regime in (*config.known_regimes, "unknown"):
        for target, side in ((1, "up"), (0, "down")):
            key = f"{regime}_{side}"
            subset, subset_probabilities = _filter_pair(
                rows,
                probabilities,
                lambda row, regime=regime, target=target: (
                    _regime(row) == regime and row.target == target
                ),
            )
            regime_by_side[key] = {
                "market_count": len(subset),
                "status": (
                    "evaluable"
                    if len(subset) >= config.min_reporting_slice_markets
                    else "insufficient_slice_evidence"
                ),
                "metrics": _metric_dict(subset, subset_probabilities),
            }
    result["regime_by_side"] = regime_by_side
    return result


def _fit_calibration(
    raw: _RawFit,
    *,
    method: str,
    config: V4GateBConfig,
) -> _CalibrationView:
    if method == "identity":
        calibrator = IdentityCalibrator()
        fit = calibrator.fit(
            raw.train_rows,
            raw.train_probabilities,
            equal_market_weights(raw.train_rows),
        )
    elif method == "platt":
        calibrator = PlattCalibrator()
        fit = calibrator.fit(
            raw.train_rows,
            raw.train_probabilities,
            equal_market_weights(raw.train_rows),
        )
    else:
        raise ValueError(f"unsupported calibration method: {method}")
    validation = apply_calibration(fit, raw.validation_probabilities)
    test = apply_calibration(fit, raw.test_probabilities)
    metrics = _metric_dict(raw.validation_rows, validation)
    assert metrics is not None
    return _CalibrationView(
        method=method,
        fit=fit,
        validation_probabilities=validation,
        test_probabilities=test,
        validation_metrics=metrics,
        validation_slices=probability_slices(
            raw.validation_rows,
            validation,
            config=config,
        ),
    )


def _mean(items: list[float]) -> float:
    if not items:
        raise V4PrepareIntegrityError("cannot aggregate empty metric list")
    return sum(items) / len(items)


def _aggregate_regime_metrics(
    fold_views: list[tuple[_RawFit, _CalibrationView]],
    *,
    config: V4GateBConfig,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for regime in config.known_regimes:
        rows: list[SupervisedRow] = []
        probabilities: list[float] = []
        for raw, view in fold_views:
            subset, subset_probabilities = _filter_pair(
                raw.validation_rows,
                view.validation_probabilities,
                lambda row, regime=regime: _regime(row) == regime,
            )
            rows.extend(subset)
            probabilities.extend(subset_probabilities)
        row_tuple = tuple(rows)
        probability_tuple = tuple(probabilities)
        result[regime] = {
            "market_count": len(row_tuple),
            "status": (
                "evaluable"
                if len(row_tuple) >= config.min_reporting_slice_markets
                else "insufficient_slice_evidence"
            ),
            "metrics": _metric_dict(row_tuple, probability_tuple),
        }
    return result


def _calibration_selection(
    raw_folds: list[_RawFit],
    *,
    config: V4GateBConfig,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    by_method: dict[str, list[tuple[_RawFit, _CalibrationView]]] = {}
    failures: dict[str, str] = {}
    for method in config.calibration_candidates:
        views: list[tuple[_RawFit, _CalibrationView]] = []
        try:
            for raw in raw_folds:
                views.append((raw, _fit_calibration(raw, method=method, config=config)))
        except CalibrationRejected as exc:
            failures[method] = str(exc)
            continue
        by_method[method] = views

    identity = by_method.get("identity")
    if identity is None:
        raise V4PrepareIntegrityError("identity calibration is unavailable")

    reports: list[dict[str, Any]] = []
    aggregates: dict[str, dict[str, Any]] = {}
    for method in config.calibration_candidates:
        views = by_method.get(method)
        if views is None:
            reports.append(
                {
                    "method": method,
                    "eligible": False,
                    "reason": failures.get(method, "calibration_unavailable"),
                }
            )
            continue
        mean_log_loss = _mean(
            [float(view.validation_metrics["log_loss"]) for _, view in views]
        )
        mean_brier = _mean(
            [float(view.validation_metrics["brier_score"]) for _, view in views]
        )
        regimes = _aggregate_regime_metrics(views, config=config)
        aggregates[method] = {
            "mean_fold_log_loss": mean_log_loss,
            "mean_fold_brier_score": mean_brier,
            "known_regime_validation": regimes,
        }
        reports.append(
            {
                "method": method,
                "eligible": True,
                "reason": "identity_baseline" if method == "identity" else "candidate",
                **aggregates[method],
                "folds": [
                    {
                        "validation_metrics": view.validation_metrics,
                        "calibration_fit": asdict(view.fit),
                    }
                    for _, view in views
                ],
            }
        )

    selected = "identity"
    platt = by_method.get("platt")
    if platt is not None:
        identity_aggregate = aggregates["identity"]
        platt_aggregate = aggregates["platt"]
        overall_better = (
            platt_aggregate["mean_fold_log_loss"]
            < identity_aggregate["mean_fold_log_loss"]
            and platt_aggregate["mean_fold_brier_score"]
            < identity_aggregate["mean_fold_brier_score"]
        )
        regime_ok = True
        for regime in config.known_regimes:
            left = identity_aggregate["known_regime_validation"][regime]
            right = platt_aggregate["known_regime_validation"][regime]
            if left["status"] != "evaluable" or right["status"] != "evaluable":
                continue
            left_metrics = left["metrics"]
            right_metrics = right["metrics"]
            assert left_metrics is not None and right_metrics is not None
            if (
                float(right_metrics["log_loss"]) > float(left_metrics["log_loss"])
                and float(right_metrics["brier_score"])
                > float(left_metrics["brier_score"])
            ):
                regime_ok = False
                break
        if overall_better and regime_ok:
            selected = "platt"

    for report in reports:
        if report["method"] == "platt" and report.get("eligible"):
            report["selected"] = selected == "platt"
            report["reason"] = (
                "frozen_eligibility_rule_passed"
                if selected == "platt"
                else "frozen_eligibility_rule_not_met"
            )
        elif report["method"] == "identity":
            report["selected"] = selected == "identity"

    return selected, reports, aggregates[selected]


def _views_for_method(
    raw_folds: list[_RawFit],
    *,
    method: str,
    config: V4GateBConfig,
) -> list[tuple[_RawFit, _CalibrationView]]:
    return [
        (raw, _fit_calibration(raw, method=method, config=config))
        for raw in raw_folds
    ]


def _not_worse_on_both_regimes(
    challenger: Mapping[str, Any],
    baseline: Mapping[str, Any],
    *,
    config: V4GateBConfig,
) -> bool:
    for regime in config.known_regimes:
        challenger_slice = challenger["known_regime_validation"][regime]
        baseline_slice = baseline["known_regime_validation"][regime]
        if (
            challenger_slice["status"] != "evaluable"
            or baseline_slice["status"] != "evaluable"
        ):
            continue
        challenger_metrics = challenger_slice["metrics"]
        baseline_metrics = baseline_slice["metrics"]
        assert challenger_metrics is not None and baseline_metrics is not None
        if (
            float(challenger_metrics["log_loss"])
            > float(baseline_metrics["log_loss"])
            and float(challenger_metrics["brier_score"])
            > float(baseline_metrics["brier_score"])
        ):
            return False
    return True


def _fit_all_candidates(
    *,
    dataset: DatasetSnapshot,
    plan: Mapping[str, Any],
    config: V4GateBConfig,
) -> tuple[
    dict[tuple[str, int], dict[str, Any]],
    dict[tuple[str, int], list[_RawFit]],
]:
    raw_by_key: dict[tuple[str, int], list[_RawFit]] = {}
    summaries: dict[tuple[str, int], dict[str, Any]] = {}

    for candidate in config.forecast_candidates:
        for offset in config.offset_candidates_seconds:
            raw_folds: list[_RawFit] = []
            for fold in plan["folds"]:
                train_rows = _rows_at_offset(
                    _rows_for_ids(dataset, _partition_ids(fold["train"])),
                    offset,
                )
                validation_rows = _rows_at_offset(
                    _rows_for_ids(dataset, _partition_ids(fold["validation"])),
                    offset,
                )
                test_rows = _rows_at_offset(
                    _rows_for_ids(dataset, _partition_ids(fold["test"])),
                    offset,
                )
                raw_folds.append(
                    _fit_raw_candidate(
                        dataset_sha256=dataset.dataset_sha256,
                        train_rows=train_rows,
                        validation_rows=validation_rows,
                        test_rows=test_rows,
                        candidate=candidate,
                        offset_seconds=offset,
                        config=config,
                    )
                )

            calibration_method, calibration_report, aggregate = _calibration_selection(
                raw_folds,
                config=config,
            )
            summaries[(candidate, offset)] = {
                "candidate": candidate,
                "offset_seconds": offset,
                "calibration_method": calibration_method,
                "calibration_candidates": calibration_report,
                **aggregate,
                "eligible_for_selection": True,
                "eligibility_reason": "eligible",
            }
            raw_by_key[(candidate, offset)] = raw_folds

    for offset in config.offset_candidates_seconds:
        xgb = summaries.get(("full_v4_xgboost", offset))
        logistic = summaries.get(("full_v4_logistic", offset))
        if xgb is None or logistic is None:
            continue
        eligible = (
            float(xgb["mean_fold_log_loss"]) < float(logistic["mean_fold_log_loss"])
            and float(xgb["mean_fold_brier_score"])
            < float(logistic["mean_fold_brier_score"])
            and _not_worse_on_both_regimes(xgb, logistic, config=config)
        )
        xgb["eligible_for_selection"] = eligible
        xgb["eligibility_reason"] = (
            "nonlinear_replacement_rule_passed"
            if eligible
            else "nonlinear_replacement_rule_not_met"
        )

    return summaries, raw_by_key


def _select_forecast(
    summaries: Mapping[tuple[str, int], Mapping[str, Any]],
    *,
    config: V4GateBConfig,
) -> tuple[str, int, str]:
    eligible = [
        summary
        for summary in summaries.values()
        if bool(summary["eligible_for_selection"])
    ]
    if not eligible:
        raise V4PrepareIntegrityError("no V4 probability candidate is selectable")

    def worst_regime(summary: Mapping[str, Any]) -> float:
        values = []
        for regime in config.known_regimes:
            item = summary["known_regime_validation"][regime]
            if item["status"] != "evaluable" or item["metrics"] is None:
                continue
            values.append(float(item["metrics"]["log_loss"]))
        return max(values) if values else math.inf

    selected = min(
        eligible,
        key=lambda item: (
            float(item["mean_fold_log_loss"]),
            float(item["mean_fold_brier_score"]),
            worst_regime(item),
            _MODEL_COMPLEXITY[str(item["candidate"])],
            int(item["offset_seconds"]),
        ),
    )
    return (
        str(selected["candidate"]),
        int(selected["offset_seconds"]),
        str(selected["calibration_method"]),
    )


def _economics_slices(
    rows: tuple[SupervisedRow, ...],
    probabilities: tuple[float, ...],
    books: dict[str, Any],
    *,
    min_edge: float | None,
    config: V4GateBConfig,
) -> dict[str, Any]:
    definitions = {
        "overall": lambda row: True,
        "bull": lambda row: _regime(row) == "bull",
        "bear": lambda row: _regime(row) == "bear",
        "sideways_mixed": lambda row: _regime(row) == "sideways_mixed",
        "unknown": lambda row: _regime(row) == "unknown",
        "up": lambda row: row.target == 1,
        "down": lambda row: row.target == 0,
    }
    result: dict[str, Any] = {}
    for name, predicate in definitions.items():
        subset, subset_probabilities = _filter_pair(rows, probabilities, predicate)
        mapping = {
            row.condition_id: probability
            for row, probability in zip(
                subset,
                subset_probabilities,
                strict=True,
            )
        }
        subset_books = {
            row.condition_id: books[row.condition_id]
            for row in subset
            if row.condition_id in books
        }
        result[name] = {
            "market_count": len(subset),
            "status": (
                "evaluable"
                if name == "overall" or len(subset) >= config.min_reporting_slice_markets
                else "insufficient_slice_evidence"
            ),
            "metrics": (
                evaluate_economics_v4(
                    subset,
                    mapping,
                    subset_books,
                    fee_rate=config.fee_rate,
                    slippage_buffer=config.slippage_buffer,
                    min_edge=min_edge,
                )
                if subset
                else None
            ),
        }

    regime_by_side: dict[str, Any] = {}
    for regime in (*config.known_regimes, "unknown"):
        for target, side in ((1, "up"), (0, "down")):
            key = f"{regime}_{side}"
            subset, subset_probabilities = _filter_pair(
                rows,
                probabilities,
                lambda row, regime=regime, target=target: (
                    _regime(row) == regime and row.target == target
                ),
            )
            mapping = {
                row.condition_id: probability
                for row, probability in zip(
                    subset,
                    subset_probabilities,
                    strict=True,
                )
            }
            subset_books = {
                row.condition_id: books[row.condition_id]
                for row in subset
                if row.condition_id in books
            }
            regime_by_side[key] = {
                "market_count": len(subset),
                "status": (
                    "evaluable"
                    if len(subset) >= config.min_reporting_slice_markets
                    else "insufficient_slice_evidence"
                ),
                "metrics": (
                    evaluate_economics_v4(
                        subset,
                        mapping,
                        subset_books,
                        fee_rate=config.fee_rate,
                        slippage_buffer=config.slippage_buffer,
                        min_edge=min_edge,
                    )
                    if subset
                    else None
                ),
            }
    result["regime_by_side"] = regime_by_side
    return result


def _select_edge_policy(
    connection: Connection,
    views: list[tuple[_RawFit, _CalibrationView]],
    *,
    config: V4GateBConfig,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    books_by_fold = [
        _execution_books(
            connection,
            raw.validation_rows,
            max_age_seconds=config.max_selected_book_age_seconds,
        )
        for raw, _ in views
    ]

    for min_edge in config.min_edge_grid:
        folds: list[dict[str, Any]] = []
        for (raw, view), books in zip(views, books_by_fold, strict=True):
            probability_map = {
                row.condition_id: probability
                for row, probability in zip(
                    raw.validation_rows,
                    view.validation_probabilities,
                    strict=True,
                )
            }
            folds.append(
                evaluate_economics_v4(
                    raw.validation_rows,
                    probability_map,
                    books,
                    fee_rate=config.fee_rate,
                    slippage_buffer=config.slippage_buffer,
                    min_edge=min_edge,
                )
            )
        aggregate_pnl = sum(
            float(item["realized_pnl_after_assumed_costs"]) for item in folds
        )
        trade_counts_ok = all(
            int(item["trade_count"]) >= config.min_validation_trades_per_fold
            for item in folds
        )
        non_negative_count = sum(
            float(item["realized_pnl_after_assumed_costs"]) >= 0.0 for item in folds
        )
        consistency_ok = (
            non_negative_count >= config.required_non_negative_validation_folds
        )
        aggregate_ok = (
            not config.require_positive_aggregate_validation_pnl
            or aggregate_pnl > 0.0
        )
        item = {
            "policy": "trade_threshold",
            "min_edge": min_edge,
            "folds": folds,
            "aggregate_realized_pnl_after_assumed_costs": aggregate_pnl,
            "total_trades": sum(int(value["trade_count"]) for value in folds),
            "non_negative_validation_folds": non_negative_count,
            "minimum_trade_count_each_fold_met": trade_counts_ok,
            "validation_consistency_met": consistency_ok,
            "positive_aggregate_pnl_met": aggregate_ok,
            "eligible": trade_counts_ok and consistency_ok and aggregate_ok,
        }
        candidates.append(item)
        if item["eligible"]:
            eligible.append(item)

    candidates.append(
        {
            "policy": "no_trade",
            "min_edge": None,
            "eligible": True,
            "reason": "explicit_frozen_candidate",
            "aggregate_realized_pnl_after_assumed_costs": 0.0,
            "total_trades": 0,
        }
    )

    if not eligible:
        return {
            "policy": "no_trade",
            "min_edge": None,
            "reason": "no_threshold_passed_frozen_economic_consistency_rule",
            "candidates": candidates,
        }

    selected = max(
        eligible,
        key=lambda item: (
            float(item["aggregate_realized_pnl_after_assumed_costs"]),
            int(item["total_trades"]),
            float(item["min_edge"]),
        ),
    )
    return {
        "policy": "trade_threshold",
        "min_edge": selected["min_edge"],
        "reason": "validation_selected_under_frozen_global_rule",
        "validation_metrics": selected,
        "candidates": candidates,
    }


def _fit_selected_final_model(
    *,
    dataset: DatasetSnapshot,
    plan: Mapping[str, Any],
    candidate: str,
    offset: int,
    calibration_method: str,
    config: V4GateBConfig,
) -> tuple[_RawFit, _CalibrationView]:
    train_rows = _rows_at_offset(
        _rows_for_ids(dataset, _final_ids(plan, "train_condition_ids")),
        offset,
    )
    validation_rows = _rows_at_offset(
        _rows_for_ids(dataset, _final_ids(plan, "validation_condition_ids")),
        offset,
    )
    raw = _fit_raw_candidate(
        dataset_sha256=dataset.dataset_sha256,
        train_rows=train_rows,
        validation_rows=validation_rows,
        test_rows=validation_rows,
        candidate=candidate,
        offset_seconds=offset,
        config=config,
    )
    view = _fit_calibration(raw, method=calibration_method, config=config)
    return raw, view


def prepare_v4_gate_b(
    connection: Connection,
    *,
    plan: dict[str, Any],
    config: V4GateBConfig = FROZEN_V4_GATE_B_CONFIG,
) -> V4PreparedSelection:
    verify_v4_prepare_plan(plan, config)
    dataset = _load_non_holdout_dataset(connection, plan=plan, config=config)

    summaries, raw_by_key = _fit_all_candidates(
        dataset=dataset,
        plan=plan,
        config=config,
    )
    candidate, offset, calibration_method = _select_forecast(
        summaries,
        config=config,
    )
    selected_raw_folds = raw_by_key[(candidate, offset)]
    selected_views = _views_for_method(
        selected_raw_folds,
        method=calibration_method,
        config=config,
    )
    edge_selection = _select_edge_policy(
        connection,
        selected_views,
        config=config,
    )
    min_edge = (
        float(edge_selection["min_edge"])
        if edge_selection["policy"] == "trade_threshold"
        else None
    )

    fold_reports: list[dict[str, Any]] = []
    for fold, (raw, view) in zip(plan["folds"], selected_views, strict=True):
        validation_books = _execution_books(
            connection,
            raw.validation_rows,
            max_age_seconds=config.max_selected_book_age_seconds,
        )
        test_books = _execution_books(
            connection,
            raw.test_rows,
            max_age_seconds=config.max_selected_book_age_seconds,
        )
        validation_probability_map = {
            row.condition_id: probability
            for row, probability in zip(
                raw.validation_rows,
                view.validation_probabilities,
                strict=True,
            )
        }
        test_probability_map = {
            row.condition_id: probability
            for row, probability in zip(
                raw.test_rows,
                view.test_probabilities,
                strict=True,
            )
        }
        fold_reports.append(
            {
                "index": int(fold["index"]),
                "membership_sha256": fold["membership_sha256"],
                "train_condition_ids": list(_partition_ids(fold["train"])),
                "validation_condition_ids": list(_partition_ids(fold["validation"])),
                "test_condition_ids": list(_partition_ids(fold["test"])),
                "validation_forecast": {
                    "metrics": _metric_dict(
                        raw.validation_rows,
                        view.validation_probabilities,
                    ),
                    "slices": probability_slices(
                        raw.validation_rows,
                        view.validation_probabilities,
                        config=config,
                    ),
                },
                "validation_economics": {
                    "overall": evaluate_economics_v4(
                        raw.validation_rows,
                        validation_probability_map,
                        validation_books,
                        fee_rate=config.fee_rate,
                        slippage_buffer=config.slippage_buffer,
                        min_edge=min_edge,
                    ),
                    "slices": _economics_slices(
                        raw.validation_rows,
                        view.validation_probabilities,
                        validation_books,
                        min_edge=min_edge,
                        config=config,
                    ),
                },
                "ordinary_test": {
                    "forecast": {
                        "metrics": _metric_dict(
                            raw.test_rows,
                            view.test_probabilities,
                        ),
                        "slices": probability_slices(
                            raw.test_rows,
                            view.test_probabilities,
                            config=config,
                        ),
                    },
                    "economics": {
                        "overall": evaluate_economics_v4(
                            raw.test_rows,
                            test_probability_map,
                            test_books,
                            fee_rate=config.fee_rate,
                            slippage_buffer=config.slippage_buffer,
                            min_edge=min_edge,
                        ),
                        "slices": _economics_slices(
                            raw.test_rows,
                            view.test_probabilities,
                            test_books,
                            min_edge=min_edge,
                            config=config,
                        ),
                        "edge_bands": edge_band_report_v3(
                            raw.test_rows,
                            test_probability_map,
                            test_books,
                            fee_rate=config.fee_rate,
                            slippage_buffer=config.slippage_buffer,
                            boundaries=config.min_edge_grid,
                        ),
                    },
                },
            }
        )

    final_raw, final_view = _fit_selected_final_model(
        dataset=dataset,
        plan=plan,
        candidate=candidate,
        offset=offset,
        calibration_method=calibration_method,
        config=config,
    )
    final_books = _execution_books(
        connection,
        final_raw.validation_rows,
        max_age_seconds=config.max_selected_book_age_seconds,
    )
    final_probability_map = {
        row.condition_id: probability
        for row, probability in zip(
            final_raw.validation_rows,
            final_view.validation_probabilities,
            strict=True,
        )
    }

    model_bundle = dict(final_raw.model_bundle)
    model_bundle.update(
        {
            "research_plan_version": config.research_plan_version,
            "plan_sha256": plan["plan_sha256"],
            "dataset_sha256_non_holdout": dataset.dataset_sha256,
            "calibration_fit": asdict(final_view.fit),
            "calibration_method": calibration_method,
            "selected_min_edge": min_edge,
            "edge_policy": edge_selection["policy"],
            "fee_rate": config.fee_rate,
            "slippage_buffer": config.slippage_buffer,
            "max_selected_book_age_seconds": config.max_selected_book_age_seconds,
        }
    )

    candidate_reports = [
        summaries[key]
        for key in sorted(
            summaries,
            key=lambda value: (
                config.forecast_candidates.index(value[0]),
                value[1],
            ),
        )
    ]

    payload: dict[str, Any] = {
        "research_plan_version": config.research_plan_version,
        "stage": "ordinary_selection_frozen",
        "dataset_version": config.dataset_version,
        "feature_version": config.feature_version,
        "label_version": config.label_version,
        "authorized_plan_sha256": AUTHORIZED_V4_GATE_B_V2_PLAN_SHA256,
        "plan_sha256": plan["plan_sha256"],
        "readiness_input_sha256": plan["readiness_input_sha256"],
        "feature_manifest_sha256": plan["feature_manifest_sha256"],
        "dataset_sha256_non_holdout": dataset.dataset_sha256,
        "config": _json_safe_config(config),
        "forecast_candidates": candidate_reports,
        "selected_forecast": {
            "candidate": candidate,
            "offset_seconds": offset,
            "calibration_method": calibration_method,
            "aggregate_validation": summaries[(candidate, offset)],
        },
        "edge_selection": edge_selection,
        "folds": fold_reports,
        "final": {
            "membership_sha256": plan["final"]["membership_sha256"],
            "train_condition_ids": list(_final_ids(plan, "train_condition_ids")),
            "validation_condition_ids": list(
                _final_ids(plan, "validation_condition_ids")
            ),
            "holdout_condition_ids": list(_final_ids(plan, "holdout_condition_ids")),
            "selected_forecast": {
                "candidate": candidate,
                "offset_seconds": offset,
                "calibration_method": calibration_method,
                "calibration_fit": asdict(final_view.fit),
                "model_config": dict(final_raw.model_bundle["config"]),
            },
            "frozen_edge_policy": {
                "policy": edge_selection["policy"],
                "min_edge": min_edge,
            },
            "validation_forecast": {
                "metrics": _metric_dict(
                    final_raw.validation_rows,
                    final_view.validation_probabilities,
                ),
                "slices": probability_slices(
                    final_raw.validation_rows,
                    final_view.validation_probabilities,
                    config=config,
                ),
            },
            "validation_economics": {
                "overall": evaluate_economics_v4(
                    final_raw.validation_rows,
                    final_probability_map,
                    final_books,
                    fee_rate=config.fee_rate,
                    slippage_buffer=config.slippage_buffer,
                    min_edge=min_edge,
                ),
                "slices": _economics_slices(
                    final_raw.validation_rows,
                    final_view.validation_probabilities,
                    final_books,
                    min_edge=min_edge,
                    config=config,
                ),
            },
        },
        "labels_read_non_holdout": True,
        "holdout_labels_read": False,
        "holdout_evaluated": False,
        "training_performed": True,
        "policy_selected": True,
        "automatic_promotion": False,
        "activation_performed": False,
    }
    return V4PreparedSelection(payload=payload, model_bundle=model_bundle)


def finalize_v4_selection(
    prepared: V4PreparedSelection,
    *,
    model_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    payload = dict(prepared.payload)
    payload["model_artifact"] = dict(model_artifact)
    payload["selection_sha256"] = canonical_hash(payload)
    return payload
