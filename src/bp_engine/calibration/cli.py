from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine

from bp_engine.calibration.models import EdgeConfig
from bp_engine.calibration.repository import CalibrationEdgeRunRepository
from bp_engine.calibration.service import run_calibration_edge_analysis
from bp_engine.config import Settings
from bp_engine.storage.schema import metadata

DEFAULT_MIN_EDGE_GRID = (0.0, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15)


def parse_datetime(value: str) -> datetime:
    rendered = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(rendered)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO-8601 datetime: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include a timezone")
    return parsed.astimezone(UTC)


def validate_window(start: datetime, end: datetime) -> None:
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("start must be timezone-aware")
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("end must be timezone-aware")
    if end <= start:
        raise ValueError("start must be before end")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the offline Phase 9 BTC Polymarket calibration and edge analysis"
    )
    parser.add_argument("--start", type=parse_datetime, required=True)
    parser.add_argument("--end", type=parse_datetime, required=True)
    parser.add_argument(
        "--source-backtest-run-id",
        action="append",
        required=True,
        help="repeat for each immutable accepted Phase 8 source backtest run",
    )
    parser.add_argument(
        "--fee-rate",
        type=float,
        required=True,
        help="explicit fee-rate coefficient used by the documented cost curve",
    )
    parser.add_argument(
        "--slippage-buffer",
        type=float,
        required=True,
        help="explicit per-share slippage assumption",
    )
    parser.add_argument(
        "--min-edge",
        action="append",
        type=float,
        default=None,
        help="repeat to replace the frozen minimum-edge candidate grid",
    )
    parser.add_argument("--min-validation-trades", type=int, default=3)
    parser.add_argument("--max-spread", type=float, default=None)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--output-dir", default=None)
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    if args.database_url:
        settings = settings.model_copy(update={"database_url": args.database_url})
    return settings


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _config(args: argparse.Namespace) -> EdgeConfig:
    min_edge_grid = (
        tuple(args.min_edge) if args.min_edge is not None else DEFAULT_MIN_EDGE_GRID
    )
    return EdgeConfig(
        fee_rate=args.fee_rate,
        slippage_buffer=args.slippage_buffer,
        min_edge_grid=min_edge_grid,
        min_validation_trades=args.min_validation_trades,
        max_spread=args.max_spread,
    )


def _write_atomic(output_dir: Path, report: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / f"{report['run_id']}.json"
    temporary_path = output_dir / f".{final_path.name}.{os.getpid()}.tmp"
    try:
        temporary_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, final_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return final_path


def _run(args: argparse.Namespace) -> list[dict[str, Any]]:
    validate_window(args.start, args.end)
    edge_config = _config(args)
    source_run_ids = tuple(args.source_backtest_run_id)
    if not source_run_ids:
        raise ValueError("at least one source backtest run id is required")
    if len(set(source_run_ids)) != len(source_run_ids):
        raise ValueError("source backtest run ids must be unique")

    settings = _settings(args)
    engine = create_engine(settings.database_url)
    metadata.create_all(engine)
    repository = CalibrationEdgeRunRepository()
    reports: list[dict[str, Any]] = []

    for source_run_id in source_run_ids:
        with engine.begin() as connection:
            report = run_calibration_edge_analysis(
                connection,
                source_backtest_run_id=source_run_id,
                start=args.start,
                end=args.end,
                edge_config=edge_config,
                created_at=datetime.now(UTC),
            )
            repository.store(connection, report)
        reports.append(_jsonable(asdict(report)))

    reports.sort(key=lambda item: int(item["horizon_seconds"]))
    if args.output_dir:
        output_dir = Path(args.output_dir)
        for report in reports:
            _write_atomic(output_dir, report)
    return reports


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        reports = _run(args)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(reports, indent=2, sort_keys=True))
    return 0
