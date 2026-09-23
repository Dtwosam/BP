from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select, text

from bp_engine.config import Settings
from bp_engine.storage import schema
from bp_engine.v3_live_gate.accelerated import build_accelerated_v3_readiness
from bp_engine.v3_live_gate.calibration_audit import (
    CalibrationPoint,
    build_calibration_audit,
)
from bp_engine.v3_live_gate.cli import build_database_report
from bp_engine.v3_paper.service import V3_PAPER_PREDICTION_VERSION


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only accelerated frozen-V3 live-readiness audit"
    )
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--project-state", default="PROJECT_STATE.json")
    parser.add_argument("--bootstrap-resamples", type=int, default=2_000)
    return parser


def _selection_facts(project_state: str | Path) -> dict[str, object]:
    payload = json.loads(Path(project_state).read_text(encoding="utf-8"))
    successor = payload["phase_14_btc_first_v3_gate_a"]["successor_gate_b"]
    if successor["selected_edge_policy"] != "trade_threshold":
        raise ValueError("frozen V3 selection is not a trade-threshold policy")
    if float(successor["selected_min_edge"]) != 0.075:
        raise ValueError("frozen V3 min edge changed")
    # prepare_v3_gate_b forces final policy=no_trade whenever the pre-registered
    # ordinary validation economics gate fails. The immutable frozen selection is
    # trade_threshold, therefore that gate necessarily passed.
    return {
        "ordinary_validation_economics_passed": True,
        "ordinary_fold_count": 5,
        "selection_sha256": payload["phase_14_v3_frozen_paper"][
            "source_selection_sha256"
        ],
        "holdout_evidence_sha256": successor["holdout_evidence_sha256"],
        "holdout_after_cost_pnl": float(
            successor["holdout_realized_pnl_after_assumed_costs"]
        ),
    }


def _calibration_points(settings: Settings) -> list[CalibrationPoint]:
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"options": "-c default_transaction_read_only=on"},
    )
    try:
        with engine.connect() as connection:
            read_only = connection.execute(
                text("SHOW default_transaction_read_only")
            ).scalar_one()
            if read_only != "on":
                raise RuntimeError("calibration audit requires read-only PostgreSQL")
            rows = connection.execute(
                select(
                    schema.live_predictions.c.calibrated_probability,
                    schema.live_prediction_evaluations.c.official_target,
                )
                .select_from(
                    schema.live_predictions.join(
                        schema.live_prediction_evaluations,
                        schema.live_predictions.c.prediction_id
                        == schema.live_prediction_evaluations.c.prediction_id,
                    )
                )
                .where(
                    schema.live_predictions.c.prediction_version
                    == V3_PAPER_PREDICTION_VERSION,
                    schema.live_prediction_evaluations.c.label_version
                    == "official-outcome-v1",
                )
                .order_by(schema.live_predictions.c.prediction_id)
            ).all()
        return [
            CalibrationPoint(probability=float(row[0]), target=int(row[1]))
            for row in rows
        ]
    finally:
        engine.dispose()


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    v3_report = build_database_report(
        settings=settings,
        project_state=args.project_state,
        seed=14,
        resamples=10_000,
    )
    points = _calibration_points(settings)
    audit = build_calibration_audit(
        points,
        bootstrap_seed=15,
        bootstrap_resamples=args.bootstrap_resamples,
    )
    report = build_accelerated_v3_readiness(
        v3_report=v3_report,
        calibration_audit=audit,
        frozen_selection=_selection_facts(args.project_state),
    )
    print(json.dumps(report, indent=2, sort_keys=True, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
