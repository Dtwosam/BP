from __future__ import annotations

import argparse
import json
from typing import Any

from sqlalchemy import create_engine, text

from bp_engine.config import Settings

EXPECTED_CHILD_COUNT = 16
MIN_REINDEX_SIGNAL_TOTAL_PKEY_BYTES = 5 * 1024**3
MIN_REINDEX_SIGNAL_BYTES_PER_LIVE_TUPLE = 250.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only V4 raw-event dedupe primary-key health report"
    )
    parser.add_argument("--env-file", default="/etc/bp/bp.env")
    return parser.parse_args()


def _dedupe_tables(connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        text(
            """
            SELECT
                child.oid AS table_oid,
                child.relname AS table_name,
                COALESCE(table_stats.n_live_tup, 0)::bigint AS estimated_live_tuples,
                COALESCE(table_stats.n_dead_tup, 0)::bigint AS estimated_dead_tuples,
                table_stats.last_autovacuum,
                table_stats.last_autoanalyze,
                pg_relation_size(child.oid) AS heap_bytes,
                pg_indexes_size(child.oid) AS all_index_bytes
            FROM pg_inherits AS inheritance
            JOIN pg_class AS parent
              ON parent.oid = inheritance.inhparent
            JOIN pg_namespace AS namespace
              ON namespace.oid = parent.relnamespace
            JOIN pg_class AS child
              ON child.oid = inheritance.inhrelid
            LEFT JOIN pg_stat_user_tables AS table_stats
              ON table_stats.relid = child.oid
            WHERE namespace.nspname = current_schema()
              AND parent.relname = 'raw_event_dedupe'
            ORDER BY child.relname
            """
        )
    ).mappings()
    return [dict(row) for row in rows]


def _dedupe_indexes(connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        text(
            """
            SELECT
                table_relation.relname AS table_name,
                index_relation.relname AS index_name,
                index_meta.indisprimary AS is_primary,
                index_meta.indisunique AS is_unique,
                index_meta.indisvalid AS is_valid,
                index_meta.indisready AS is_ready,
                pg_relation_size(index_relation.oid) AS bytes,
                COALESCE(index_stats.idx_scan, 0)::bigint AS idx_scan,
                COALESCE(index_stats.idx_tup_read, 0)::bigint AS idx_tup_read,
                COALESCE(index_stats.idx_tup_fetch, 0)::bigint AS idx_tup_fetch,
                COALESCE(index_io.idx_blks_read, 0)::bigint AS idx_blks_read,
                COALESCE(index_io.idx_blks_hit, 0)::bigint AS idx_blks_hit
            FROM pg_index AS index_meta
            JOIN pg_class AS table_relation
              ON table_relation.oid = index_meta.indrelid
            JOIN pg_namespace AS namespace
              ON namespace.oid = table_relation.relnamespace
            JOIN pg_class AS index_relation
              ON index_relation.oid = index_meta.indexrelid
            LEFT JOIN pg_stat_user_indexes AS index_stats
              ON index_stats.indexrelid = index_relation.oid
            LEFT JOIN pg_statio_user_indexes AS index_io
              ON index_io.indexrelid = index_relation.oid
            WHERE namespace.nspname = current_schema()
              AND table_relation.relname LIKE 'raw_event_dedupe_h__'
            ORDER BY table_relation.relname, index_meta.indisprimary DESC, index_relation.relname
            """
        )
    ).mappings()
    return [dict(row) for row in rows]


def _cache_hit_ratio(hits: int, reads: int) -> float | None:
    total = hits + reads
    return hits / total if total else None


def build_report(connection) -> dict[str, Any]:
    readonly = str(
        connection.execute(text("SHOW default_transaction_read_only")).scalar_one()
    ).lower()
    if readonly != "on":
        raise RuntimeError("database connection is not read-only")

    cache_sizes = connection.execute(
        text(
            """
            SELECT
                pg_size_bytes(current_setting('shared_buffers')) AS shared_buffers_bytes,
                pg_size_bytes(current_setting('effective_cache_size'))
                    AS effective_cache_size_bytes
            """
        )
    ).mappings().one()

    tables = _dedupe_tables(connection)
    indexes = _dedupe_indexes(connection)
    expected_tables = [
        f"raw_event_dedupe_h{remainder:02d}"
        for remainder in range(EXPECTED_CHILD_COUNT)
    ]
    expected_primary_indexes = [f"{name}_pkey" for name in expected_tables]

    table_by_name = {str(row["table_name"]): row for row in tables}
    primary_by_table: dict[str, dict[str, Any]] = {}
    secondary_by_table: dict[str, list[dict[str, Any]]] = {}
    for index in indexes:
        table_name = str(index["table_name"])
        enriched = dict(index)
        reads = int(enriched["idx_blks_read"] or 0)
        hits = int(enriched["idx_blks_hit"] or 0)
        enriched["cache_hit_ratio"] = _cache_hit_ratio(hits, reads)
        if bool(index["is_primary"]):
            primary_by_table[table_name] = enriched
        else:
            secondary_by_table.setdefault(table_name, []).append(enriched)

    children: list[dict[str, Any]] = []
    for table_name in expected_tables:
        table = table_by_name.get(table_name)
        primary = primary_by_table.get(table_name)
        secondaries = secondary_by_table.get(table_name, [])
        if table is None:
            children.append(
                {
                    "table_name": table_name,
                    "missing": True,
                    "primary_index": None,
                    "secondary_indexes": secondaries,
                }
            )
            continue

        live = int(table["estimated_live_tuples"] or 0)
        dead = int(table["estimated_dead_tuples"] or 0)
        pkey_bytes = int(primary["bytes"] or 0) if primary is not None else 0
        children.append(
            {
                "table_name": table_name,
                "missing": False,
                "estimated_live_tuples": live,
                "estimated_dead_tuples": dead,
                "dead_to_live_ratio": dead / live if live else None,
                "heap_bytes": int(table["heap_bytes"] or 0),
                "all_index_bytes": int(table["all_index_bytes"] or 0),
                "last_autovacuum": table["last_autovacuum"],
                "last_autoanalyze": table["last_autoanalyze"],
                "primary_index": primary,
                "primary_key_bytes_per_live_tuple": (
                    pkey_bytes / live if live else None
                ),
                "secondary_indexes": secondaries,
            }
        )

    primary_indexes = [
        primary_by_table[name]
        for name in expected_tables
        if name in primary_by_table
    ]
    total_live = sum(
        int(table_by_name[name]["estimated_live_tuples"] or 0)
        for name in expected_tables
        if name in table_by_name
    )
    total_dead = sum(
        int(table_by_name[name]["estimated_dead_tuples"] or 0)
        for name in expected_tables
        if name in table_by_name
    )
    total_pkey_bytes = sum(int(index["bytes"] or 0) for index in primary_indexes)
    total_all_index_bytes = sum(int(row["all_index_bytes"] or 0) for row in tables)

    actual_primary_names = [
        str(primary_by_table[name]["index_name"])
        for name in expected_tables
        if name in primary_by_table
    ]
    all_primary_healthy = (
        actual_primary_names == expected_primary_indexes
        and all(
            bool(index["is_primary"])
            and bool(index["is_unique"])
            and bool(index["is_valid"])
            and bool(index["is_ready"])
            for index in primary_indexes
        )
    )
    bytes_per_live_tuple = total_pkey_bytes / total_live if total_live else None
    reindex_signal = (
        len(tables) == EXPECTED_CHILD_COUNT
        and all_primary_healthy
        and total_pkey_bytes >= MIN_REINDEX_SIGNAL_TOTAL_PKEY_BYTES
        and bytes_per_live_tuple is not None
        and bytes_per_live_tuple >= MIN_REINDEX_SIGNAL_BYTES_PER_LIVE_TUPLE
    )

    return {
        "report": "v4_dedupe_index_health_v1",
        "expected_child_count": EXPECTED_CHILD_COUNT,
        "expected_tables": expected_tables,
        "expected_primary_indexes": expected_primary_indexes,
        "child_count": len(tables),
        "children": children,
        "totals": {
            "estimated_live_tuples": total_live,
            "estimated_dead_tuples": total_dead,
            "dead_to_live_ratio": total_dead / total_live if total_live else None,
            "primary_key_bytes": total_pkey_bytes,
            "all_index_bytes": total_all_index_bytes,
            "primary_key_fraction_of_all_index_bytes": (
                total_pkey_bytes / total_all_index_bytes
                if total_all_index_bytes
                else None
            ),
            "primary_key_bytes_per_live_tuple": bytes_per_live_tuple,
        },
        "postgresql_cache": {
            "shared_buffers_bytes": int(cache_sizes["shared_buffers_bytes"] or 0),
            "effective_cache_size_bytes": int(
                cache_sizes["effective_cache_size_bytes"] or 0
            ),
            "primary_key_bytes_to_shared_buffers_ratio": (
                total_pkey_bytes / int(cache_sizes["shared_buffers_bytes"])
                if int(cache_sizes["shared_buffers_bytes"] or 0) > 0
                else None
            ),
            "primary_key_bytes_to_effective_cache_size_ratio": (
                total_pkey_bytes / int(cache_sizes["effective_cache_size_bytes"])
                if int(cache_sizes["effective_cache_size_bytes"] or 0) > 0
                else None
            ),
        },
        "signals": {
            "all_primary_indexes_healthy": all_primary_healthy,
            "minimum_total_primary_key_bytes": MIN_REINDEX_SIGNAL_TOTAL_PKEY_BYTES,
            "minimum_primary_key_bytes_per_live_tuple": (
                MIN_REINDEX_SIGNAL_BYTES_PER_LIVE_TUPLE
            ),
            "reindex_evidence_threshold_met": reindex_signal,
        },
        "safety": {
            "database_read_only_required": True,
            "database_writes_performed": False,
            "service_mutation_performed": False,
            "order_submission_performed": False,
        },
    }


def main() -> int:
    args = parse_args()
    settings = Settings(_env_file=args.env_file)
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={
            "options": (
                "-c default_transaction_read_only=on "
                "-c statement_timeout=5000 "
                "-c application_name=bp-v4-dedupe-index-health"
            )
        },
    )
    try:
        with engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            report = build_report(connection)
    finally:
        engine.dispose()

    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    print("PHASE14_V4_DEDUPE_INDEX_HEALTH_STATUS=PASS")
    print("DATABASE_ACCESS=read_only")
    print("DATABASE_WRITES_PERFORMED=false")
    print("SERVICE_MUTATION_PERFORMED=false")
    print("ORDER_SUBMISSION_PERFORMED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
