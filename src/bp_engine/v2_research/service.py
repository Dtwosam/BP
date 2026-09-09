from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from sqlalchemy import Connection

from bp_engine.calibration.calibrators import apply_calibration, select_calibrator
from bp_engine.calibration.models import CalibrationFit
from bp_engine.features.hashing import canonical_hash
from bp_engine.modeling.dataset import load_dataset
from bp_engine.modeling.metrics import evaluate_probabilities
from bp_engine.modeling.models import DatasetSnapshot, MetricSummary, SupervisedRow
from bp_engine.modeling.split import equal_market_weights
from bp_engine.v2_research.config import (
    EXPECTED_OFFSETS_SECONDS,
    FROZEN_COVERAGE_INPUT_SHA256,
    FROZEN_FRESHNESS_CANDIDATES_SECONDS,
    V2_DATASET_VERSION,
    V2_FEATURE_VERSION,
    V2_GATE_B_VERSION,
    V2_LABEL_VERSION,
)
from bp_engine.v2_research.models import GateBResearchConfig
from bp_engine.v2_research.policy import (
    eligible_probability,
    evaluate_edge_policy_v2,
)


class GateBResearchIntegrityError(RuntimeError):
    """Raised when Gate B research would violate a frozen selection boundary."""


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GateBResearchIntegrityError("plan datetime must be timezone-aware")
    return parsed


def _verify_hash(payload: dict[str, Any], field: str) -> None:
    expected = payload.get(field)
    if not isinstance(expected, str) or len(expected) != 64:
        raise GateBResearchIntegrityError(f"{field} must be SHA-256")
    body = dict(payload)
    body.pop(field, None)
    if canonical_hash(body) != expected:
        raise GateBResearchIntegrityError(f"{field} mismatch")


def _verify_plan(plan: dict[str, Any]) -> None:
    _verify_hash(plan, "plan_sha256")
    if plan.get("gate_b_version") != V2_GATE_B_VERSION:
        raise GateBResearchIntegrityError("unexpected Gate B version")
    if plan.get("feature_version") != V2_FEATURE_VERSION:
        raise GateBResearchIntegrityError("unexpected V2 feature version")
    if plan.get("coverage_input_sha256") != FROZEN_COVERAGE_INPUT_SHA256:
        raise GateBResearchIntegrityError("coverage preregistration hash mismatch")
    if tuple(plan.get("freshness_candidates_seconds", ())) != (
        FROZEN_FRESHNESS_CANDIDATES_SECONDS
    ):
        raise GateBResearchIntegrityError("frozen freshness candidates changed")
    if plan.get("include_no_trade") is not True:
        raise GateBResearchIntegrityError("no_trade must remain included")
    if plan.get("labels_read") is not False:
        raise GateBResearchIntegrityError("plan must remain feature-only")


def _condition_ids(partition: dict[str, Any]) -> tuple[str, ...]:
    raw = partition.get("condition_ids")
    if not isinstance(raw, list) or not raw:
        raise GateBResearchIntegrityError("partition condition_ids must be non-empty")
    values = tuple(str(value) for value in raw)
    if len(values) != len(set(values)):
        raise GateBResearchIntegrityError("partition contains duplicate condition ids")
    return values


def _final_ids(plan: dict[str, Any], key: str) -> tuple[str, ...]:
    raw = plan["final"].get(key)
    if not isinstance(raw, list) or not raw:
        raise GateBResearchIntegrityError(f"final {key} must be non-empty")
    values = tuple(str(value) for value in raw)
    if len(values) != len(set(values)):
        raise GateBResearchIntegrityError(f"final {key} contains duplicates")
    return values


def _non_holdout_ids(plan: dict[str, Any]) -> tuple[str, ...]:
    holdout = set(_final_ids(plan, "holdout_condition_ids"))
    values: set[str] = set()
    for fold in plan["folds"]:
        for name in ("train", "validation", "test"):
            values.update(_condition_ids(fold[name]))
    values.update(_final_ids(plan, "train_condition_ids"))
    values.update(_final_ids(plan, "validation_condition_ids"))
    if values.intersection(holdout):
        raise GateBResearchIntegrityError("prepare partitions overlap final holdout")
    return tuple(sorted(values))


def _load_scoped_dataset(
    connection: Connection,
    *,
    plan: dict[str, Any],
    condition_ids: tuple[str, ...],
) -> DatasetSnapshot:
    dataset = load_dataset(
        connection,
        start=_parse_datetime(plan["market_start_at"]),
        end=_parse_datetime(plan["market_end_at"]),
        horizon_seconds=300,
        feature_version=V2_FEATURE_VERSION,
        label_version=V2_LABEL_VERSION,
        condition_ids=condition_ids,
        dataset_version=V2_DATASET_VERSION,
    )
    if dataset.dataset_version != V2_DATASET_VERSION:
        raise GateBResearchIntegrityError("unexpected V2 dataset identity")
    observed_ids = {row.condition_id for row in dataset.rows}
    missing = set(condition_ids).difference(observed_ids)
    if missing:
        raise GateBResearchIntegrityError(
            f"missing canonical labels/features for {sorted(missing)[0]}"
        )
    unexpected = observed_ids.difference(condition_ids)
    if unexpected:
        raise GateBResearchIntegrityError(
            f"dataset contains unrequested market {sorted(unexpected)[0]}"
        )
    by_condition: dict[str, set[int]] = {}
    for row in dataset.rows:
        by_condition.setdefault(row.condition_id, set()).add(row.feature_offset_seconds)
    for condition_id in condition_ids:
        if tuple(sorted(by_condition[condition_id])) != EXPECTED_OFFSETS_SECONDS:
            raise GateBResearchIntegrityError(
                f"{condition_id} does not have the frozen four V2 offsets"
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
    seen: set[str] = set()
    for row in selected:
        if row.condition_id in seen:
            raise GateBResearchIntegrityError(
                f"duplicate condition at offset {offset_seconds}: {row.condition_id}"
            )
        seen.add(row.condition_id)
    return selected


def _require_both_classes(rows: tuple[SupervisedRow, ...], name: str) -> None:
    if {row.target for row in rows} != {0, 1}:
        raise GateBResearchIntegrityError(f"{name} must contain both target classes")


def _eligible(
    rows: tuple[SupervisedRow, ...], max_age_seconds: int
) -> tuple[tuple[SupervisedRow, ...], tuple[float, ...]]:
    selected_rows: list[SupervisedRow] = []
    probabilities: list[float] = []
    for row in rows:
        probability = eligible_probability(row, max_age_seconds)
        if probability is None:
            continue
        selected_rows.append(row)
        probabilities.append(probability)
    return tuple(selected_rows), tuple(probabilities)


def _metric_dict(
    rows: tuple[SupervisedRow, ...], probabilities: tuple[float, ...]
) -> dict[str, Any] | None:
    if not rows:
        return None
    return asdict(
        evaluate_probabilities(rows, probabilities, equal_market_weights(rows))
    )


def _edge_grid_selection(
    validation_rows: tuple[SupervisedRow, ...],
    calibrated_by_condition: dict[str, float],
    *,
    max_age_seconds: int,
    config: GateBResearchConfig,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    for min_edge in config.min_edge_grid:
        metrics = evaluate_edge_policy_v2(
            validation_rows,
            calibrated_by_condition,
            max_last_trade_age_seconds=max_age_seconds,
            fee_rate=config.fee_rate,
            slippage_buffer=config.slippage_buffer,
            min_edge=min_edge,
        )
        ok = (
            metrics.trade_count >= config.min_validation_trades
            and metrics.realized_pnl_after_assumed_costs > 0
            and metrics.mean_realized_pnl_after_assumed_costs is not None
            and metrics.mean_realized_pnl_after_assumed_costs > 0
        )
        item = {
            "min_edge": min_edge,
            "eligible": ok,
            "metrics": asdict(metrics),
        }
        candidates.append(item)
        if ok:
            eligible.append(item)
    if not eligible:
        no_trade = evaluate_edge_policy_v2(
            validation_rows,
            calibrated_by_condition,
            max_last_trade_age_seconds=max_age_seconds,
            fee_rate=config.fee_rate,
            slippage_buffer=config.slippage_buffer,
            min_edge=None,
        )
        return {
            "policy": "no_trade",
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
        "min_edge": selected["min_edge"],
        "validation_metrics": selected["metrics"],
        "candidates": candidates,
    }


def _candidate(
    train_rows: tuple[SupervisedRow, ...],
    validation_rows: tuple[SupervisedRow, ...],
    *,
    offset_seconds: int,
    max_age_seconds: int,
    config: GateBResearchConfig,
) -> dict[str, Any] | None:
    train_offset = _rows_at_offset(train_rows, offset_seconds)
    validation_offset = _rows_at_offset(validation_rows, offset_seconds)
    train_eligible, train_raw = _eligible(train_offset, max_age_seconds)
    validation_eligible, validation_raw = _eligible(
        validation_offset, max_age_seconds
    )
    if len(train_eligible) < config.min_train_eligible_markets:
        return None
    if len(validation_eligible) < config.min_validation_eligible_markets:
        return None
    if {row.target for row in train_eligible} != {0, 1}:
        return None
    if {row.target for row in validation_eligible} != {0, 1}:
        return None

    calibration = select_calibrator(
        train_eligible,
        train_raw,
        validation_eligible,
        validation_raw,
    )
    calibrated_validation = apply_calibration(calibration.fit, validation_raw)
    calibrated_by_condition = {
        row.condition_id: probability
        for row, probability in zip(
            validation_eligible, calibrated_validation, strict=True
        )
    }
    edge = _edge_grid_selection(
        validation_offset,
        calibrated_by_condition,
        max_age_seconds=max_age_seconds,
        config=config,
    )
    return {
        "offset_seconds": offset_seconds,
        "max_last_trade_age_seconds": max_age_seconds,
        "train_market_count": len(train_offset),
        "validation_market_count": len(validation_offset),
        "train_eligible_market_count": len(train_eligible),
        "validation_eligible_market_count": len(validation_eligible),
        "calibration": {
            "method": calibration.method,
            "fit": asdict(calibration.fit),
            "validation_metrics": asdict(calibration.validation_metrics),
            "candidates": [asdict(value) for value in calibration.candidates],
        },
        "raw_validation_metrics": _metric_dict(
            validation_eligible, validation_raw
        ),
        "calibrated_validation_metrics": _metric_dict(
            validation_eligible, calibrated_validation
        ),
        "edge": edge,
    }


def _select_validation_policy(
    train_rows: tuple[SupervisedRow, ...],
    validation_rows: tuple[SupervisedRow, ...],
    config: GateBResearchConfig,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    trade_candidates: list[dict[str, Any]] = []
    for offset_seconds in EXPECTED_OFFSETS_SECONDS:
        for max_age_seconds in FROZEN_FRESHNESS_CANDIDATES_SECONDS:
            item = _candidate(
                train_rows,
                validation_rows,
                offset_seconds=offset_seconds,
                max_age_seconds=max_age_seconds,
                config=config,
            )
            if item is None:
                continue
            candidates.append(item)
            if item["edge"]["policy"] == "trade_threshold":
                trade_candidates.append(item)
    if not candidates:
        return {
            "policy": "no_trade",
            "reason": "no_validation_candidate_satisfies_eligibility",
            "selected": None,
            "candidates": [],
        }
    if not trade_candidates:
        return {
            "policy": "no_trade",
            "reason": "no_validation_edge_candidate_profitable",
            "selected": None,
            "candidates": candidates,
        }
    selected = max(
        trade_candidates,
        key=lambda item: (
            item["edge"]["validation_metrics"]["realized_pnl_after_assumed_costs"],
            item["edge"]["validation_metrics"][
                "mean_realized_pnl_after_assumed_costs"
            ],
            item["edge"]["validation_metrics"]["trade_count"],
            -item["max_last_trade_age_seconds"],
            -item["offset_seconds"],
            item["edge"]["min_edge"],
        ),
    )
    return {
        "policy": "trade_threshold",
        "reason": "validation_selected",
        "selected": selected,
        "candidates": candidates,
    }


def _calibrated_map(
    rows: tuple[SupervisedRow, ...],
    *,
    max_age_seconds: int,
    calibration_fit: CalibrationFit,
) -> tuple[
    tuple[SupervisedRow, ...],
    tuple[float, ...],
    tuple[float, ...],
    dict[str, float],
]:
    eligible_rows, raw = _eligible(rows, max_age_seconds)
    calibrated = apply_calibration(calibration_fit, raw) if raw else ()
    mapping = {
        row.condition_id: probability
        for row, probability in zip(eligible_rows, calibrated, strict=True)
    }
    return eligible_rows, raw, calibrated, mapping


def _evaluate_frozen_policy(
    rows: tuple[SupervisedRow, ...],
    selection: dict[str, Any],
    config: GateBResearchConfig,
) -> dict[str, Any]:
    if selection["policy"] == "no_trade":
        return {
            "policy": "no_trade",
            "raw_metrics": None,
            "calibrated_metrics": None,
            "edge_metrics": {
                "prediction_markets": len(rows),
                "trade_count": 0,
                "trade_coverage": 0.0,
                "reason": "policy_no_trade",
            },
        }
    selected = selection["selected"]
    offset = int(selected["offset_seconds"])
    max_age = int(selected["max_last_trade_age_seconds"])
    offset_rows = _rows_at_offset(rows, offset)
    fit_payload = selected["calibration"]["fit"]
    fit = CalibrationFit(
        method=str(fit_payload["method"]),
        intercept=fit_payload["intercept"],
        coefficient=fit_payload["coefficient"],
    )
    eligible_rows, raw, calibrated, mapping = _calibrated_map(
        offset_rows,
        max_age_seconds=max_age,
        calibration_fit=fit,
    )
    edge = evaluate_edge_policy_v2(
        offset_rows,
        mapping,
        max_last_trade_age_seconds=max_age,
        fee_rate=config.fee_rate,
        slippage_buffer=config.slippage_buffer,
        min_edge=selected["edge"]["min_edge"],
    )
    return {
        "policy": "trade_threshold",
        "offset_seconds": offset,
        "max_last_trade_age_seconds": max_age,
        "min_edge": selected["edge"]["min_edge"],
        "calibration_fit": fit_payload,
        "eligible_market_count": len(eligible_rows),
        "raw_metrics": _metric_dict(eligible_rows, raw),
        "calibrated_metrics": _metric_dict(eligible_rows, calibrated),
        "edge_metrics": asdict(edge),
    }


def _partition_classes(
    dataset: DatasetSnapshot, condition_ids: tuple[str, ...], name: str
) -> None:
    rows = _rows_for_ids(dataset, condition_ids)
    one_per_market: dict[str, SupervisedRow] = {}
    for row in rows:
        one_per_market.setdefault(row.condition_id, row)
    _require_both_classes(tuple(one_per_market.values()), name)


def prepare_gate_b(
    connection: Connection,
    *,
    plan: dict[str, Any],
    config: GateBResearchConfig | None = None,
) -> dict[str, Any]:
    _verify_plan(plan)
    config = config or GateBResearchConfig()
    holdout_ids = _final_ids(plan, "holdout_condition_ids")
    non_holdout_ids = _non_holdout_ids(plan)
    dataset = _load_scoped_dataset(
        connection, plan=plan, condition_ids=non_holdout_ids
    )
    if {row.condition_id for row in dataset.rows}.intersection(holdout_ids):
        raise GateBResearchIntegrityError("prepare loaded a final-holdout label")

    fold_reports: list[dict[str, Any]] = []
    seen_test: set[str] = set()
    for fold in plan["folds"]:
        train_ids = _condition_ids(fold["train"])
        validation_ids = _condition_ids(fold["validation"])
        test_ids = _condition_ids(fold["test"])
        if seen_test.intersection(test_ids):
            raise GateBResearchIntegrityError("ordinary test market reused")
        seen_test.update(test_ids)
        for ids, name in (
            (train_ids, "fold train"),
            (validation_ids, "fold validation"),
            (test_ids, "fold test"),
        ):
            _partition_classes(dataset, ids, name)
        train_rows = _rows_for_ids(dataset, train_ids)
        validation_rows = _rows_for_ids(dataset, validation_ids)
        test_rows = _rows_for_ids(dataset, test_ids)
        selection = _select_validation_policy(train_rows, validation_rows, config)
        fold_reports.append(
            {
                "index": int(fold["index"]),
                "membership_sha256": fold["membership_sha256"],
                "train_condition_ids": list(train_ids),
                "validation_condition_ids": list(validation_ids),
                "test_condition_ids": list(test_ids),
                "selection_partition": "validation",
                "selection": selection,
                "test_evaluation": _evaluate_frozen_policy(
                    test_rows, selection, config
                ),
            }
        )

    final_train_ids = _final_ids(plan, "train_condition_ids")
    final_validation_ids = _final_ids(plan, "validation_condition_ids")
    _partition_classes(dataset, final_train_ids, "final train")
    _partition_classes(dataset, final_validation_ids, "final validation")
    final_train_rows = _rows_for_ids(dataset, final_train_ids)
    final_validation_rows = _rows_for_ids(dataset, final_validation_ids)
    final_selection = _select_validation_policy(
        final_train_rows, final_validation_rows, config
    )

    config_payload = {
        "fee_rate": config.fee_rate,
        "slippage_buffer": config.slippage_buffer,
        "min_edge_grid": list(config.min_edge_grid),
        "min_validation_trades": config.min_validation_trades,
        "min_train_eligible_markets": config.min_train_eligible_markets,
        "min_validation_eligible_markets": config.min_validation_eligible_markets,
    }
    payload: dict[str, Any] = {
        "gate_b_version": V2_GATE_B_VERSION,
        "stage": "prepared_validation_frozen",
        "dataset_version": V2_DATASET_VERSION,
        "feature_version": V2_FEATURE_VERSION,
        "label_version": V2_LABEL_VERSION,
        "coverage_input_sha256": FROZEN_COVERAGE_INPUT_SHA256,
        "freshness_candidates_seconds": list(
            FROZEN_FRESHNESS_CANDIDATES_SECONDS
        ),
        "include_no_trade": True,
        "plan_sha256": plan["plan_sha256"],
        "dataset_sha256_non_holdout": dataset.dataset_sha256,
        "config": config_payload,
        "config_sha256": canonical_hash(config_payload),
        "folds": fold_reports,
        "final": {
            "membership_sha256": plan["final"]["membership_sha256"],
            "train_condition_ids": list(final_train_ids),
            "validation_condition_ids": list(final_validation_ids),
            "holdout_condition_ids": list(holdout_ids),
            "selection_partition": "validation",
            "selection": final_selection,
        },
        "holdout_labels_read": False,
        "holdout_evaluated": False,
        "gate_b_authorized": False,
        "automatic_promotion": False,
    }
    payload["selection_sha256"] = canonical_hash(payload)
    return payload


def _verify_selection(selection: dict[str, Any], plan: dict[str, Any]) -> None:
    _verify_hash(selection, "selection_sha256")
    if selection.get("stage") != "prepared_validation_frozen":
        raise GateBResearchIntegrityError("selection artifact is not prepared/frozen")
    if selection.get("plan_sha256") != plan.get("plan_sha256"):
        raise GateBResearchIntegrityError("selection plan hash mismatch")
    if selection.get("coverage_input_sha256") != FROZEN_COVERAGE_INPUT_SHA256:
        raise GateBResearchIntegrityError("selection coverage hash mismatch")
    if tuple(selection.get("freshness_candidates_seconds", ())) != (
        FROZEN_FRESHNESS_CANDIDATES_SECONDS
    ):
        raise GateBResearchIntegrityError("selection freshness grid changed")
    if selection.get("holdout_labels_read") is not False:
        raise GateBResearchIntegrityError("prepared selection already read holdout")
    if selection.get("holdout_evaluated") is not False:
        raise GateBResearchIntegrityError("prepared selection already evaluated holdout")


def evaluate_gate_b_holdout(
    connection: Connection,
    *,
    plan: dict[str, Any],
    selection: dict[str, Any],
) -> dict[str, Any]:
    _verify_plan(plan)
    _verify_selection(selection, plan)
    holdout_ids = _final_ids(plan, "holdout_condition_ids")
    if selection["final"]["holdout_condition_ids"] != list(holdout_ids):
        raise GateBResearchIntegrityError("selection holdout membership changed")
    dataset = _load_scoped_dataset(
        connection, plan=plan, condition_ids=holdout_ids
    )
    _partition_classes(dataset, holdout_ids, "final holdout")
    holdout_rows = _rows_for_ids(dataset, holdout_ids)

    cfg = selection["config"]
    config = GateBResearchConfig(
        fee_rate=float(cfg["fee_rate"]),
        slippage_buffer=float(cfg["slippage_buffer"]),
        min_edge_grid=tuple(float(value) for value in cfg["min_edge_grid"]),
        min_validation_trades=int(cfg["min_validation_trades"]),
        min_train_eligible_markets=int(cfg["min_train_eligible_markets"]),
        min_validation_eligible_markets=int(
            cfg["min_validation_eligible_markets"]
        ),
    )
    evaluation = _evaluate_frozen_policy(
        holdout_rows, selection["final"]["selection"], config
    )
    payload: dict[str, Any] = {
        "gate_b_version": V2_GATE_B_VERSION,
        "stage": "final_holdout_evaluated",
        "plan_sha256": plan["plan_sha256"],
        "selection_sha256": selection["selection_sha256"],
        "coverage_input_sha256": FROZEN_COVERAGE_INPUT_SHA256,
        "dataset_version": V2_DATASET_VERSION,
        "feature_version": V2_FEATURE_VERSION,
        "label_version": V2_LABEL_VERSION,
        "holdout_condition_ids": list(holdout_ids),
        "holdout_dataset_sha256": dataset.dataset_sha256,
        "frozen_selection": selection["final"]["selection"],
        "holdout_evaluation": evaluation,
        "holdout_labels_read": True,
        "holdout_evaluated_once": True,
        "gate_b_evidence_complete": True,
        "gate_b_authorized": False,
        "automatic_promotion": False,
    }
    payload["holdout_evidence_sha256"] = canonical_hash(payload)
    return payload
