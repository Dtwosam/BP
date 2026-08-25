from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from bp_engine.backtesting.selection import rows_at_offset, select_validation_offset

from bp_engine.modeling.models import SupervisedRow


def _row(
    condition_id: str,
    *,
    offset_seconds: int,
    target: int,
    price: float | None,
) -> SupervisedRow:
    start = datetime(2026, 8, 24, tzinfo=UTC)
    return SupervisedRow(
        condition_id=condition_id,
        slug=f"market-{condition_id}",
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=start + timedelta(seconds=300),
        feature_at=start + timedelta(seconds=offset_seconds),
        feature_offset_seconds=offset_seconds,
        predictors={"pm_up_price": price},
        target=target,
        feature_hash="a" * 64,
        input_fingerprint="b" * 64,
    )


def _training_rows() -> tuple[SupervisedRow, ...]:
    return (
        _row("train-up", offset_seconds=60, target=1, price=0.6),
        _row("train-up", offset_seconds=120, target=1, price=0.7),
        _row("train-down", offset_seconds=60, target=0, price=0.4),
        _row("train-down", offset_seconds=120, target=0, price=0.3),
    )


def _validation_rows(
    *,
    offsets: dict[int, tuple[float | None, ...]],
    targets: tuple[int, ...],
) -> tuple[SupervisedRow, ...]:
    rows: list[SupervisedRow] = []
    for offset_seconds, prices in offsets.items():
        assert len(prices) == len(targets)
        for index, (target, price) in enumerate(zip(targets, prices, strict=True)):
            rows.append(
                _row(
                    f"validation-{index}",
                    offset_seconds=offset_seconds,
                    target=target,
                    price=price,
                )
            )
    return tuple(rows)


def test_rows_at_offset_rejects_duplicate_condition_rows() -> None:
    row = _row("duplicate", offset_seconds=60, target=1, price=0.6)
    duplicate = replace(row, feature_hash="c" * 64)

    with pytest.raises(ValueError, match="duplicate condition"):
        rows_at_offset((row, duplicate), 60)


def test_offset_below_observed_market_price_coverage_is_excluded() -> None:
    targets = (1, 0, 1, 0, 1)
    validation = _validation_rows(
        targets=targets,
        offsets={
            60: (0.8, 0.2, 0.7, 0.3, 0.6),
            120: (0.99, None, 0.99, None, 0.99),
            180: (0.7, 0.3, 0.6, 0.4, 0.55),
        },
    )

    selection = select_validation_offset(
        _training_rows(),
        validation,
        min_market_price_coverage=0.80,
        min_validation_markets=5,
    )

    candidate_offsets = tuple(candidate.offset_seconds for candidate in selection.candidates)
    assert 120 not in candidate_offsets
    assert candidate_offsets == (60, 180)
    assert selection.selected_offset_seconds == 60


def test_lower_log_loss_wins_even_with_lower_accuracy() -> None:
    targets = (1, 1, 0, 0)
    validation = _validation_rows(
        targets=targets,
        offsets={
            60: (0.99, 0.99, 0.01, 0.51),
            120: (0.51, 0.51, 0.49, 0.49),
        },
    )

    selection = select_validation_offset(
        _training_rows(),
        validation,
        min_market_price_coverage=0.80,
        min_validation_markets=4,
    )
    candidates = {candidate.offset_seconds: candidate for candidate in selection.candidates}

    assert candidates[60].metrics.accuracy < candidates[120].metrics.accuracy
    assert candidates[60].metrics.log_loss < candidates[120].metrics.log_loss
    assert selection.selected_offset_seconds == 60


def test_exact_metric_tie_uses_smaller_offset() -> None:
    targets = (1, 0, 1, 0)
    prices = (0.7, 0.3, 0.6, 0.4)
    validation = _validation_rows(
        targets=targets,
        offsets={120: prices, 60: prices},
    )

    selection = select_validation_offset(
        _training_rows(),
        validation,
        min_market_price_coverage=0.80,
        min_validation_markets=4,
    )

    assert selection.selected_offset_seconds == 60


def test_test_or_holdout_perturbation_cannot_change_validation_selection() -> None:
    targets = (1, 0, 1, 0)
    validation = _validation_rows(
        targets=targets,
        offsets={
            60: (0.8, 0.2, 0.7, 0.3),
            120: (0.55, 0.45, 0.55, 0.45),
        },
    )
    unrelated_test = _validation_rows(
        targets=targets,
        offsets={60: (0.9, 0.1, 0.9, 0.1)},
    )

    first = select_validation_offset(
        _training_rows(),
        validation,
        min_market_price_coverage=0.80,
        min_validation_markets=4,
    )
    _mutated_test = tuple(
        replace(
            row,
            target=1 - row.target,
            predictors={"pm_up_price": 1.0 - float(row.predictors["pm_up_price"] or 0.5)},
        )
        for row in unrelated_test
    )
    second = select_validation_offset(
        _training_rows(),
        validation,
        min_market_price_coverage=0.80,
        min_validation_markets=4,
    )

    assert first == second
