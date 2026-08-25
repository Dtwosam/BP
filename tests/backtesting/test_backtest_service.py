from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bp_engine.backtesting.models import WalkForwardConfig
from bp_engine.backtesting.predictor import ModelSpec
from bp_engine.backtesting.service import BacktestIntegrityError, run_walk_forward_backtest
from bp_engine.modeling.models import DatasetSnapshot, SupervisedRow

_START = datetime(2026, 8, 24, tzinfo=UTC)
_END = _START + timedelta(hours=10)
_SOURCE = ModelSpec(
    run_id="phase7-300-service-test",
    semantic_sha256="a" * 64,
    horizon_seconds=300,
    dataset_version="supervised-core-v1",
    split_version="chronological-market-v1",
    feature_version="core-v1",
    label_version="official-outcome-v1",
    validation_champion="market_price",
    market_price_config={
        "predictor": "pm_up_price",
        "missing_fallback": "training_prior",
        "clip_epsilon": 1e-6,
    },
)
_CONFIG = WalkForwardConfig(
    train_duration=timedelta(hours=2),
    validation_duration=timedelta(hours=1),
    test_duration=timedelta(hours=1),
    step_duration=timedelta(hours=1),
    final_holdout_duration=timedelta(hours=1),
    embargo_markets=0,
    min_train_markets=8,
    min_validation_markets=4,
    min_test_markets=4,
    min_market_price_coverage=0.80,
    min_prediction_coverage=0.90,
)


def _predictors(target: int, offset_seconds: int) -> dict[str, float | None]:
    price = (
        (0.80 if target else 0.20)
        if offset_seconds == 60
        else (0.55 if target else 0.45)
    )
    return {
        "pm_up_price": price,
        "pm_up_best_ask": 0.60,
        "pm_down_best_ask": 0.40,
        "missing__pm_up_book_missing": 0.0,
        "missing__pm_up_book_stale": 0.0,
        "missing__pm_down_book_missing": 0.0,
        "missing__pm_down_book_stale": 0.0,
        "coinbase_realized_vol_15m": 0.1 + (target * 0.2),
    }


def _dataset(
    *,
    mutate_final_holdout: bool = False,
    missing_selected_conditions: set[str] | None = None,
) -> DatasetSnapshot:
    rows: list[SupervisedRow] = []
    missing_selected_conditions = missing_selected_conditions or set()
    market_count = int((_END - _START).total_seconds() // 300)
    holdout_start = _END - _CONFIG.final_holdout_duration
    for index in range(market_count):
        market_start = _START + timedelta(seconds=300 * index)
        market_end = market_start + timedelta(seconds=300)
        condition_id = f"condition-{index:03d}"
        base_target = index % 2
        if mutate_final_holdout and market_start >= holdout_start:
            target = 1 - base_target
        else:
            target = base_target
        for offset_seconds in (60, 120):
            if offset_seconds == 60 and condition_id in missing_selected_conditions:
                continue
            predictors = _predictors(target, offset_seconds)
            if mutate_final_holdout and market_start >= holdout_start:
                predictors = {
                    **predictors,
                    "pm_up_price": 1.0 - float(predictors["pm_up_price"] or 0.5),
                    "coinbase_realized_vol_15m": 9.0,
                }
            rows.append(
                SupervisedRow(
                    condition_id=condition_id,
                    slug=f"market-{index:03d}",
                    horizon_seconds=300,
                    market_start_at=market_start,
                    market_end_at=market_end,
                    feature_at=market_start + timedelta(seconds=offset_seconds),
                    feature_offset_seconds=offset_seconds,
                    predictors=predictors,
                    target=target,
                    feature_hash=f"{index:064x}"[-64:],
                    input_fingerprint=f"{index + offset_seconds:064x}"[-64:],
                )
            )
    return DatasetSnapshot(
        dataset_version="supervised-core-v1",
        feature_version="core-v1",
        label_version="official-outcome-v1",
        horizon_seconds=300,
        start=_START,
        end=_END,
        rows=tuple(rows),
        predictor_names=tuple(sorted(rows[0].predictors)),
        dataset_sha256=("c" if not mutate_final_holdout else "d") * 64,
    )


def _patch_sources(monkeypatch: pytest.MonkeyPatch, dataset: DatasetSnapshot) -> None:
    monkeypatch.setattr(
        "bp_engine.backtesting.service.load_model_spec",
        lambda connection, run_id: _SOURCE,
    )
    monkeypatch.setattr(
        "bp_engine.backtesting.service.load_dataset",
        lambda connection, **kwargs: dataset,
    )


def test_walk_forward_service_produces_unique_ordinary_oos_folds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_sources(monkeypatch, _dataset())

    report = run_walk_forward_backtest(
        object(),
        source_training_run_id=_SOURCE.run_id,
        start=_START,
        end=_END,
        config=_CONFIG,
        created_at=datetime(2026, 8, 25, 20, 30, tzinfo=UTC),
    )

    assert len(report.folds) >= 3
    ordinary_ids = [
        condition_id
        for fold in report.folds
        for condition_id in fold.test_condition_ids
    ]
    assert len(ordinary_ids) == len(set(ordinary_ids))
    assert tuple(ordinary_ids) == report.aggregate_oos_condition_ids
    assert report.final_holdout.holdout_condition_ids
    assert set(report.final_holdout.holdout_condition_ids).isdisjoint(ordinary_ids)
    for fold in report.folds:
        assert fold.selected_offset_seconds == 60
        assert fold.validation_candidates
        assert len(fold.membership_sha256) == 64


def test_aggregate_oos_report_includes_regimes_with_market_count_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_sources(monkeypatch, _dataset())
    report = run_walk_forward_backtest(
        object(),
        source_training_run_id=_SOURCE.run_id,
        start=_START,
        end=_END,
        config=_CONFIG,
        created_at=datetime(2026, 8, 25, 20, 30, tzinfo=UTC),
    )

    regimes = report.aggregate_oos_regimes
    assert set(regimes) == {"utc_session", "volatility", "execution_availability"}
    expected = report.aggregate_oos_metrics.market_count
    for groups in regimes.values():
        assert sum(group["market_count"] for group in groups.values()) == expected


def test_final_holdout_mutation_cannot_change_ordinary_fold_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_dataset = _dataset()
    _patch_sources(monkeypatch, first_dataset)
    first = run_walk_forward_backtest(
        object(),
        source_training_run_id=_SOURCE.run_id,
        start=_START,
        end=_END,
        config=_CONFIG,
        created_at=datetime(2026, 8, 25, 20, 31, tzinfo=UTC),
    )

    second_dataset = _dataset(mutate_final_holdout=True)
    _patch_sources(monkeypatch, second_dataset)
    second = run_walk_forward_backtest(
        object(),
        source_training_run_id=_SOURCE.run_id,
        start=_START,
        end=_END,
        config=_CONFIG,
        created_at=datetime(2026, 8, 25, 20, 32, tzinfo=UTC),
    )

    first_choices = tuple(
        (
            fold.membership_sha256,
            fold.selected_offset_seconds,
            fold.validation_candidates,
        )
        for fold in first.folds
    )
    second_choices = tuple(
        (
            fold.membership_sha256,
            fold.selected_offset_seconds,
            fold.validation_candidates,
        )
        for fold in second.folds
    )
    assert first_choices == second_choices
    assert first.aggregate_oos_condition_ids == second.aggregate_oos_condition_ids
    assert first.aggregate_oos_metrics == second.aggregate_oos_metrics
    assert first.final_holdout.metrics != second.final_holdout.metrics


def test_missing_selected_offset_is_not_substituted_and_fails_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = {"condition-036", "condition-037", "condition-038"}
    dataset = _dataset(missing_selected_conditions=missing)
    _patch_sources(monkeypatch, dataset)

    with pytest.raises(BacktestIntegrityError, match="prediction coverage"):
        run_walk_forward_backtest(
            object(),
            source_training_run_id=_SOURCE.run_id,
            start=_START,
            end=_END,
            config=_CONFIG,
            created_at=datetime(2026, 8, 25, 20, 33, tzinfo=UTC),
        )


def test_walk_forward_semantic_identity_ignores_created_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_sources(monkeypatch, _dataset())
    first = run_walk_forward_backtest(
        object(),
        source_training_run_id=_SOURCE.run_id,
        start=_START,
        end=_END,
        config=_CONFIG,
        created_at=datetime(2026, 8, 25, 20, 34, tzinfo=UTC),
    )
    second = run_walk_forward_backtest(
        object(),
        source_training_run_id=_SOURCE.run_id,
        start=_START,
        end=_END,
        config=_CONFIG,
        created_at=datetime(2026, 8, 25, 20, 35, tzinfo=UTC),
    )

    assert first.run_id == second.run_id
    assert first.semantic_sha256 == second.semantic_sha256
    assert first.config_sha256 == second.config_sha256
    assert first.plan_sha256 == second.plan_sha256
    assert first.created_at != second.created_at
