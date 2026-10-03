from __future__ import annotations

import argparse
import glob
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, create_engine, select, text

from bp_engine.config import Settings
from bp_engine.storage import schema

OFFICIAL_LABEL_VERSION = "official-outcome-v1"
EVALUATED_EVENT = "fresh_book_shadow_evaluated"
_ZERO = Decimal("0")


class V3TradeLedgerError(RuntimeError):
    """Raised when V3 fresh-book trade evidence is malformed."""


@dataclass(frozen=True)
class Label:
    outcome: str
    market_start_at: datetime
    market_end_at: datetime


def _decimal(value: object, name: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise V3TradeLedgerError(f"{name} must be numeric")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise V3TradeLedgerError(f"{name} must be numeric") from exc
    if not result.is_finite():
        raise V3TradeLedgerError(f"{name} must be finite")
    return result


def _utc(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise V3TradeLedgerError(f"{name} must be an ISO datetime string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise V3TradeLedgerError(f"{name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _load_trade_records(path: Path) -> list[dict[str, Any]]:
    trades: dict[str, dict[str, Any]] = {}
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
            if record.get("event") != EVALUATED_EVENT:
                continue
            if record.get("trade") is not True:
                continue
            filled = _decimal(record.get("filled_shares"), "filled_shares")
            if filled <= _ZERO:
                continue
            prediction_id = str(record.get("prediction_id") or "")
            if not prediction_id:
                raise V3TradeLedgerError(
                    f"prediction_id missing in {path.name}:{line_number}"
                )
            existing = trades.get(prediction_id)
            if existing is None:
                trades[prediction_id] = record
            elif existing != record:
                raise V3TradeLedgerError(
                    f"conflicting prediction_id={prediction_id} in {path.name}"
                )
    return list(trades.values())


def _labels(
    connection: Connection,
    condition_ids: tuple[str, ...],
) -> dict[str, Label]:
    if not condition_ids:
        return {}
    rows = connection.execute(
        select(
            schema.market_labels.c.condition_id,
            schema.market_labels.c.official_outcome,
            schema.market_labels.c.market_start_at,
            schema.market_labels.c.market_end_at,
        ).where(
            schema.market_labels.c.condition_id.in_(condition_ids),
            schema.market_labels.c.label_version == OFFICIAL_LABEL_VERSION,
        )
    ).all()
    labels: dict[str, Label] = {}
    for condition_id, outcome, start_at, end_at in rows:
        if outcome not in {"Up", "Down"}:
            raise V3TradeLedgerError(
                f"invalid official outcome for condition_id={condition_id}"
            )
        key = str(condition_id)
        label = Label(
            outcome=str(outcome),
            market_start_at=_stored_utc(start_at),
            market_end_at=_stored_utc(end_at),
        )
        existing = labels.get(key)
        if existing is not None and existing != label:
            raise V3TradeLedgerError(
                f"conflicting labels for condition_id={condition_id}"
            )
        labels[key] = label
    return labels


def _depth_metrics(
    record: dict[str, Any],
    *,
    limit_price: Decimal,
) -> tuple[Decimal, Decimal]:
    raw_levels = record.get("displayed_ask_levels")
    if not isinstance(raw_levels, list):
        raise V3TradeLedgerError("displayed_ask_levels must be a list")
    top_depth = _ZERO
    executable_depth = _ZERO
    for raw in raw_levels:
        if not isinstance(raw, list) or len(raw) != 2:
            raise V3TradeLedgerError("displayed ask level must be [price, size]")
        price = _decimal(raw[0], "ask level price")
        size = _decimal(raw[1], "ask level size")
        if top_depth == _ZERO:
            top_depth = size
        if price <= limit_price:
            executable_depth += size
    return top_depth, executable_depth


def build_trade_ledger(
    connection: Connection,
    evidence_files: tuple[Path, ...],
) -> list[dict[str, Any]]:
    epoch_records: list[tuple[int, str, dict[str, Any]]] = []
    for epoch_index, path in enumerate(evidence_files, start=1):
        for record in _load_trade_records(path):
            epoch_records.append((epoch_index, path.name, record))

    condition_ids = tuple(
        sorted({str(record["condition_id"]) for _, _, record in epoch_records})
    )
    labels = _labels(connection, condition_ids)

    ledger: list[dict[str, Any]] = []
    seen_predictions: dict[str, dict[str, Any]] = {}
    for epoch_index, filename, record in epoch_records:
        prediction_id = str(record["prediction_id"])
        condition_id = str(record["condition_id"])
        label = labels.get(condition_id)
        if label is None:
            continue

        side = str(record.get("selected_side") or "").lower()
        if side not in {"up", "down"}:
            raise V3TradeLedgerError("selected_side must be up or down")

        probability_up = _decimal(
            record.get("calibrated_probability_up"),
            "calibrated_probability_up",
        )
        side_probability = _decimal(record.get("side_probability"), "side_probability")
        best_ask = _decimal(record.get("best_ask"), "best_ask")
        limit_price = _decimal(record.get("limit_price"), "limit_price")
        raw_edge = _decimal(record.get("raw_edge"), "raw_edge")
        adjusted_edge = _decimal(
            record.get("cost_adjusted_edge"),
            "cost_adjusted_edge",
        )
        filled_shares = _decimal(record.get("filled_shares"), "filled_shares")
        requested_shares = _decimal(
            record.get("requested_shares"),
            "requested_shares",
        )
        gross_fill_cost = _decimal(
            record.get("gross_fill_cost"),
            "gross_fill_cost",
        )
        total_fees = _decimal(record.get("total_fees"), "total_fees")
        total_fill_cost = _decimal(
            record.get("total_fill_cost"),
            "total_fill_cost",
        )

        correct = side == label.outcome.lower()
        payout = filled_shares if correct else _ZERO
        pnl = payout - total_fill_cost
        avg_fill_price = gross_fill_cost / filled_shares
        fill_ratio = (
            _ZERO if requested_shares == _ZERO else filled_shares / requested_shares
        )
        fee_per_share = total_fees / filled_shares
        slippage_from_best = avg_fill_price - best_ask
        top_depth, executable_depth = _depth_metrics(
            record,
            limit_price=limit_price,
        )

        recorded_at = _utc(
            record.get("prediction_recorded_at"),
            "prediction_recorded_at",
        )
        quote_at = _utc(record.get("quote_observed_at"), "quote_observed_at")
        quote_delay_ms = int((quote_at - recorded_at).total_seconds() * 1000)

        row = {
            "epoch": epoch_index,
            "evidence_file": filename,
            "prediction_id": prediction_id,
            "condition_id": condition_id,
            "market_start_at": label.market_start_at.isoformat(),
            "market_end_at": label.market_end_at.isoformat(),
            "prediction_recorded_at": recorded_at.isoformat(),
            "quote_observed_at": quote_at.isoformat(),
            "quote_delay_ms": quote_delay_ms,
            "selected_side": side,
            "official_outcome": label.outcome.lower(),
            "correct": correct,
            "probability_up": str(probability_up),
            "side_probability": str(side_probability),
            "best_ask": str(best_ask),
            "limit_price": str(limit_price),
            "raw_edge": str(raw_edge),
            "cost_adjusted_edge": str(adjusted_edge),
            "requested_shares": str(requested_shares),
            "filled_shares": str(filled_shares),
            "fill_ratio": str(fill_ratio),
            "full_fill": bool(record.get("full_fill")),
            "top_level_depth": str(top_depth),
            "executable_depth": str(executable_depth),
            "avg_fill_price": str(avg_fill_price),
            "slippage_from_best": str(slippage_from_best),
            "fee_per_share": str(fee_per_share),
            "total_fees": str(total_fees),
            "total_fill_cost": str(total_fill_cost),
            "realized_pnl_usd": str(pnl),
        }

        existing = seen_predictions.get(prediction_id)
        if existing is not None:
            if existing != row:
                raise V3TradeLedgerError(
                    f"conflicting prediction across epochs: {prediction_id}"
                )
            continue
        seen_predictions[prediction_id] = row
        ledger.append(row)

    ledger.sort(
        key=lambda row: (
            row["market_start_at"],
            row["prediction_id"],
        )
    )
    for index, row in enumerate(ledger, start=1):
        row["trade_number"] = index
    return ledger


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Emit one canonical row per settled V3 fresh-book paper trade using "
            "official-outcome-v1 labels."
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
            ledger = build_trade_ledger(connection, evidence_files)
    finally:
        engine.dispose()

    print(
        json.dumps(
            {
                "report": "v3_fresh_book_trade_ledger_v1",
                "label_version": OFFICIAL_LABEL_VERSION,
                "settlement_source": "market_labels",
                "trade_count": len(ledger),
                "trades": ledger,
                "safety": {
                    "database_read_only_required": True,
                    "database_writes_performed": False,
                    "service_mutation_performed": False,
                    "order_submission_performed": False,
                },
            },
            sort_keys=True,
            indent=2,
        )
    )
    print("PHASE15_V3_FRESH_BOOK_TRADE_LEDGER=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
