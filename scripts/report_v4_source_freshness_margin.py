from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from bp_engine.config import Settings, TradingMode
from bp_engine.features.v4_models import V4FeatureTarget
from bp_engine.v4_paper.inference import FROZEN_V4_OFFSET_SECONDS
from bp_engine.v4_paper.source_time_features import (
    MAX_FUTURE_SKEW_SECONDS,
    MAX_SOURCE_AGE_SECONDS,
    V4SourceTimeReader,
    _bybit_price,
    _coinbase_price,
    probe_core_source_time_v4_readiness,
)

# Strictly a historical, read-only audit. A post-hoc row's received_at
# does not prove that its transaction was committed at the live decision.
DECISION_EVENTS = frozenset({
    "v4_fresh_book_shadow_source_ineligible",
    "v4_fresh_book_shadow_decision_missed",
    "v4_source_time_prediction",
})
VENUES = (
    ("coinbase", "coinbase", "spot", "BTC-USD"),
    ("bybit_spot", "bybit", "spot", "BTCUSDT"),
    ("bybit_linear", "bybit", "linear", "BTCUSDT"),
)
LOOKBACK_SECONDS = 600
ROW_LIMIT = 32

_PRICE_PREDICATE = {
    "bybit": """(
        (event_type = 'ticker' AND payload->'data' ? 'lastPrice')
        OR (event_type = 'trade'
            AND jsonb_typeof(payload->'data') = 'array'
            AND jsonb_array_length(payload->'data') > 0)
    )""",
    "coinbase": """(
        (event_type LIKE 'ticker_%'
            AND payload #>> '{events,0,tickers,0,price}' IS NOT NULL)
        OR (event_type LIKE 'market_trades_%'
            AND payload #>> '{events,0,trades,-1,price}' IS NOT NULL)
    )""",
}


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("decision timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def load_decisions(path: Path) -> tuple[list[dict[str, Any]], int]:
    if not path.is_file():
        raise SystemExit(f"evidence file missing: {path}")
    decisions: dict[str, dict[str, Any]] = {}
    seen_market_count: int | None = None
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # Non-JSON diagnostic log lines are possible.
            if not isinstance(record, dict):
                continue
            if record.get("event") == "v4_fresh_book_shadow_completed":
                seen_market_count = int(record["seen_market_count"])
            if record.get("event") not in DECISION_EVENTS:
                continue
            condition_id = record.get("condition_id")
            decision_at = record.get("decision_at")
            if not isinstance(condition_id, str) or not isinstance(decision_at, str):
                raise SystemExit("decision evidence missing condition_id/decision_at")
            normalized = _utc(decision_at).isoformat()
            existing = decisions.get(condition_id)
            if existing is not None:
                if existing["decision_at"] != normalized:
                    raise SystemExit("conflicting decision timestamp for condition")
                continue
            decisions[condition_id] = {
                "condition_id": condition_id,
                "decision_at": normalized,
                "original_event": record["event"],
                "original_missing_flags": record.get("missing_flags", {}),
                "original_ineligible_reasons": record.get("source_ineligible_reasons", []),
            }
    if seen_market_count is None or not decisions:
        raise SystemExit("incomplete shadow evidence: completion/decisions missing")
    if len(decisions) != seen_market_count:
        raise SystemExit(
            f"incomplete evidence: {len(decisions)} decisions, "
            f"{seen_market_count} markets reported"
        )
    return sorted(decisions.values(), key=lambda x: x["decision_at"]), seen_market_count


def nearest_price_bearing_event(
    connection,
    *,
    source: str,
    stream: str,
    instrument: str,
    cutoff: datetime,
) -> dict[str, Any]:
    # Predicate uses exact parser-supported price fields; NOT mark/index/bid/ask.
    statement = text(f"""
        SELECT id, source, stream, instrument, event_type,
               source_timestamp, received_at, payload
        FROM raw_market_events
        WHERE source = :source
          AND stream = :stream
          AND instrument = :instrument
          AND source_timestamp IS NOT NULL
          AND received_at >= :lower
          AND received_at <= :cutoff
          AND source_timestamp <= :source_upper
          AND {_PRICE_PREDICATE[source]}
        ORDER BY received_at DESC, id DESC
        LIMIT :row_limit
    """)
    rows = connection.execute(
        statement,
        {
            "source": source,
            "stream": stream,
            "instrument": instrument,
            "lower": cutoff - timedelta(seconds=LOOKBACK_SECONDS),
            "cutoff": cutoff,
            "source_upper": cutoff + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS),
            "row_limit": ROW_LIMIT,
        },
    ).mappings().all()
    valid: list[dict[str, Any]] = []
    parse_price = _coinbase_price if source == "coinbase" else _bybit_price
    for row in rows:
        item = dict(row)
        if parse_price(item) is None:
            continue
        source_at = item["source_timestamp"].astimezone(UTC)
        received_at = item["received_at"].astimezone(UTC)
        age = (cutoff - source_at).total_seconds()
        transport_lag = (received_at - source_at).total_seconds()
        valid.append({
            "row_id": int(item["id"]),
            "event_type": item["event_type"],
            "source_at": source_at.isoformat(),
            "received_at": received_at.isoformat(),
            "source_age_seconds": round(age, 6),
            "received_age_seconds": round((cutoff - received_at).total_seconds(), 6),
            "source_freshness_margin_seconds": round(MAX_SOURCE_AGE_SECONDS - age, 6),
            "passes_timestamp_policy": (
                -MAX_FUTURE_SKEW_SECONDS <= age <= MAX_SOURCE_AGE_SECONDS
                and transport_lag >= -MAX_FUTURE_SKEW_SECONDS
                and received_at <= cutoff
            ),
        })
    # Closest source-time price, regardless of how many newer no-price
    # partial updates existed. Report truncation to avoid claiming exhaustiveness.
    best = min(
        valid,
        key=lambda r: (
            abs(r["source_age_seconds"]),
            -datetime.fromisoformat(r["received_at"]).timestamp(),
            -r["row_id"],
        ),
        default=None,
    )
    return {
        "nearest_valid_price_event": best,
        "price_candidates_checked": len(rows),
        "candidate_limit_reached": len(rows) == ROW_LIMIT,
        "lookback_seconds": LOOKBACK_SECONDS,
        "price_values_disclosed": False,
    }


def build_report(connection, evidence_path: Path) -> dict[str, Any]:
    if connection.execute(
        text("SHOW default_transaction_read_only")
    ).scalar_one() != "on":
        raise SystemExit("database connection is not read-only")
    decisions, seen_market_count = load_decisions(evidence_path)
    reader = V4SourceTimeReader()
    results: list[dict[str, Any]] = []
    for record in decisions:
        cutoff = _utc(record["decision_at"])
        start = cutoff - timedelta(seconds=FROZEN_V4_OFFSET_SECONDS)
        target = V4FeatureTarget(
            condition_id=record["condition_id"],
            slug="historical-freshness-audit",
            horizon_seconds=300,
            market_start_at=start,
            market_end_at=start + timedelta(seconds=300),
        )
        readiness = probe_core_source_time_v4_readiness(
            connection, target, decision_at=cutoff, reader=reader
        )
        anchors: list[dict[str, Any]] = []
        original_flags = record["original_missing_flags"]
        for prefix, source, stream, instrument in VENUES:
            for anchor, stamp in (("market_start", start), ("current", cutoff)):
                key = f"{prefix}_{anchor}_missing"
                missing = bool(readiness.missing_flags[key])
                originally_missing = bool(original_flags.get(key, False))
                item: dict[str, Any] = {
                    "venue": prefix,
                    "anchor": anchor,
                    "replay_missing": missing,
                    "original_missing": originally_missing,
                    "replay_stale": bool(readiness.missing_flags[f"{prefix}_{anchor}_stale"]),
                }
                if missing or originally_missing:
                    item.update(
                        nearest_price_bearing_event(
                            connection,
                            source=source,
                            stream=stream,
                            instrument=instrument,
                            cutoff=stamp,
                        )
                    )
                anchors.append(item)
        results.append({
            "condition_id": record["condition_id"],
            "decision_at": record["decision_at"],
            "original_event": record["original_event"],
            "original_ineligible_reasons": record["original_ineligible_reasons"],
            "replay_core_source_ready": readiness.core_source_ready,
            "replay_ineligible_reasons": list(readiness.core_source_ineligible_reasons()),
            "anchors": anchors,
        })
    return {
        "report": "v4_fresh_book_source_freshness_margin_v1",
        "evidence_file": evidence_path.name,
        "observed_market_count": seen_market_count,
        "audited_decision_count": len(results),
        "max_source_age_seconds": MAX_SOURCE_AGE_SECONDS,
        "max_future_skew_seconds": MAX_FUTURE_SKEW_SECONDS,
        "results": results,
        "limitations": [
            "Historical received_at does not prove database commit visibility at decision time.",
            "Nearest price-bearing rows are sampled with a declared limit; truncated samples are not exhaustive.",
            "This is not a live timing or trading replay.",
        ],
        "safety": {
            "database_read_only_required": True,
            "database_writes_performed": False,
            "service_mutation_performed": False,
            "order_submission_performed": False,
            "holdout_labels_read": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only V4 historical core-source freshness audit.")
    parser.add_argument("--env-file", default="/etc/bp/bp.env")
    parser.add_argument("--evidence-file", required=True)
    args = parser.parse_args()
    settings = Settings(_env_file=args.env_file)
    if settings.mode is not TradingMode.RESEARCH:
        raise SystemExit("MODE must be research")
    if settings.live_trading_enabled:
        raise SystemExit("LIVE_TRADING_ENABLED must be false")
    if settings.max_trade_size_usd != 0 or settings.max_daily_loss_usd != 0:
        raise SystemExit("risk limits must be zero")
    if settings.recorder_batch_size != 100:
        raise SystemExit("RECORDER_BATCH_SIZE must be 100")
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={
            "options": (
                "-c default_transaction_read_only=on "
                "-c statement_timeout=3000 "
                "-c application_name=bp-v4-source-freshness-audit"
            )
        },
    )
    try:
        with engine.connect() as connection:
            report = build_report(connection, Path(args.evidence_file))
    finally:
        engine.dispose()
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    print("PHASE14_V4_SOURCE_FRESHNESS_AUDIT_STATUS=PASS")
    print("DATABASE_ACCESS=read_only")
    print("DATABASE_WRITES_PERFORMED=false")
    print("SERVICE_MUTATION_PERFORMED=false")
    print("ORDER_SUBMISSION_PERFORMED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
