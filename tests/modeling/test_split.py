from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bp_engine.modeling.models import DATASET_VERSION, DatasetSnapshot, SupervisedRow
from bp_engine.modeling.split import (
    SplitIntegrityError,
    chronological_market_split,
    equal_market_weights,
)


def _row(market_index: int, row_index: int, target: int) -> SupervisedRow:
    start = datetime(2026, 8, 24, tzinfo=UTC) + timedelta(minutes=5 * market_index)
    return SupervisedRow(
        condition_id=f"condition-{market_index:03d}",
        slug=f"btc-updown-5m-{market_index}",
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=start + timedelta(minutes=5),
        feature_at=start + timedelta(minutes=row_index + 1),
        feature_offset_seconds=(row_index + 1) * 60,
        predictors={"x": float(market_index + row_index)},
        target=target,
        feature_hash=f"{market_index % 10}" * 64,
        input_fingerprint=f"{(market_index + 1) % 10}" * 64,
    )


def _dataset(markets: int = 20, rows_per_market: int = 2) -> DatasetSnapshot:
    rows = tuple(
        _row(market, row, market % 2)
        for market in range(markets)
        for row in range(rows_per_market)
    )
    return DatasetSnapshot(
        dataset_version=DATASET_VERSION,
        feature_version="core-v1",
        label_version="official-outcome-v1",
        horizon_seconds=300,
        start=datetime(2026, 8, 24, tzinfo=UTC),
        end=datetime(2026, 8, 25, tzinfo=UTC),
        rows=rows,
        predictor_names=("x",),
        dataset_sha256="d" * 64,
    )


def test_chronological_split_keeps_each_market_in_exactly_one_partition() -> None:
    split = chronological_market_split(_dataset(), embargo_markets=1)

    train = set(split.train.condition_ids)
    validation = set(split.validation.condition_ids)
    test = set(split.test.condition_ids)
    embargo = set(split.embargo_condition_ids)
    assert train.isdisjoint(validation | test | embargo)
    assert validation.isdisjoint(test | embargo)
    assert test.isdisjoint(embargo)
    assert len(train | validation | test | embargo) == 20
    assert max(row.market_start_at for row in split.train.rows) < min(
        row.market_start_at for row in split.validation.rows
    )
    assert max(row.market_start_at for row in split.validation.rows) < min(
        row.market_start_at for row in split.test.rows
    )


def test_equal_market_weights_give_each_condition_equal_total_weight() -> None:
    rows = (_row(0, 0, 0), _row(0, 1, 0)) + tuple(_row(1, index, 1) for index in range(4))
    weights = equal_market_weights(rows)
    totals: dict[str, float] = {}
    for row, weight in zip(rows, weights, strict=True):
        totals[row.condition_id] = totals.get(row.condition_id, 0.0) + weight

    assert totals == pytest.approx({"condition-000": 1.0, "condition-001": 1.0})


def test_split_rejects_insufficient_markets() -> None:
    with pytest.raises(SplitIntegrityError, match="market"):
        chronological_market_split(_dataset(markets=5), min_markets=6)


def test_split_rejects_single_class_training_partition() -> None:
    dataset = _dataset(markets=10)
    rows = tuple(
        SupervisedRow(**{**row.__dict__, "target": 0})
        if row.market_start_at < datetime(2026, 8, 24, 0, 30, tzinfo=UTC)
        else row
        for row in dataset.rows
    )
    dataset = DatasetSnapshot(**{**dataset.__dict__, "rows": rows})
    with pytest.raises(SplitIntegrityError, match="class"):
        chronological_market_split(dataset, embargo_markets=0)
