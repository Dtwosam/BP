from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from bp_engine.backtesting.cli import _run, build_parser
from sqlalchemy import create_engine, func, select

from bp_engine.backtesting.models import BacktestReport, FinalHoldoutReport
from bp_engine.modeling.models import MetricSummary
from bp_engine.storage.schema import backtest_runs


def _metric() -> MetricSummary:
    return MetricSummary(
        row_count=8,
        market_count=8,
        accuracy=0.625,
        balanced_accuracy=0.625,
        log_loss=0.6,
        brier_score=0.21,
        ece=0.05,
        calibration=(),
        confidence_coverage={},
    )


def _report(source_run_id: str, horizon_seconds: int, created_at: datetime) -> BacktestReport:
    metric = _metric()
    semantic = ("9" if horizon_seconds == 300 else "8") * 64
    final = FinalHoldoutReport(
        membership_sha256="f" * 64,
        train_condition_ids=("train-a", "train-b"),
        validation_condition_ids=("validation-a", "validation-b"),
        holdout_condition_ids=("holdout-a", "holdout-b"),
        selected_offset_seconds=60,
        validation_candidates=(),
        expected_holdout_markets=2,
        predicted_holdout_markets=2,
        missing_offset_condition_ids=(),
        prediction_coverage=1.0,
        metrics=metric,
        accuracy_wilson_95=(0.2, 0.9),
        volatility_threshold=0.2,
        execution={"execution_coverage": 0.5},
        regimes={"utc_session": {}},
    )
    return BacktestReport(
        run_id=f"phase8-{horizon_seconds}-{semantic[:32]}",
        backtest_version="walk-forward-v1",
        source_training_run_id=source_run_id,
        source_training_semantic_sha256="a" * 64,
        dataset_version="supervised-core-v1",
        feature_version="core-v1",
        label_version="official-outcome-v1",
        horizon_seconds=horizon_seconds,
        start=datetime(2026, 8, 24, tzinfo=UTC),
        end=datetime(2026, 8, 25, tzinfo=UTC),
        dataset_sha256="b" * 64,
        config={"train_duration_seconds": 28800.0},
        config_sha256="c" * 64,
        plan_sha256="d" * 64,
        fold_membership_sha256=("e" * 64, "f" * 64),
        folds=(),
        aggregate_oos_condition_ids=("test-a", "test-b"),
        aggregate_oos_metrics=metric,
        aggregate_oos_accuracy_wilson_95=(0.2, 0.9),
        aggregate_oos_execution={"execution_coverage": 0.5},
        final_holdout=final,
        semantic_sha256=semantic,
        created_at=created_at,
    )


def test_parser_accepts_repeated_source_runs_and_documented_defaults(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        [
            "--start",
            "2026-08-24T00:00:00Z",
            "--end",
            "2026-08-25T00:00:00Z",
            "--source-training-run-id",
            "phase7-900-source",
            "--source-training-run-id",
            "phase7-300-source",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert args.source_training_run_id == ["phase7-900-source", "phase7-300-source"]
    assert args.start.tzinfo is not None and args.end.tzinfo is not None
    assert args.train_hours == 8
    assert args.validation_hours == 2
    assert args.test_hours == 2
    assert args.step_hours == 2
    assert args.final_holdout_hours == 2
    assert args.embargo_markets == 1
    assert args.min_train_markets == 24
    assert args.min_validation_markets == 6
    assert args.min_test_markets == 6
    assert args.min_market_price_coverage == pytest.approx(0.80)
    assert args.min_prediction_coverage == pytest.approx(0.90)


def test_backtesting_execution_path_has_no_network_client_imports() -> None:
    root = Path(__file__).parents[2]
    for relative in (
        "src/bp_engine/backtesting/cli.py",
        "src/bp_engine/backtesting/service.py",
        "src/bp_engine/backtesting/predictor.py",
    ):
        text = (root / relative).read_text(encoding="utf-8")
        assert "import httpx" not in text
        assert "from httpx" not in text
        assert "import websockets" not in text
        assert "from websockets" not in text


def test_cli_is_atomic_and_registry_rerun_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "phase8.sqlite"
    database_url = f"sqlite+pysqlite:///{database_path}"
    output_dir = tmp_path / "reports"

    def fake_service(
        connection: object,
        *,
        source_training_run_id: str,
        start: datetime,
        end: datetime,
        config: object,
        created_at: datetime,
    ) -> BacktestReport:
        horizon = 900 if "900" in source_training_run_id else 300
        return _report(source_training_run_id, horizon, created_at)

    monkeypatch.setattr("bp_engine.backtesting.cli.run_walk_forward_backtest", fake_service)
    args = build_parser().parse_args(
        [
            "--start",
            "2026-08-24T00:00:00Z",
            "--end",
            "2026-08-25T00:00:00Z",
            "--source-training-run-id",
            "phase7-900-source",
            "--source-training-run-id",
            "phase7-300-source",
            "--database-url",
            database_url,
            "--output-dir",
            str(output_dir),
        ]
    )

    first = _run(args)
    second = _run(args)

    engine = create_engine(database_url)
    with engine.begin() as connection:
        registry_count = connection.execute(
            select(func.count()).select_from(backtest_runs)
        ).scalar_one()

    assert registry_count == 2
    assert [item["horizon_seconds"] for item in first] == [300, 900]
    assert [item["semantic_sha256"] for item in first] == [
        item["semantic_sha256"] for item in second
    ]
    report_path = output_dir / "phase8-backtest-reports.json"
    assert report_path.exists()
    rendered = json.loads(report_path.read_text(encoding="utf-8"))
    assert [item["horizon_seconds"] for item in rendered] == [300, 900]
    assert not list(output_dir.glob("*.tmp"))


def test_thin_script_entrypoint_exists() -> None:
    script = Path(__file__).parents[2] / "scripts" / "run_walk_forward_backtest.py"
    text = script.read_text(encoding="utf-8")
    assert "bp_engine.backtesting.cli import main" in text
