from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from bp_engine.backtesting.predictor import (
    MarketPriceFoldPredictor,
    ModelSpecIntegrityError,
    SourceTrainingRunNotFound,
    load_model_spec,
)

from bp_engine.modeling.models import (
    MetricSummary,
    ModelEvaluation,
    SupervisedRow,
    TrainingRunReport,
)
from bp_engine.modeling.repository import ModelTrainingRunRepository
from bp_engine.storage.schema import metadata


MARKET_PRICE_CONFIG = {
    "predictor": "pm_up_price",
    "missing_fallback": "training_prior",
    "clip_epsilon": 1e-6,
}


def _metric() -> MetricSummary:
    return MetricSummary(
        row_count=10,
        market_count=10,
        accuracy=0.6,
        balanced_accuracy=0.6,
        log_loss=0.4,
        brier_score=0.2,
        ece=0.05,
        calibration=(),
        confidence_coverage={},
    )


def _report(**overrides: object) -> TrainingRunReport:
    evaluation = ModelEvaluation(
        family="market_price",
        config=MARKET_PRICE_CONFIG,
        validation=_metric(),
        test=_metric(),
    )
    values: dict[str, object] = {
        "run_id": "phase7-market-price-run",
        "dataset_version": "supervised-core-v1",
        "split_version": "chronological-market-v1",
        "feature_version": "core-v1",
        "label_version": "official-outcome-v1",
        "horizon_seconds": 300,
        "start": datetime(2026, 8, 24, tzinfo=UTC),
        "end": datetime(2026, 8, 25, tzinfo=UTC),
        "dataset_sha256": "d" * 64,
        "split_sha256": "s" * 64,
        "predictor_names": ("pm_up_price",),
        "dropped_all_missing": (),
        "model_configs": {"market_price": MARKET_PRICE_CONFIG},
        "validation_champion": "market_price",
        "best_test_result": "market_price",
        "boosted_promotion_eligible": False,
        "evaluations": {"market_price": evaluation},
        "offset_metrics": {},
        "gross_execution_diagnostic": {"coverage": 1.0},
        "artifacts": (),
        "semantic_sha256": "e" * 64,
        "created_at": datetime(2026, 8, 25, 18, 0, tzinfo=UTC),
    }
    values.update(overrides)
    return TrainingRunReport(**values)  # type: ignore[arg-type]


def _engine_with_report(report: TrainingRunReport):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    with engine.begin() as connection:
        ModelTrainingRunRepository().store(connection, report)
    return engine


def _row(
    condition_id: str,
    *,
    target: int,
    price: float | None,
    offset_seconds: int = 60,
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


def test_load_model_spec_accepts_exact_phase7_market_price_contract() -> None:
    report = _report()
    engine = _engine_with_report(report)

    with engine.begin() as connection:
        spec = load_model_spec(connection, report.run_id)

    assert spec.run_id == report.run_id
    assert spec.semantic_sha256 == "e" * 64
    assert spec.horizon_seconds == 300
    assert spec.dataset_version == "supervised-core-v1"
    assert spec.split_version == "chronological-market-v1"
    assert spec.feature_version == "core-v1"
    assert spec.label_version == "official-outcome-v1"
    assert spec.validation_champion == "market_price"
    assert spec.market_price_config == MARKET_PRICE_CONFIG


def test_load_model_spec_rejects_missing_source_run() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)

    with engine.begin() as connection:
        with pytest.raises(SourceTrainingRunNotFound, match="missing-run"):
            load_model_spec(connection, "missing-run")


@pytest.mark.parametrize(
    "overrides",
    [
        {"validation_champion": "logistic"},
        {"dataset_version": "unexpected-dataset"},
        {"split_version": "random-split"},
        {"feature_version": "future-feature"},
        {"label_version": "future-label"},
        {
            "model_configs": {
                "market_price": {
                    **MARKET_PRICE_CONFIG,
                    "clip_epsilon": 1e-5,
                }
            }
        },
    ],
)
def test_load_model_spec_fails_closed_on_contract_mismatch(
    overrides: dict[str, object],
) -> None:
    report = _report(**overrides)
    engine = _engine_with_report(report)

    with engine.begin() as connection:
        with pytest.raises(ModelSpecIntegrityError):
            load_model_spec(connection, report.run_id)


def test_market_price_predictor_uses_training_only_market_weighted_prior() -> None:
    train = (
        _row("up-market", target=1, price=0.7, offset_seconds=60),
        _row("up-market", target=1, price=0.8, offset_seconds=120),
        _row("down-market", target=0, price=0.3, offset_seconds=60),
    )
    evaluation = (
        _row("validation-a", target=1, price=None),
        _row("validation-b", target=0, price=None),
    )
    predictor = MarketPriceFoldPredictor()
    predictor.fit(train)

    original = predictor.predict(evaluation)
    mutated = predictor.predict(
        tuple(replace(row, target=1 - row.target) for row in evaluation)
    )

    assert original == pytest.approx((0.5, 0.5))
    assert mutated == pytest.approx(original)


def test_market_price_predictor_reports_observed_price_coverage() -> None:
    rows = (
        _row("a", target=1, price=0.6),
        _row("b", target=0, price=None),
        _row("c", target=1, price=0.7),
        _row("d", target=0, price=None),
    )
    predictor = MarketPriceFoldPredictor()

    assert predictor.observed_price_coverage(rows) == pytest.approx(0.5)
