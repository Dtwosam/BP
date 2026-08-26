from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select

from bp_engine.storage.schema import calibration_edge_runs

DEFAULT_MIN_EDGE_GRID = (0.0, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15)


def _module():
    return importlib.import_module("bp_engine.calibration.cli")


@dataclass(frozen=True)
class _CliReport:
    run_id: str
    calibration_version: str
    edge_policy_version: str
    source_backtest_run_id: str
    source_backtest_version: str
    source_backtest_semantic_sha256: str
    source_training_run_id: str
    source_training_semantic_sha256: str
    dataset_version: str
    feature_version: str
    label_version: str
    horizon_seconds: int
    start: datetime
    end: datetime
    dataset_sha256: str
    config: dict[str, Any]
    config_sha256: str
    source_backtest_config_sha256: str
    source_plan_sha256: str
    source_fold_membership_sha256: tuple[str, ...]
    folds: tuple[dict[str, Any], ...]
    aggregate_oos: dict[str, Any]
    final_holdout: dict[str, Any]
    semantic_sha256: str
    created_at: datetime


def _report(
    source_backtest_run_id: str,
    horizon_seconds: int,
    created_at: datetime,
    fee_rate: float,
    slippage_buffer: float,
) -> _CliReport:
    semantic = ("9" if horizon_seconds == 300 else "8") * 64
    return _CliReport(
        run_id=f"phase9-{horizon_seconds}-{semantic[:32]}",
        calibration_version="platt-or-identity-v1",
        edge_policy_version="selected-ask-edge-v1",
        source_backtest_run_id=source_backtest_run_id,
        source_backtest_version="walk-forward-v1",
        source_backtest_semantic_sha256="7" * 64,
        source_training_run_id=f"phase7-{horizon_seconds}-source",
        source_training_semantic_sha256="a" * 64,
        dataset_version="supervised-core-v1",
        feature_version="core-v1",
        label_version="official-outcome-v1",
        horizon_seconds=horizon_seconds,
        start=datetime(2026, 8, 24, tzinfo=UTC),
        end=datetime(2026, 8, 25, tzinfo=UTC),
        dataset_sha256="b" * 64,
        config={
            "fee_rate": fee_rate,
            "slippage_buffer": slippage_buffer,
            "min_edge_grid": list(DEFAULT_MIN_EDGE_GRID),
            "min_validation_trades": 3,
            "max_spread": None,
        },
        config_sha256="c" * 64,
        source_backtest_config_sha256="4" * 64,
        source_plan_sha256="d" * 64,
        source_fold_membership_sha256=("e" * 64, "f" * 64),
        folds=({"index": 0, "edge_policy": "no_trade"},),
        aggregate_oos={"edge_metrics": {"trade_count": 0}},
        final_holdout={"edge_metrics": {"trade_count": 0}},
        semantic_sha256=semantic,
        created_at=created_at,
    )


def _base_args(tmp_path: Path) -> list[str]:
    return [
        "--start",
        "2026-08-24T00:00:00Z",
        "--end",
        "2026-08-25T00:00:00Z",
        "--source-backtest-run-id",
        "phase8-900-source",
        "--source-backtest-run-id",
        "phase8-300-source",
        "--fee-rate",
        "0.07",
        "--slippage-buffer",
        "0.01",
        "--output-dir",
        str(tmp_path),
    ]


def test_parser_requires_explicit_cost_assumptions_and_uses_frozen_defaults(
    tmp_path: Path,
) -> None:
    module = _module()
    args = module.build_parser().parse_args(_base_args(tmp_path))
    config = module._config(args)

    assert args.source_backtest_run_id == ["phase8-900-source", "phase8-300-source"]
    assert args.fee_rate == pytest.approx(0.07)
    assert args.slippage_buffer == pytest.approx(0.01)
    assert config.min_edge_grid == DEFAULT_MIN_EDGE_GRID
    assert config.min_validation_trades == 3
    assert config.max_spread is None

    without_fee = _base_args(tmp_path)
    fee_index = without_fee.index("--fee-rate")
    del without_fee[fee_index : fee_index + 2]
    with pytest.raises(SystemExit):
        module.build_parser().parse_args(without_fee)

    without_slippage = _base_args(tmp_path)
    slippage_index = without_slippage.index("--slippage-buffer")
    del without_slippage[slippage_index : slippage_index + 2]
    with pytest.raises(SystemExit):
        module.build_parser().parse_args(without_slippage)


def test_duplicate_source_backtest_run_ids_are_rejected(tmp_path: Path) -> None:
    module = _module()
    args = module.build_parser().parse_args(
        [
            "--start",
            "2026-08-24T00:00:00Z",
            "--end",
            "2026-08-25T00:00:00Z",
            "--source-backtest-run-id",
            "phase8-300-source",
            "--source-backtest-run-id",
            "phase8-300-source",
            "--fee-rate",
            "0.07",
            "--slippage-buffer",
            "0.01",
            "--database-url",
            f"sqlite+pysqlite:///{tmp_path / 'duplicate.sqlite'}",
        ]
    )

    with pytest.raises(ValueError, match="unique"):
        module._run(args)


def test_cli_is_atomic_sorted_and_registry_rerun_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    database_path = tmp_path / "phase9.sqlite"
    database_url = f"sqlite+pysqlite:///{database_path}"
    output_dir = tmp_path / "reports"

    def fake_service(
        connection: object,
        *,
        source_backtest_run_id: str,
        start: datetime,
        end: datetime,
        edge_config: object,
        created_at: datetime,
    ) -> _CliReport:
        horizon = 900 if "900" in source_backtest_run_id else 300
        return _report(
            source_backtest_run_id,
            horizon,
            created_at,
            edge_config.fee_rate,
            edge_config.slippage_buffer,
        )

    monkeypatch.setattr(module, "run_calibration_edge_analysis", fake_service)
    args = module.build_parser().parse_args(
        [
            *_base_args(output_dir),
            "--database-url",
            database_url,
        ]
    )

    first = module._run(args)
    second = module._run(args)

    engine = create_engine(database_url)
    with engine.begin() as connection:
        registry_count = connection.execute(
            select(func.count()).select_from(calibration_edge_runs)
        ).scalar_one()

    assert registry_count == 2
    assert [item["horizon_seconds"] for item in first] == [300, 900]
    assert [item["semantic_sha256"] for item in first] == [
        item["semantic_sha256"] for item in second
    ]
    for item in first:
        report_path = output_dir / f"{item['run_id']}.json"
        assert report_path.exists()
        rendered = json.loads(report_path.read_text(encoding="utf-8"))
        assert rendered["semantic_sha256"] == item["semantic_sha256"]
        assert rendered["config"]["fee_rate"] == pytest.approx(0.07)
        assert rendered["config"]["slippage_buffer"] == pytest.approx(0.01)
    assert not list(output_dir.glob("*.tmp"))


def test_calibration_cli_execution_path_has_no_network_client_imports() -> None:
    root = Path(__file__).parents[2]
    for relative in (
        "src/bp_engine/calibration/cli.py",
        "src/bp_engine/calibration/service.py",
        "src/bp_engine/calibration/source.py",
    ):
        text = (root / relative).read_text(encoding="utf-8")
        assert "import httpx" not in text
        assert "from httpx" not in text
        assert "import websockets" not in text
        assert "from websockets" not in text


def test_thin_script_entrypoint_exists() -> None:
    script = Path(__file__).parents[2] / "scripts" / "run_calibration_edge.py"
    text = script.read_text(encoding="utf-8")
    assert "bp_engine.calibration.cli import main" in text
