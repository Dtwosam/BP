from dataclasses import replace
from decimal import Decimal

from sqlalchemy import create_engine

from bp_engine.execution.models import (
    PAPER_EXECUTION_VERSION,
    V3_FROZEN_PAPER_EXECUTION_VERSION,
    PaperExecutionConfig,
)
from bp_engine.execution.service import PaperExecutionService
from bp_engine.live_prediction.repository import LivePredictionRepository
from bp_engine.storage import schema
from tests.execution.test_service_postgres import BASE, _prediction


def test_default_v1_paper_config_hash_payload_is_backward_compatible() -> None:
    config = PaperExecutionConfig()

    assert config.execution_version == PAPER_EXECUTION_VERSION
    assert config.prediction_version is None
    assert config.excluded_prediction_versions == ()
    assert config.as_mapping() == {
        "execution_version": "paper-execution-v1",
        "starting_cash_usd": "100.00",
        "target_notional_usd": "5.00",
        "latency_ms": 250,
        "order_ttl_ms": 2000,
        "share_precision": 6,
    }


def test_v3_paper_config_is_isolated_without_changing_virtual_sizing() -> None:
    config = PaperExecutionConfig(
        execution_version=V3_FROZEN_PAPER_EXECUTION_VERSION,
        prediction_version="v3-frozen-paper-v1",
    )

    assert config.execution_version == "paper-execution-v3-frozen-v1"
    assert config.prediction_version == "v3-frozen-paper-v1"
    assert config.starting_cash_usd == Decimal("100.00")
    assert config.target_notional_usd == Decimal("5.00")
    assert config.as_mapping()["execution_version"] == "paper-execution-v3-frozen-v1"
    assert "prediction_version" not in config.as_mapping()


def test_paper_workers_examine_disjoint_prediction_populations() -> None:
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    repository = LivePredictionRepository()
    legacy = _prediction(
        prediction_id="1" * 64,
        semantic_sha256="a" * 64,
        condition_id="legacy-signal",
        trade=False,
    )
    v3 = replace(
        _prediction(
            prediction_id="2" * 64,
            semantic_sha256="b" * 64,
            condition_id="v3-signal",
            trade=False,
        ),
        prediction_version="v3-frozen-paper-v1",
    )
    with engine.begin() as connection:
        repository.store(connection, legacy)
        repository.store(connection, v3)

    legacy_service = PaperExecutionService(
        engine=engine,
        config=PaperExecutionConfig(
            execution_version=PAPER_EXECUTION_VERSION,
            excluded_prediction_versions=("v3-frozen-paper-v1",),
        ),
    )
    v3_service = PaperExecutionService(
        engine=engine,
        config=PaperExecutionConfig(
            execution_version=V3_FROZEN_PAPER_EXECUTION_VERSION,
            prediction_version="v3-frozen-paper-v1",
        ),
    )

    legacy_report = legacy_service.run_once(now=BASE)
    v3_report = v3_service.run_once(now=BASE)

    assert legacy_report.examined_predictions == 1
    assert legacy_report.skipped_predictions == 1
    assert v3_report.examined_predictions == 1
    assert v3_report.skipped_predictions == 1
