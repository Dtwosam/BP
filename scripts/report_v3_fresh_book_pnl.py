from __future__ import annotations

import argparse
import glob
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, create_engine, select, text

from bp_engine.config import Settings
from bp_engine.storage import schema

OFFICIAL_LABEL_VERSION = "official-outcome-v1"
EVALUATED_EVENT = "fresh_book_shadow_evaluated"
QUOTE_UNAVAILABLE_EVENT = "fresh_book_shadow_quote_unavailable"
_ZERO = Decimal("0")


class FreshBookPnlReportError(RuntimeError):
    """Raised when V3 fresh-book paper evidence is malformed or ambiguous."""


@dataclass(frozen=True)
class PaperTrade:
    prediction_id: str
    condition_id: str
    selected_side: str
    filled_shares: Decimal
    total_fill_cost: Decimal
    semantic_sha256: str | None


def _decimal(value: object, name: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise FreshBookPnlReportError(f"{name} must be numeric")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise FreshBookPnlReportError(f"{name} must be numeric") from exc
    if not result.is_finite():
        raise FreshBookPnlReportError(f"{name} must be finite")
    return result


def _trade_from_record(record: dict[str, Any]) -> PaperTrade | None:
    if record.get("event") != EVALUATED_EVENT or record.get("trade") is not True:
        return None
    filled = _decimal(record.get("filled_shares"), "filled_shares")
    if filled <= _ZERO:
        return None
    cost = _decimal(record.get("total_fill_cost"), "total_fill_cost")
    if cost < _ZERO:
        raise FreshBookPnlReportError("total_fill_cost must be non-negative")
    prediction_id = str(record.get("prediction_id") or "")
    condition_id = str(record.get("condition_id") or "")
    selected_side = str(record.get("selected_side") or "").lower()
    if not prediction_id or not condition_id:
        raise FreshBookPnlReportError("trade identity missing")
    if selected_side not in {"up", "down"}:
        raise FreshBookPnlReportError("selected_side must be up or down")
    semantic = record.get("semantic_sha256")
    semantic_sha256 = None if semantic in (None, "") else str(semantic)
    return PaperTrade(
        prediction_id=prediction_id,
        condition_id=condition_id,
        selected_side=selected_side,
        filled_shares=filled,
        total_fill_cost=cost,
        semantic_sha256=semantic_sha256,
    )


def _same_trade(left: PaperTrade, right: PaperTrade) -> bool:
    return left == right


def load_epoch(path: Path) -> dict[str, Any]:
    evaluated = 0
    quote_unavailable = 0
    trades: dict[str, PaperTrade] = {}
    duplicate_prediction_count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            event = record.get("event")
            if event == EVALUATED_EVENT:
                evaluated += 1
            elif event == QUOTE_UNAVAILABLE_EVENT:
                quote_unavailable += 1
            trade = _trade_from_record(record)
            if trade is None:
                continue
            existing = trades.get(trade.prediction_id)
            if existing is None:
                trades[trade.prediction_id] = trade
                continue
            if not _same_trade(existing, trade):
                raise FreshBookPnlReportError(
                    f"conflicting duplicate prediction_id={trade.prediction_id} "
                    f"in {path.name}:{line_number}"
                )
            duplicate_prediction_count += 1
    return {
        "file": path.name,
        "evaluated": evaluated,
        "quote_unavailable": quote_unavailable,
        "trades": trades,
        "duplicate_prediction_count": duplicate_prediction_count,
    }


def _official_outcomes(
    connection: Connection,
    condition_ids: tuple[str, ...],
) -> dict[str, str]:
    if not condition_ids:
        return {}
    rows = connection.execute(
        select(
            schema.market_labels.c.condition_id,
            schema.market_labels.c.official_outcome,
        ).where(
            schema.market_labels.c.condition_id.in_(condition_ids),
            schema.market_labels.c.label_version == OFFICIAL_LABEL_VERSION,
        )
    ).all()
    outcomes: dict[str, str] = {}
    for condition_id, raw_outcome in rows:
        outcome = str(raw_outcome)
        if outcome not in {"Up", "Down"}:
            raise FreshBookPnlReportError(
                f"invalid official outcome for condition_id={condition_id}"
            )
        key = str(condition_id)
        existing = outcomes.get(key)
        if existing is not None and existing != outcome:
            raise FreshBookPnlReportError(
                f"conflicting official labels for condition_id={key}"
            )
        outcomes[key] = outcome
    return outcomes


def _metrics(
    trades: dict[str, PaperTrade],
    outcomes: dict[str, str],
) -> dict[str, Any]:
    settled = 0
    pending = 0
    wins = 0
    losses = 0
    realized_pnl = _ZERO
    settled_fill_cost = _ZERO
    gross_profit = _ZERO
    gross_loss = _ZERO

    for trade in trades.values():
        outcome = outcomes.get(trade.condition_id)
        if outcome is None:
            pending += 1
            continue
        settled += 1
        payout = (
            trade.filled_shares
            if trade.selected_side == outcome.lower()
            else _ZERO
        )
        pnl = payout - trade.total_fill_cost
        realized_pnl += pnl
        settled_fill_cost += trade.total_fill_cost
        if pnl > _ZERO:
            wins += 1
            gross_profit += pnl
        elif pnl < _ZERO:
            losses += 1
            gross_loss += -pnl

    return {
        "paper_trades": len(trades),
        "settled": settled,
        "pending": pending,
        "wins": wins,
        "losses": losses,
        "win_rate": (
            None if settled == 0 else str(Decimal(wins) / Decimal(settled))
        ),
        "realized_pnl_usd": str(realized_pnl),
        "settled_fill_cost_usd": str(settled_fill_cost),
        "return_on_cost": (
            None
            if settled_fill_cost == _ZERO
            else str(realized_pnl / settled_fill_cost)
        ),
        "profit_factor": (
            None if gross_loss == _ZERO else str(gross_profit / gross_loss)
        ),
    }


def build_report(
    connection: Connection,
    evidence_files: tuple[Path, ...],
) -> dict[str, Any]:
    epochs = [load_epoch(path) for path in evidence_files]
    condition_ids = tuple(
        sorted(
            {
                trade.condition_id
                for epoch in epochs
                for trade in epoch["trades"].values()
            }
        )
    )
    outcomes = _official_outcomes(connection, condition_ids)

    cumulative_trades: dict[str, PaperTrade] = {}
    duplicate_across_epochs = 0
    epoch_reports: list[dict[str, Any]] = []
    for epoch in epochs:
        for prediction_id, trade in epoch["trades"].items():
            existing = cumulative_trades.get(prediction_id)
            if existing is None:
                cumulative_trades[prediction_id] = trade
            elif _same_trade(existing, trade):
                duplicate_across_epochs += 1
            else:
                raise FreshBookPnlReportError(
                    f"conflicting prediction_id across epochs: {prediction_id}"
                )
        epoch_reports.append(
            {
                "file": epoch["file"],
                "evaluated": epoch["evaluated"],
                "quote_unavailable": epoch["quote_unavailable"],
                "duplicate_prediction_count": epoch["duplicate_prediction_count"],
                **_metrics(epoch["trades"], outcomes),
            }
        )

    cumulative = {
        "epoch_count": len(epochs),
        "evaluated": sum(epoch["evaluated"] for epoch in epochs),
        "quote_unavailable": sum(epoch["quote_unavailable"] for epoch in epochs),
        "duplicate_prediction_count_across_epochs": duplicate_across_epochs,
        **_metrics(cumulative_trades, outcomes),
    }
    return {
        "report": "v3_fresh_book_canonical_pnl_v1",
        "label_version": OFFICIAL_LABEL_VERSION,
        "settlement_source": "market_labels",
        "evidence_file_count": len(evidence_files),
        "epochs": epoch_reports,
        "cumulative": cumulative,
        "safety": {
            "database_read_only_required": True,
            "database_writes_performed": False,
            "service_mutation_performed": False,
            "order_submission_performed": False,
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Report V3 fresh-book paper P&L using immutable official-outcome-v1 "
            "market_labels only."
        )
    )
    parser.add_argument("--env-file", default="/etc/bp/bp.env")
    parser.add_argument(
        "--evidence-glob",
        default="/var/lib/bp/evidence/v3-fresh-book-shadow-*.jsonl",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    evidence_files = tuple(
        Path(path) for path in sorted(glob.glob(args.evidence_glob))
    )
    if not evidence_files:
        raise SystemExit("no V3 fresh-book evidence files found")

    settings = Settings(_env_file=args.env_file)
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
                raise SystemExit("database connection is not read-only")
            report = build_report(connection, evidence_files)
    finally:
        engine.dispose()

    print(json.dumps(report, sort_keys=True, indent=2))
    print("PHASE15_V3_FRESH_BOOK_PNL_STATUS=PASS")
    print("DATABASE_ACCESS=read_only")
    print("DATABASE_WRITES_PERFORMED=false")
    print("SERVICE_MUTATION_PERFORMED=false")
    print("ORDER_SUBMISSION_PERFORMED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
