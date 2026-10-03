from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from bp_engine.config import Settings
from bp_engine.v4_paper.inference import FROZEN_V4_OFFSET_SECONDS

_SOURCE_SPECS = {
    "coinbase": ("coinbase", "spot", "BTC-USD"),
    "bybit_spot": ("bybit", "spot", "BTCUSDT"),
    "bybit_linear": ("bybit", "linear", "BTCUSDT"),
}
_RECEIVED_WINDOW_SECONDS = 4.0


def _parse_iso_utc(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise SystemExit(f"{name} must be an ISO timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SystemExit(f"{name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _representative_decision_at(path: Path) -> datetime:
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            if record.get("event") != "v4_fresh_book_shadow_source_ineligible":
                continue
            return _parse_iso_utc(record.get("decision_at"), "decision_at")
    raise SystemExit("no source-ineligible decision found in evidence")


def _partition_name(value: datetime) -> str:
    utc = value.astimezone(UTC)
    return f"raw_market_events_{utc:%Y%m%d}_{utc:%H}"


def _relation_info(connection, name: str) -> dict[str, Any] | None:
    row = connection.execute(
        text(
            """
            SELECT
                relation.relkind,
                relation.reltuples::bigint AS estimated_rows,
                pg_total_relation_size(relation.oid) AS total_bytes
            FROM pg_class AS relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = current_schema()
              AND relation.relname = :name
            """
        ),
        {"name": name},
    ).mappings().one_or_none()
    return None if row is None else dict(row)


def _indexes(connection, table_name: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        text(
            """
            SELECT
                indexes.indexname,
                indexes.indexdef,
                pg_index.indisvalid,
                pg_index.indisready
            FROM pg_indexes AS indexes
            JOIN pg_class AS table_relation
              ON table_relation.relname = indexes.tablename
            JOIN pg_namespace AS table_namespace
              ON table_namespace.oid = table_relation.relnamespace
             AND table_namespace.nspname = indexes.schemaname
            JOIN pg_class AS index_relation
              ON index_relation.relname = indexes.indexname
             AND index_relation.relnamespace = table_namespace.oid
            JOIN pg_index ON pg_index.indexrelid = index_relation.oid
            WHERE indexes.schemaname = current_schema()
              AND indexes.tablename = :table_name
            ORDER BY indexes.indexname
            """
        ),
        {"table_name": table_name},
    ).mappings()
    return [dict(row) for row in rows]


def _attached_to_parent(connection, child_name: str) -> bool:
    return bool(
        connection.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_inherits
                    JOIN pg_class AS parent ON parent.oid = pg_inherits.inhparent
                    JOIN pg_class AS child ON child.oid = pg_inherits.inhrelid
                    JOIN pg_namespace AS namespace ON namespace.oid = parent.relnamespace
                    WHERE namespace.nspname = current_schema()
                      AND parent.relname = 'raw_market_events'
                      AND child.relname = :child_name
                )
                """
            ),
            {"child_name": child_name},
        ).scalar_one()
    )


def _explain(connection, *, venue: str, requested_at: datetime) -> dict[str, Any]:
    source, stream, instrument = _SOURCE_SPECS[venue]
    lower = requested_at - timedelta(seconds=_RECEIVED_WINDOW_SECONDS)
    try:
        plan = connection.execute(
            text(
                """
                EXPLAIN (FORMAT JSON, COSTS OFF)
                SELECT id, event_type, source_timestamp, received_at
                FROM raw_market_events
                WHERE source = :source
                  AND stream = :stream
                  AND instrument = :instrument
                  AND received_at >= :lower
                  AND received_at <= :upper
                ORDER BY received_at DESC, id DESC
                LIMIT 1
                """
            ),
            {
                "source": source,
                "stream": stream,
                "instrument": instrument,
                "lower": lower,
                "upper": requested_at,
            },
        ).scalar_one()
        return {"status": "ok", "plan": plan}
    except OperationalError as exc:
        connection.rollback()
        return {
            "status": "timeout" if "statement timeout" in str(exc).lower() else "failed",
            "error": str(exc).splitlines()[0],
        }


def build_report(connection, evidence_file: Path) -> dict[str, Any]:
    decision_at = _representative_decision_at(evidence_file)
    market_start_at = decision_at - timedelta(seconds=FROZEN_V4_OFFSET_SECONDS)
    cutoffs = {
        "market_start": market_start_at,
        "current": decision_at,
    }

    parent_indexes = _indexes(connection, "raw_market_events")
    partitions: dict[str, Any] = {}
    for label, cutoff in cutoffs.items():
        name = _partition_name(cutoff)
        partitions[label] = {
            "cutoff": cutoff.isoformat(),
            "partition": name,
            "relation": _relation_info(connection, name),
            "attached": _attached_to_parent(connection, name),
            "indexes": _indexes(connection, name),
        }

    return {
        "report": "v4_raw_query_path_v1",
        "evidence_file": evidence_file.name,
        "representative_decision_at": decision_at.isoformat(),
        "parent_relation": _relation_info(connection, "raw_market_events"),
        "parent_indexes": parent_indexes,
        "partitions": partitions,
        "current_cutoff_explain": {
            venue: _explain(connection, venue=venue, requested_at=decision_at)
            for venue in _SOURCE_SPECS
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
        description="Inspect the V4 raw-event partition/index query path without scanning payloads."
    )
    parser.add_argument("--env-file", default="/etc/bp/bp.env")
    parser.add_argument("--evidence-file", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    evidence_file = Path(args.evidence_file)
    if not evidence_file.is_file():
        raise SystemExit(f"evidence file missing: {evidence_file}")

    settings = Settings(_env_file=args.env_file)
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={
            "options": "-c default_transaction_read_only=on -c statement_timeout=3000"
        },
    )
    try:
        with engine.connect() as connection:
            if connection.execute(text("SHOW default_transaction_read_only")).scalar_one() != "on":
                raise SystemExit("database connection is not read-only")
            report = build_report(connection, evidence_file)
    finally:
        engine.dispose()

    print(json.dumps(report, sort_keys=True, indent=2, default=str))
    print("PHASE14_V4_RAW_QUERY_PATH_STATUS=PASS")
    print("DATABASE_ACCESS=read_only")
    print("DATABASE_WRITES_PERFORMED=false")
    print("SERVICE_MUTATION_PERFORMED=false")
    print("ORDER_SUBMISSION_PERFORMED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
