from __future__ import annotations

import argparse
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine

from bp_engine.config import Settings
from bp_engine.prospective_evidence.repository import PostgresProspectiveEvidenceRepository
from bp_engine.prospective_evidence.service import build_prospective_evidence_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Emit a read-only Phase 14 prospective evidence report"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    report = subparsers.add_parser("report", help="emit prospective evidence JSON")
    report.add_argument("--env-file", default=None)
    report.add_argument("--database-url", default=None)
    report.add_argument("--project-state", default="PROJECT_STATE.json")
    report.add_argument("--bootstrap-seed", type=int, default=14)
    report.add_argument("--bootstrap-resamples", type=int, default=10_000)
    return parser


def _zero(value: object) -> bool:
    try:
        return Decimal(str(value)).is_zero()
    except (InvalidOperation, ValueError):
        return False


def ensure_money_disabled(settings: Settings) -> None:
    safe = (
        settings.live_trading_enabled is False
        and _zero(settings.max_trade_size_usd)
        and _zero(settings.max_daily_loss_usd)
    )
    if not safe:
        raise RuntimeError(
            "prospective evidence reporting requires money-disabled runtime interlocks: "
            "LIVE_TRADING_ENABLED=false, MAX_TRADE_SIZE_USD=0, MAX_DAILY_LOSS_USD=0"
        )


def load_master_live_gate(path: str | Path) -> dict[str, Any]:
    state_path = Path(path)
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("PROJECT_STATE root must be an object")
    checkpoint = payload.get("phase_14_checkpoint")
    if not isinstance(checkpoint, dict):
        raise ValueError("PROJECT_STATE phase_14_checkpoint must be an object")
    overall = checkpoint.get("overall_live_gate")
    master = checkpoint.get("master_live_gate")
    if not isinstance(overall, str):
        raise ValueError("phase_14_checkpoint.overall_live_gate must be a string")
    if not isinstance(master, dict):
        raise ValueError("phase_14_checkpoint.master_live_gate must be an object")
    return {"overall_live_gate": overall, "master_live_gate": dict(master)}


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    if args.database_url:
        settings = settings.model_copy(update={"database_url": args.database_url})
    return settings


def build_database_report(
    *,
    settings: Settings,
    project_state: str | Path,
    bootstrap_seed: int,
    bootstrap_resamples: int,
) -> dict[str, object]:
    ensure_money_disabled(settings)
    master_live_gate = load_master_live_gate(project_state)
    engine = create_engine(settings.database_url)
    try:
        repository = PostgresProspectiveEvidenceRepository(engine)
        return build_prospective_evidence_report(
            settlements=repository.list_settlements(),
            evaluations=repository.list_evaluations(),
            reconciliation=repository.get_reconciliation(),
            master_live_gate=master_live_gate,
            bootstrap_seed=bootstrap_seed,
            bootstrap_resamples=bootstrap_resamples,
        )
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "report":
        raise RuntimeError(f"unsupported command: {args.command}")
    settings = _settings(args)
    report = build_database_report(
        settings=settings,
        project_state=args.project_state,
        bootstrap_seed=args.bootstrap_seed,
        bootstrap_resamples=args.bootstrap_resamples,
    )
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
