from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, select, text

from bp_engine.config import Settings
from bp_engine.storage import schema
from bp_engine.v3_live_gate.report import build_v3_live_gate_report
from bp_engine.v3_paper.service import V3_PAPER_EXECUTION_VERSION, V3_PAPER_PREDICTION_VERSION


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only frozen V3 live-gate reassessment")
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--project-state", default="PROJECT_STATE.json")
    parser.add_argument("--bootstrap-seed", type=int, default=14)
    parser.add_argument("--bootstrap-resamples", type=int, default=10_000)
    return parser


def _authorization(path: str | Path) -> bool:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    gate = payload.get("phase_14_v3_live_gate_reassessment") or {}
    return gate.get("explicit_user_live_authorization") == "pass"


def _latest(rows: list[dict[str, Any]], key: str, timestamp: str) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        identity = str(row[key])
        if identity not in latest or row[timestamp] > latest[identity][timestamp]:
            latest[identity] = row
    return sorted(latest.values(), key=lambda row: (row[timestamp], str(row[key])))


def build_database_report(*, settings: Settings, project_state: str | Path, seed: int, resamples: int) -> dict[str, object]:
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"options": "-c default_transaction_read_only=on"},
    )
    try:
        with engine.connect() as connection:
            if connection.execute(text("SHOW default_transaction_read_only")).scalar_one() != "on":
                raise RuntimeError("V3 live-gate reassessment requires a read-only PostgreSQL session")
            orders = [dict(row) for row in connection.execute(
                select(schema.paper_orders).where(
                    schema.paper_orders.c.execution_version == V3_PAPER_EXECUTION_VERSION
                )
            ).mappings()]
            order_ids = tuple(str(row["paper_order_id"]) for row in orders)
            prediction_ids = tuple(str(row["prediction_id"]) for row in orders)
            settlements = [] if not order_ids else [dict(row) for row in connection.execute(
                select(schema.paper_settlements).where(schema.paper_settlements.c.paper_order_id.in_(order_ids))
            ).mappings()]
            evaluations = [] if not prediction_ids else [dict(row) for row in connection.execute(
                select(schema.live_prediction_evaluations).where(
                    schema.live_prediction_evaluations.c.prediction_id.in_(prediction_ids)
                )
            ).mappings()]
            fills = [] if not order_ids else [dict(row) for row in connection.execute(
                select(schema.paper_fills).where(schema.paper_fills.c.paper_order_id.in_(order_ids))
            ).mappings()]
            invalid_sources = connection.scalar(
                select(func.count()).select_from(schema.paper_orders.join(
                    schema.live_predictions,
                    schema.paper_orders.c.prediction_id == schema.live_predictions.c.prediction_id,
                )).where(
                    schema.paper_orders.c.execution_version == V3_PAPER_EXECUTION_VERSION,
                    schema.live_predictions.c.prediction_version != V3_PAPER_PREDICTION_VERSION,
                )
            ) or 0
        latest_settlements = _latest(settlements, "paper_order_id", "settled_at")
        latest_evaluations = _latest(evaluations, "prediction_id", "evaluated_at")
        fill_by_order: dict[str, list[dict[str, Any]]] = {}
        for fill in fills:
            fill_by_order.setdefault(str(fill["paper_order_id"]), []).append(fill)
        violations = int(invalid_sources)
        for settlement in latest_settlements:
            order_fills = fill_by_order.get(str(settlement["paper_order_id"]), [])
            shares = sum((row["shares"] for row in order_fills), 0)
            cost = sum((row["total_cost"] for row in order_fills), 0)
            if shares != settlement["filled_shares"] or cost != settlement["total_fill_cost"]:
                violations += 1
        reconciliation = {"status": "OK" if violations == 0 else "VIOLATION", "violation_count": violations}
        return build_v3_live_gate_report(
            settlements=latest_settlements,
            evaluations=latest_evaluations,
            reconciliation=reconciliation,
            user_authorized=_authorization(project_state),
            bootstrap_seed=seed,
            bootstrap_resamples=resamples,
        )
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    report = build_database_report(
        settings=settings,
        project_state=args.project_state,
        seed=args.bootstrap_seed,
        resamples=args.bootstrap_resamples,
    )
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
