from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest
from bp_engine.modeling.models import (
    DATASET_VERSION,
    SPLIT_VERSION,
    DatasetSnapshot,
    SupervisedRow,
)


def _row() -> SupervisedRow:
    return SupervisedRow(
        condition_id="condition-1",
        slug="btc-updown-5m-1724457600",
        horizon_seconds=300,
        market_start_at=datetime(2026, 8, 24, 0, 0, tzinfo=UTC),
        market_end_at=datetime(2026, 8, 24, 0, 5, tzinfo=UTC),
        feature_at=datetime(2026, 8, 24, 0, 1, tzinfo=UTC),
        feature_offset_seconds=60,
        predictors={"pm_up_price": 0.6, "missing__pm_up_book_missing": 1.0},
        target=1,
        feature_hash="f" * 64,
        input_fingerprint="i" * 64,
    )


def test_phase7_versions_are_fixed() -> None:
    assert DATASET_VERSION == "supervised-core-v1"
    assert SPLIT_VERSION == "chronological-market-v1"


def test_supervised_row_is_frozen() -> None:
    row = _row()

    with pytest.raises(FrozenInstanceError):
        row.target = 0  # type: ignore[misc]


def test_dataset_snapshot_is_frozen_and_keeps_predictor_schema() -> None:
    row = _row()
    snapshot = DatasetSnapshot(
        dataset_version=DATASET_VERSION,
        feature_version="core-v1",
        label_version="official-outcome-v1",
        horizon_seconds=300,
        start=datetime(2026, 8, 24, tzinfo=UTC),
        end=datetime(2026, 8, 25, tzinfo=UTC),
        rows=(row,),
        predictor_names=("missing__pm_up_book_missing", "pm_up_price"),
        dataset_sha256="d" * 64,
    )

    assert snapshot.rows == (row,)
    assert snapshot.predictor_names == (
        "missing__pm_up_book_missing",
        "pm_up_price",
    )
    with pytest.raises(FrozenInstanceError):
        snapshot.dataset_sha256 = "x" * 64  # type: ignore[misc]
