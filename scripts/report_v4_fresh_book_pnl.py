from __future__ import annotations

import argparse
import glob
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median
from typing import Any

from sqlalchemy import Connection, create_engine, select, text

from bp_engine.config import Settings
from bp_engine.storage import schema
from bp_engine.v4_paper.inference import FROZEN_V4_OFFSET_SECONDS
from bp_engine.v4_paper.source_time_features import V4_CORE_SOURCE_REQUIRED_FLAGS

OFFICIAL_LABEL_VERSION = "official-outcome-v1"
EVALUATED_EVENT = "v4_fresh_book_shadow_evaluated"
QUOTE_UNAVAILABLE_EVENT = "v4_fresh_book_shadow_quote_unavailable"
SOURCE_INELIGIBLE_EVENT = "v4_fresh_book_shadow_source_ineligible"
EXTREME_EDGE_THRESHOLD = Decimal("0.50")
SOURCE_TIMING_SEARCH_SECONDS = 10.0
_SOURCE_SPECS = {
    "coinbase": ("coinbase", "spot", "BTC-USD"),
    "bybit_spot": ("bybit", "spot", "BTCUSDT"),
    "bybit_linear": ("bybit", "linear", "BTCUSDT"),
}
_ZERO = Decimal("0")


class V4FreshBookPnlReportError(RuntimeError):
    """Raised when V4 paper evidence is malformed or ambiguous."""


@dataclass(frozen=True)
class PaperTrade:
    prediction_id: str
    condition_id: str
    selected_side: str
    filled_shares: Decimal
    total_fill_cost: Decimal
    cost_adjusted_edge: Decimal | None
    extreme_edge_observation: bool
    semantic_sha256: str | None


def _decimal(value: object, name: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise V4FreshBookPnlReportError(f"{name} must be numeric")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise V4FreshBookPnlReportError(f"{name} must be numeric") from exc
    if not result.is_finite():
        raise V4FreshBookPnlReportError(f"{name} must be finite")
    return result


def _source_reason_dimensions(reason: str) -> tuple[str, str, str]:
    if reason not in V4_CORE_SOURCE_REQUIRED_FLAGS:
        raise V4FreshBookPnlReportError(
            f"unexpected source-ineligible reason: {reason}"
        )
    failure_type = "missing" if reason.endswith("_missing") else "stale"
    stem = reason.removesuffix(f"_{failure_type}")
    if stem.startswith("coinbase_"):
        venue = "coinbase"
        anchor = stem.removeprefix("coinbase_")
    elif stem.startswith("bybit_spot_"):
        venue = "bybit_spot"
        anchor = stem.removeprefix("bybit_spot_")
    elif stem.startswith("bybit_linear_"):
        venue = "bybit_linear"
        anchor = stem.removeprefix("bybit_linear_")
    else:
        raise V4FreshBookPnlReportError(
            f"unrecognized source-ineligible reason: {reason}"
        )
    return venue, anchor, failure_type


def _db_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_iso_utc(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise V4FreshBookPnlReportError(f"{name} must be an ISO timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise V4FreshBookPnlReportError(f"{name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _positive_decimal(value: object) -> Decimal | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite() or parsed <= _ZERO:
        return None
    return parsed


def _raw_row_has_price(row: dict[str, Any]) -> bool:
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return False
    source = str(row.get("source") or "")
    event_type = str(row.get("event_type") or "")
    if source == "coinbase":
        events = payload.get("events")
        if not isinstance(events, list) or not events or not isinstance(events[0], dict):
            return False
        first = events[0]
        if event_type.startswith("ticker_"):
            tickers = first.get("tickers")
            return bool(
                isinstance(tickers, list)
                and tickers
                and isinstance(tickers[0], dict)
                and _positive_decimal(tickers[0].get("price")) is not None
            )
        if event_type.startswith("market_trades_"):
            trades = first.get("trades")
            return bool(
                isinstance(trades, list)
                and trades
                and isinstance(trades[-1], dict)
                and _positive_decimal(trades[-1].get("price")) is not None
            )
        return False
    if source == "bybit":
        data = payload.get("data")
        if event_type == "ticker" and isinstance(data, dict):
            return _positive_decimal(data.get("lastPrice")) is not None
        if event_type == "trade" and isinstance(data, list) and data:
            trade = data[-1]
            return bool(
                isinstance(trade, dict)
                and _positive_decimal(trade.get("p")) is not None
            )
    return False


def _timing_row_details(
    row: dict[str, Any],
    requested_at: datetime,
) -> dict[str, Any]:
    source_at = _db_utc(row["source_timestamp"])
    received_at = _db_utc(row["received_at"])
    source_delta = (source_at - requested_at).total_seconds()
    received_delta = (received_at - requested_at).total_seconds()
    transport_lag = (received_at - source_at).total_seconds()
    in_source_window = -2.0 <= source_delta <= 1.0
    metadata_eligible = (
        in_source_window
        and transport_lag >= -1.0
        and received_delta <= 0.0
    )
    return {
        "row_id": int(row["id"]),
        "event_type": str(row["event_type"]),
        "source_at": source_at.isoformat(),
        "received_at": received_at.isoformat(),
        "source_delta_seconds": source_delta,
        "received_delta_seconds": received_delta,
        "transport_lag_seconds": transport_lag,
        "in_source_window": in_source_window,
        "metadata_eligible": metadata_eligible,
    }


def _classify_timing_rows(
    rows: list[dict[str, Any]],
    requested_at: datetime,
) -> dict[str, Any]:
    usable = [row for row in rows if _raw_row_has_price(row)]
    if not usable:
        return {
            "classification": "no_usable_event_within_10s",
            "nearest": None,
        }

    details = [_timing_row_details(row, requested_at) for row in usable]
    nearest = min(
        details,
        key=lambda item: (
            abs(float(item["source_delta_seconds"])),
            abs(float(item["received_delta_seconds"])),
            int(item["row_id"]),
        ),
    )
    if any(bool(item["metadata_eligible"]) for item in details):
        classification = "eventually_metadata_eligible"
    elif any(
        bool(item["in_source_window"])
        and float(item["received_delta_seconds"]) > 0.0
        and float(item["transport_lag_seconds"]) >= -1.0
        for item in details
    ):
        classification = "late_received_after_cutoff"
    elif any(bool(item["in_source_window"]) for item in details):
        classification = "source_window_rejected_by_transport"
    else:
        classification = "no_usable_event_in_source_window"
    return {
        "classification": classification,
        "nearest": nearest,
    }


def _nearest_timing_rows(
    connection: Connection,
    *,
    venue: str,
    requested_at: datetime,
) -> list[dict[str, Any]]:
    source, stream, instrument = _SOURCE_SPECS[venue]
    window = timedelta(seconds=SOURCE_TIMING_SEARCH_SECONDS)
    statement = select(schema.raw_market_events).where(
        schema.raw_market_events.c.source == source,
        schema.raw_market_events.c.stream == stream,
        schema.raw_market_events.c.instrument == instrument,
        schema.raw_market_events.c.source_timestamp.is_not(None),
        schema.raw_market_events.c.source_timestamp >= requested_at - window,
        schema.raw_market_events.c.source_timestamp <= requested_at + window,
    )
    if source == "coinbase":
        statement = statement.where(
            schema.raw_market_events.c.event_type.like("ticker_%")
            | schema.raw_market_events.c.event_type.like("market_trades_%")
        )
    else:
        statement = statement.where(
            schema.raw_market_events.c.event_type.in_(("ticker", "trade"))
        )
    return [dict(row) for row in connection.execute(statement).mappings()]


def _distribution(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    p95_index = max(0, min(len(ordered) - 1, int(0.95 * len(ordered) + 0.999999) - 1))
    return {
        "min": ordered[0],
        "median": float(median(ordered)),
        "p95": ordered[p95_index],
        "max": ordered[-1],
    }


def _source_timing_diagnostic(
    connection: Connection,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    by_reason: dict[str, dict[str, Any]] = {}
    cache: dict[tuple[str, datetime], dict[str, Any]] = {}

    for record in records:
        decision_at = _parse_iso_utc(record.get("decision_at"), "decision_at")
        raw_reasons = record.get("source_ineligible_reasons")
        if not isinstance(raw_reasons, list):
            continue
        for raw_reason in raw_reasons:
            reason = str(raw_reason)
            venue, anchor, failure_type = _source_reason_dimensions(reason)
            if failure_type != "missing":
                continue
            requested_at = (
                decision_at
                if anchor == "current"
                else decision_at - timedelta(seconds=FROZEN_V4_OFFSET_SECONDS)
            )
            key = (venue, requested_at)
            diagnostic = cache.get(key)
            if diagnostic is None:
                rows = _nearest_timing_rows(
                    connection,
                    venue=venue,
                    requested_at=requested_at,
                )
                diagnostic = _classify_timing_rows(rows, requested_at)
                cache[key] = diagnostic

            bucket = by_reason.setdefault(
                reason,
                {
                    "missing_count": 0,
                    "classification_counts": Counter(),
                    "nearest_source_delta_seconds": [],
                    "nearest_received_delta_seconds": [],
                    "nearest_transport_lag_seconds": [],
                },
            )
            bucket["missing_count"] += 1
            bucket["classification_counts"][diagnostic["classification"]] += 1
            nearest = diagnostic["nearest"]
            if nearest is not None:
                bucket["nearest_source_delta_seconds"].append(
                    float(nearest["source_delta_seconds"])
                )
                bucket["nearest_received_delta_seconds"].append(
                    float(nearest["received_delta_seconds"])
                )
                bucket["nearest_transport_lag_seconds"].append(
                    float(nearest["transport_lag_seconds"])
                )

    rendered: dict[str, Any] = {}
    for reason, bucket in sorted(by_reason.items()):
        rendered[reason] = {
            "missing_count": bucket["missing_count"],
            "classification_counts": dict(
                sorted(bucket["classification_counts"].items())
            ),
            "nearest_source_delta_seconds": _distribution(
                bucket["nearest_source_delta_seconds"]
            ),
            "nearest_received_delta_seconds": _distribution(
                bucket["nearest_received_delta_seconds"]
            ),
            "nearest_transport_lag_seconds": _distribution(
                bucket["nearest_transport_lag_seconds"]
            ),
        }
    return {
        "search_window_seconds": SOURCE_TIMING_SEARCH_SECONDS,
        "frozen_offset_seconds": FROZEN_V4_OFFSET_SECONDS,
        "by_reason": rendered,
    }


def _source_ineligible_summary(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    reason_counts: Counter[str] = Counter()
    combination_counts: Counter[str] = Counter()
    venue_counts: Counter[str] = Counter()
    anchor_counts: Counter[str] = Counter()
    failure_type_counts: Counter[str] = Counter()
    sample_condition_ids: dict[str, list[str]] = {}

    for record in records:
        raw_reasons = record.get("source_ineligible_reasons")
        if not isinstance(raw_reasons, list) or not raw_reasons:
            raise V4FreshBookPnlReportError(
                "source-ineligible record missing reasons"
            )
        reasons = tuple(sorted(str(reason) for reason in raw_reasons))
        combination_counts[" + ".join(reasons)] += 1
        condition_id = str(record.get("condition_id") or "")
        for reason in reasons:
            venue, anchor, failure_type = _source_reason_dimensions(reason)
            reason_counts[reason] += 1
            venue_counts[venue] += 1
            anchor_counts[anchor] += 1
            failure_type_counts[failure_type] += 1
            samples = sample_condition_ids.setdefault(reason, [])
            if condition_id and condition_id not in samples and len(samples) < 3:
                samples.append(condition_id)

    return {
        "record_count": len(records),
        "reason_counts": dict(sorted(reason_counts.items())),
        "venue_counts": dict(sorted(venue_counts.items())),
        "anchor_counts": dict(sorted(anchor_counts.items())),
        "failure_type_counts": dict(sorted(failure_type_counts.items())),
        "reason_combination_counts": dict(
            sorted(
                combination_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        "sample_condition_ids_by_reason": dict(sorted(sample_condition_ids.items())),
    }


def _trade_from_record(record: dict[str, Any]) -> PaperTrade | None:
    if record.get("event") != EVALUATED_EVENT or record.get("trade") is not True:
        return None
    filled = _decimal(record.get("filled_shares"), "filled_shares")
    if filled <= _ZERO:
        return None
    cost = _decimal(record.get("total_fill_cost"), "total_fill_cost")
    if cost < _ZERO:
        raise V4FreshBookPnlReportError("total_fill_cost must be non-negative")
    prediction_id = str(record.get("prediction_id") or "")
    condition_id = str(record.get("condition_id") or "")
    selected_side = str(record.get("selected_side") or "").lower()
    if not prediction_id or not condition_id:
        raise V4FreshBookPnlReportError("trade identity missing")
    if selected_side not in {"up", "down"}:
        raise V4FreshBookPnlReportError("selected_side must be up or down")

    edge_raw = record.get("cost_adjusted_edge")
    edge = None if edge_raw in (None, "") else _decimal(edge_raw, "cost_adjusted_edge")
    extreme = bool(record.get("extreme_edge_observation", False))
    if edge is not None and (edge > EXTREME_EDGE_THRESHOLD) != extreme:
        raise V4FreshBookPnlReportError(
            f"extreme-edge flag mismatch for prediction_id={prediction_id}"
        )
    semantic = record.get("semantic_sha256")
    return PaperTrade(
        prediction_id=prediction_id,
        condition_id=condition_id,
        selected_side=selected_side,
        filled_shares=filled,
        total_fill_cost=cost,
        cost_adjusted_edge=edge,
        extreme_edge_observation=extreme,
        semantic_sha256=None if semantic in (None, "") else str(semantic),
    )


def load_epoch(path: Path) -> dict[str, Any]:
    evaluated = 0
    quote_unavailable = 0
    source_ineligible = 0
    source_ineligible_records: list[dict[str, Any]] = []
    trades: dict[str, PaperTrade] = {}
    duplicate_prediction_count = 0
    completed = False

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
            elif event == SOURCE_INELIGIBLE_EVENT:
                source_ineligible += 1
                source_ineligible_records.append(record)
            elif event == "v4_fresh_book_shadow_completed":
                completed = True

            trade = _trade_from_record(record)
            if trade is None:
                continue
            existing = trades.get(trade.prediction_id)
            if existing is None:
                trades[trade.prediction_id] = trade
                continue
            if existing != trade:
                raise V4FreshBookPnlReportError(
                    f"conflicting duplicate prediction_id={trade.prediction_id} "
                    f"in {path.name}:{line_number}"
                )
            duplicate_prediction_count += 1

    return {
        "file": path.name,
        "completed": completed,
        "evaluated": evaluated,
        "quote_unavailable": quote_unavailable,
        "source_ineligible": source_ineligible,
        "source_ineligible_records": source_ineligible_records,
        "source_ineligible_breakdown": _source_ineligible_summary(
            source_ineligible_records
        ),
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
            raise V4FreshBookPnlReportError(
                f"invalid official outcome for condition_id={condition_id}"
            )
        key = str(condition_id)
        existing = outcomes.get(key)
        if existing is not None and existing != outcome:
            raise V4FreshBookPnlReportError(
                f"conflicting official labels for condition_id={key}"
            )
        outcomes[key] = outcome
    return outcomes


def _metrics(
    trades: dict[str, PaperTrade],
    outcomes: dict[str, str],
) -> dict[str, Any]:
    settled = pending = wins = losses = 0
    realized_pnl = settled_fill_cost = gross_profit = gross_loss = _ZERO

    for trade in trades.values():
        outcome = outcomes.get(trade.condition_id)
        if outcome is None:
            pending += 1
            continue
        settled += 1
        payout = trade.filled_shares if trade.selected_side == outcome.lower() else _ZERO
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
        "win_rate": None if settled == 0 else str(Decimal(wins) / Decimal(settled)),
        "realized_pnl_usd": str(realized_pnl),
        "settled_fill_cost_usd": str(settled_fill_cost),
        "return_on_cost": (
            None if settled_fill_cost == _ZERO else str(realized_pnl / settled_fill_cost)
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
    condition_ids = tuple(sorted({
        trade.condition_id
        for epoch in epochs
        for trade in epoch["trades"].values()
    }))
    outcomes = _official_outcomes(connection, condition_ids)

    all_trades: dict[str, PaperTrade] = {}
    duplicate_across_epochs = 0
    epoch_reports: list[dict[str, Any]] = []
    for epoch in epochs:
        for prediction_id, trade in epoch["trades"].items():
            existing = all_trades.get(prediction_id)
            if existing is None:
                all_trades[prediction_id] = trade
            elif existing == trade:
                duplicate_across_epochs += 1
            else:
                raise V4FreshBookPnlReportError(
                    f"conflicting prediction_id across epochs: {prediction_id}"
                )
        epoch_reports.append({
            "file": epoch["file"],
            "completed": epoch["completed"],
            "evaluated": epoch["evaluated"],
            "quote_unavailable": epoch["quote_unavailable"],
            "source_ineligible": epoch["source_ineligible"],
            "source_ineligible_breakdown": epoch["source_ineligible_breakdown"],
            "duplicate_prediction_count": epoch["duplicate_prediction_count"],
            **_metrics(epoch["trades"], outcomes),
        })

    extreme = {
        key: trade
        for key, trade in all_trades.items()
        if trade.extreme_edge_observation
    }
    non_extreme = {
        key: trade
        for key, trade in all_trades.items()
        if not trade.extreme_edge_observation
    }

    all_source_ineligible_records = [
        record
        for epoch in epochs
        for record in epoch["source_ineligible_records"]
    ]

    return {
        "report": "v4_fresh_book_canonical_pnl_v1",
        "label_version": OFFICIAL_LABEL_VERSION,
        "settlement_source": "market_labels",
        "evidence_file_count": len(evidence_files),
        "epochs": epoch_reports,
        "cumulative": {
            "epoch_count": len(epochs),
            "completed_epoch_count": sum(bool(epoch["completed"]) for epoch in epochs),
            "evaluated": sum(epoch["evaluated"] for epoch in epochs),
            "quote_unavailable": sum(epoch["quote_unavailable"] for epoch in epochs),
            "source_ineligible": sum(epoch["source_ineligible"] for epoch in epochs),
            "source_ineligible_breakdown": _source_ineligible_summary(
                all_source_ineligible_records
            ),
            "source_timing_diagnostic": _source_timing_diagnostic(
                connection,
                all_source_ineligible_records,
            ),
            "duplicate_prediction_count_across_epochs": duplicate_across_epochs,
            **_metrics(all_trades, outcomes),
        },
        "edge_cohorts": {
            "edge_gt_0_50": _metrics(extreme, outcomes),
            "edge_le_0_50": _metrics(non_extreme, outcomes),
        },
        "safety": {
            "database_read_only_required": True,
            "database_writes_performed": False,
            "service_mutation_performed": False,
            "order_submission_performed": False,
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report V4 fresh-book paper P&L using official labels only."
    )
    parser.add_argument("--env-file", default="/etc/bp/bp.env")
    parser.add_argument(
        "--evidence-glob",
        default="/var/lib/bp/evidence/v4-fresh-book-shadow-*.jsonl",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    evidence_files = tuple(Path(path) for path in sorted(glob.glob(args.evidence_glob)))
    if not evidence_files:
        raise SystemExit("no V4 fresh-book evidence files found")

    settings = Settings(_env_file=args.env_file)
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"options": "-c default_transaction_read_only=on"},
    )
    try:
        with engine.connect() as connection:
            if connection.execute(text("SHOW default_transaction_read_only")).scalar_one() != "on":
                raise SystemExit("database connection is not read-only")
            report = build_report(connection, evidence_files)
    finally:
        engine.dispose()

    print(json.dumps(report, sort_keys=True, indent=2))
    print("PHASE14_V4_FRESH_BOOK_PNL_STATUS=PASS")
    print("DATABASE_ACCESS=read_only")
    print("DATABASE_WRITES_PERFORMED=false")
    print("SERVICE_MUTATION_PERFORMED=false")
    print("ORDER_SUBMISSION_PERFORMED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
