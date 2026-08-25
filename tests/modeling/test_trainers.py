from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from bp_engine.modeling.models import DatasetSplit, MarketPartition, SupervisedRow
from bp_engine.modeling.trainers import prepare_matrices, train_logistic, train_xgboost


def _row(index: int, target: int, x: float | None, all_missing: float | None) -> SupervisedRow:
    start = datetime(2026, 8, 24, tzinfo=UTC) + timedelta(minutes=5 * index)
    return SupervisedRow(
        condition_id=f"condition-{index}",
        slug=f"btc-updown-5m-{index}",
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=start + timedelta(minutes=5),
        feature_at=start + timedelta(minutes=1),
        feature_offset_seconds=60,
        predictors={"all_missing": all_missing, "x": x},
        target=target,
        feature_hash=f"{index % 10}" * 64,
        input_fingerprint=f"{(index + 1) % 10}" * 64,
    )


def _partition(name: str, rows: tuple[SupervisedRow, ...]) -> MarketPartition:
    return MarketPartition(
        name=name,
        condition_ids=tuple(row.condition_id for row in rows),
        rows=rows,
    )


def _split() -> DatasetSplit:
    train = (
        _row(0, 0, 1.0, None),
        _row(1, 1, 3.0, None),
        _row(2, 0, 1.0, None),
        _row(3, 1, 3.0, None),
    )
    validation = (_row(4, 0, None, 9.0), _row(5, 1, 999.0, 9.0))
    test = (_row(6, 0, None, 10.0), _row(7, 1, 1000.0, 10.0))
    return DatasetSplit(
        split_version="chronological-market-v1",
        dataset_sha256="d" * 64,
        train=_partition("train", train),
        validation=_partition("validation", validation),
        test=_partition("test", test),
        embargo_condition_ids=(),
        split_sha256="s" * 64,
    )


def test_preprocessing_uses_training_data_only_and_drops_train_all_missing_columns() -> None:
    prepared = prepare_matrices(_split())

    assert prepared.predictor_names == ("x",)
    assert prepared.dropped_all_missing == ("all_missing",)
    assert prepared.x_validation[0, 0] == pytest.approx(2.0)
    assert prepared.x_test[0, 0] == pytest.approx(2.0)
    assert np.isfinite(prepared.x_train_scaled).all()
    assert np.isfinite(prepared.x_validation_scaled).all()


def test_logistic_training_is_deterministic() -> None:
    split = _split()
    prepared = prepare_matrices(split)
    first = train_logistic(split, prepared)
    second = train_logistic(split, prepared)

    assert first.family == "logistic"
    assert first.validation_probabilities == pytest.approx(
        second.validation_probabilities, abs=1e-12
    )
    assert first.test_probabilities == pytest.approx(second.test_probabilities, abs=1e-12)


def test_xgboost_training_is_deterministic_with_fixed_configuration() -> None:
    split = _split()
    prepared = prepare_matrices(split)
    first = train_xgboost(split, prepared)
    second = train_xgboost(split, prepared)

    assert first.family == "xgboost"
    assert first.config["random_state"] == 20260825
    assert first.config["n_jobs"] == 1
    assert first.validation_probabilities == pytest.approx(
        second.validation_probabilities, abs=1e-12
    )
    assert first.test_probabilities == pytest.approx(second.test_probabilities, abs=1e-12)
