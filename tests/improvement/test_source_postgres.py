from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, delete, insert

from bp_engine.improvement.models import ChampionRef
from bp_engine.improvement.source import (
    ChampionIntegrityError,
    load_champion_ref,
    load_phase9_report,
)
from bp_engine.storage import schema

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)

TRAINING_RUN_ID = "phase7-300-source-test"
BACKTEST_RUN_ID = "phase8-300-source-test"
CALIBRATION_RUN_ID = "phase9-300-source-test"
TRAINING_SHA = "1" * 64
BACKTEST_SHA = "2" * 64
CALIBRATION_SHA = "3" * 64
START = datetime(2026, 8, 24, tzinfo=UTC)
END = datetime(2026, 8, 25, tzinfo=UTC)
CREATED = datetime(2026, 8, 26, tzinfo=UTC)


def _training_values() -> dict[str, object]:
    report = {
        "run_id": TRAINING_RUN_ID,
        "dataset_version": "supervised-core-v1",
        "split_version": "walk-forward-v1",
        "feature_version": "core-v1",
        "label_version": "official-outcome-v1",
        "horizon_seconds": 300,
        "semantic_sha256": TRAINING_SHA,
    }
    return {
        "run_id": TRAINING_RUN_ID,
        "dataset_version": "supervised-core-v1",
        "split_version": "walk-forward-v1",
        "feature_version": "core-v1",
        "label_version": "official-outcome-v1",
        "horizon_seconds": 300,
        "requested_start": START,
        "requested_end": END,
        "dataset_sha256": "4" * 64,
        "split_sha256": "5" * 64,
        "predictor_names": ["pm_up_price"],
        "dropped_all_missing": [],
        "model_configs": {"market_price": {"predictor": "pm_up_price"}},
        "validation_champion": "market_price",
        "report": report,
        "artifact_manifest": [],
        "semantic_sha256": TRAINING_SHA,
        "created_at": CREATED,
    }


def _backtest_values(*, source_training_sha: str = TRAINING_SHA) -> dict[str, object]:
    report = {
        "run_id": BACKTEST_RUN_ID,
        "source_training_run_id": TRAINING_RUN_ID,
        "source_training_semantic_sha256": source_training_sha,
        "horizon_seconds": 300,
        "semantic_sha256": BACKTEST_SHA,
    }
    return {
        "run_id": BACKTEST_RUN_ID,
        "backtest_version": "walk-forward-v1",
        "source_training_run_id": TRAINING_RUN_ID,
        "source_training_semantic_sha256": source_training_sha,
        "dataset_version": "supervised-core-v1",
        "feature_version": "core-v1",
        "label_version": "official-outcome-v1",
        "horizon_seconds": 300,
        "requested_start": START,
        "requested_end": END,
        "dataset_sha256": "4" * 64,
        "config": {"cost_model": "assumed"},
        "config_sha256": "6" * 64,
        "plan_sha256": "7" * 64,
        "fold_membership_sha256": ["8" * 64],
        "report": report,
        "semantic_sha256": BACKTEST_SHA,
        "created_at": CREATED,
    }


def _calibration_values(
    *,
    source_backtest_sha: str = BACKTEST_SHA,
    source_training_sha: str = TRAINING_SHA,
) -> dict[str, object]:
    report = {
        "run_id": CALIBRATION_RUN_ID,
        "source_backtest_run_id": BACKTEST_RUN_ID,
        "source_backtest_semantic_sha256": source_backtest_sha,
        "source_training_run_id": TRAINING_RUN_ID,
        "source_training_semantic_sha256": source_training_sha,
        "horizon_seconds": 300,
        "semantic_sha256": CALIBRATION_SHA,
    }
    return {
        "run_id": CALIBRATION_RUN_ID,
        "calibration_version": "calibration-v1",
        "edge_policy_version": "edge-policy-v1",
        "source_backtest_run_id": BACKTEST_RUN_ID,
        "source_backtest_semantic_sha256": source_backtest_sha,
        "source_training_run_id": TRAINING_RUN_ID,
        "source_training_semantic_sha256": source_training_sha,
        "dataset_version": "supervised-core-v1",
        "feature_version": "core-v1",
        "label_version": "official-outcome-v1",
        "horizon_seconds": 300,
        "requested_start": START,
        "requested_end": END,
        "dataset_sha256": "4" * 64,
        "config": {"edge": "validation-selected"},
        "config_sha256": "9" * 64,
        "source_plan_sha256": "7" * 64,
        "source_fold_membership_sha256": ["8" * 64],
        "report": report,
        "semantic_sha256": CALIBRATION_SHA,
        "created_at": CREATED,
    }


def _setup(
    *,
    calibration_backtest_sha: str = BACKTEST_SHA,
    calibration_training_sha: str = TRAINING_SHA,
    backtest_training_sha: str = TRAINING_SHA,
):
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(delete(schema.calibration_edge_runs))
        connection.execute(delete(schema.backtest_runs))
        connection.execute(delete(schema.model_training_runs))
        connection.execute(insert(schema.model_training_runs).values(**_training_values()))
        connection.execute(
            insert(schema.backtest_runs).values(
                **_backtest_values(source_training_sha=backtest_training_sha)
            )
        )
        connection.execute(
            insert(schema.calibration_edge_runs).values(
                **_calibration_values(
                    source_backtest_sha=calibration_backtest_sha,
                    source_training_sha=calibration_training_sha,
                )
            )
        )
    return engine


def test_load_champion_ref_reconstructs_exact_phase9_phase8_phase7_chain() -> None:
    engine = _setup()

    with engine.connect() as connection:
        champion = load_champion_ref(connection, CALIBRATION_RUN_ID)

    assert champion == ChampionRef(
        calibration_run_id=CALIBRATION_RUN_ID,
        calibration_semantic_sha256=CALIBRATION_SHA,
        backtest_run_id=BACKTEST_RUN_ID,
        backtest_semantic_sha256=BACKTEST_SHA,
        training_run_id=TRAINING_RUN_ID,
        training_semantic_sha256=TRAINING_SHA,
    )


def test_load_phase9_report_validates_stored_identity() -> None:
    engine = _setup()

    with engine.connect() as connection:
        report = load_phase9_report(connection, CALIBRATION_RUN_ID)

    assert report["run_id"] == CALIBRATION_RUN_ID
    assert report["semantic_sha256"] == CALIBRATION_SHA


@pytest.mark.parametrize(
    ("calibration_backtest_sha", "calibration_training_sha", "backtest_training_sha"),
    [
        ("f" * 64, TRAINING_SHA, TRAINING_SHA),
        (BACKTEST_SHA, "f" * 64, TRAINING_SHA),
        (BACKTEST_SHA, TRAINING_SHA, "f" * 64),
    ],
)
def test_corrupt_source_hash_fails_closed(
    calibration_backtest_sha: str,
    calibration_training_sha: str,
    backtest_training_sha: str,
) -> None:
    engine = _setup(
        calibration_backtest_sha=calibration_backtest_sha,
        calibration_training_sha=calibration_training_sha,
        backtest_training_sha=backtest_training_sha,
    )

    with engine.connect() as connection:
        with pytest.raises(ChampionIntegrityError, match="provenance"):
            load_champion_ref(connection, CALIBRATION_RUN_ID)


def test_phase9_report_rejects_semantic_rewrite_inside_report() -> None:
    engine = _setup()
    with engine.begin() as connection:
        row = connection.execute(
            schema.calibration_edge_runs.select().where(
                schema.calibration_edge_runs.c.run_id == CALIBRATION_RUN_ID
            )
        ).mappings().one()
        rewritten = dict(row["report"])
        rewritten["semantic_sha256"] = "e" * 64
        connection.execute(
            schema.calibration_edge_runs.update()
            .where(schema.calibration_edge_runs.c.run_id == CALIBRATION_RUN_ID)
            .values(report=rewritten)
        )

    with engine.connect() as connection:
        with pytest.raises(ChampionIntegrityError, match="immutable column"):
            load_phase9_report(connection, CALIBRATION_RUN_ID)
