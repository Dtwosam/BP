from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from bp_engine.config import Settings

EXPECTED_CHILD_COUNT = 16
MIN_SERVER_VERSION_NUM = 120000
LONG_TRANSACTION_SECONDS = 60.0
MIN_TRANSIENT_FREE_BYTES = 2 * 1024**3
TRANSIENT_INDEX_MULTIPLIER = 4


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only V4 dedupe online-rebuild readiness report"
    )
    parser.add_argument("--env-file", default="/etc/bp/bp.env")
    return parser.parse_args()


def _expected_tables() -> list[str]:
    return [
        f"raw_event_dedupe_h{remainder:02d}"
        for remainder in range(EXPECTED_CHILD_COUNT)
    ]


def _expected_primary_indexes() -> list[str]:
    return [f"{name}_pkey" for name in _expected_tables()]


def _index_rows(connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            text(
                """
                SELECT
                    child.relname AS table_name,
                    index_relation.relname AS index_name,
                    index_meta.indisprimary AS is_primary,
                    index_meta.indisunique AS is_unique,
                    index_meta.indisvalid AS is_valid,
                    index_meta.indisready AS is_ready,
                    pg_relation_size(index_relation.oid) AS bytes,
                    COALESCE(table_stats.n_live_tup, 0)::bigint
                        AS estimated_live_tuples
                FROM pg_inherits AS inheritance
                JOIN pg_class AS parent
                  ON parent.oid = inheritance.inhparent
                JOIN pg_namespace AS namespace
                  ON namespace.oid = parent.relnamespace
                JOIN pg_class AS child
                  ON child.oid = inheritance.inhrelid
                JOIN pg_index AS index_meta
                  ON index_meta.indrelid = child.oid
                 AND index_meta.indisprimary
                JOIN pg_class AS index_relation
                  ON index_relation.oid = index_meta.indexrelid
                LEFT JOIN pg_stat_user_tables AS table_stats
                  ON table_stats.relid = child.oid
                WHERE namespace.nspname = current_schema()
                  AND parent.relname = 'raw_event_dedupe'
                ORDER BY child.relname
                """
            )
        ).mappings()
    ]


def _invalid_dedupe_indexes(connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            text(
                """
                SELECT
                    table_relation.relname AS table_name,
                    index_relation.relname AS index_name,
                    index_meta.indisprimary AS is_primary,
                    index_meta.indisunique AS is_unique,
                    index_meta.indisvalid AS is_valid,
                    index_meta.indisready AS is_ready
                FROM pg_index AS index_meta
                JOIN pg_class AS table_relation
                  ON table_relation.oid = index_meta.indrelid
                JOIN pg_namespace AS namespace
                  ON namespace.oid = table_relation.relnamespace
                JOIN pg_class AS index_relation
                  ON index_relation.oid = index_meta.indexrelid
                WHERE namespace.nspname = current_schema()
                  AND table_relation.relname LIKE 'raw_event_dedupe_h__'
                  AND (
                    index_meta.indisvalid IS NOT TRUE
                    OR index_meta.indisready IS NOT TRUE
                  )
                ORDER BY table_relation.relname, index_relation.relname
                """
            )
        ).mappings()
    ]


def _long_transactions(connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            text(
                """
                SELECT
                    pid,
                    usename,
                    application_name,
                    state,
                    EXTRACT(EPOCH FROM (clock_timestamp() - xact_start))
                        AS xact_age_seconds,
                    wait_event_type,
                    wait_event
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND backend_type = 'client backend'
                  AND pid <> pg_backend_pid()
                  AND xact_start IS NOT NULL
                  AND clock_timestamp() - xact_start
                        >= (:seconds * interval '1 second')
                ORDER BY xact_start
                """
            ),
            {"seconds": LONG_TRANSACTION_SECONDS},
        ).mappings()
    ]


def _prepared_transactions(connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            text(
                """
                SELECT gid, prepared, owner, database
                FROM pg_prepared_xacts
                WHERE database = current_database()
                ORDER BY prepared
                """
            )
        ).mappings()
    ]


def build_report(connection, settings: Settings) -> dict[str, Any]:
    readonly = str(
        connection.execute(text("SHOW default_transaction_read_only")).scalar_one()
    ).lower()
    if readonly != "on":
        raise RuntimeError("database connection is not read-only")

    server_version_num = int(
        connection.execute(
            text("SELECT current_setting('server_version_num')::int")
        ).scalar_one()
    )
    indexes = _index_rows(connection)
    invalid_indexes = _invalid_dedupe_indexes(connection)
    long_transactions = _long_transactions(connection)
    prepared_transactions = _prepared_transactions(connection)

    expected_tables = _expected_tables()
    expected_indexes = _expected_primary_indexes()
    actual_tables = [str(row["table_name"]) for row in indexes]
    actual_indexes = [str(row["index_name"]) for row in indexes]
    all_primary_indexes_healthy = (
        actual_tables == expected_tables
        and actual_indexes == expected_indexes
        and len(indexes) == EXPECTED_CHILD_COUNT
        and all(
            bool(row["is_primary"])
            and bool(row["is_unique"])
            and bool(row["is_valid"])
            and bool(row["is_ready"])
            for row in indexes
        )
    )

    total_primary_key_bytes = sum(int(row["bytes"] or 0) for row in indexes)
    largest_primary_key_bytes = max(
        (int(row["bytes"] or 0) for row in indexes),
        default=0,
    )
    total_live_tuples = sum(
        int(row["estimated_live_tuples"] or 0) for row in indexes
    )
    bytes_per_live_tuple = (
        total_primary_key_bytes / total_live_tuples
        if total_live_tuples
        else None
    )

    health_path = Path(
        settings.storage_health_path
        or settings.storage_archive_dir
    )
    if not health_path.exists():
        raise RuntimeError(f"storage health path is missing: {health_path}")
    usage = shutil.disk_usage(health_path)
    warning_reserve_bytes = int(settings.storage_warning_free_gib * 1024**3)
    transient_required_bytes = max(
        MIN_TRANSIENT_FREE_BYTES,
        largest_primary_key_bytes * TRANSIENT_INDEX_MULTIPLIER,
    )
    free_above_warning_reserve_bytes = max(
        0,
        int(usage.free) - warning_reserve_bytes,
    )
    transient_headroom_ok = (
        free_above_warning_reserve_bytes >= transient_required_bytes
    )

    server_supports_concurrent_rebuild = server_version_num >= MIN_SERVER_VERSION_NUM
    no_invalid_indexes = len(invalid_indexes) == 0
    no_long_transactions = len(long_transactions) == 0
    no_prepared_transactions = len(prepared_transactions) == 0

    readiness = (
        all_primary_indexes_healthy
        and server_supports_concurrent_rebuild
        and no_invalid_indexes
        and no_long_transactions
        and no_prepared_transactions
        and transient_headroom_ok
    )

    return {
        "report": "v4_dedupe_reindex_readiness_v1",
        "recorded_at": datetime.now(UTC).isoformat(),
        "expected_child_count": EXPECTED_CHILD_COUNT,
        "expected_tables": expected_tables,
        "expected_primary_indexes": expected_indexes,
        "primary_indexes": indexes,
        "totals": {
            "estimated_live_tuples": total_live_tuples,
            "primary_key_bytes": total_primary_key_bytes,
            "largest_primary_key_bytes": largest_primary_key_bytes,
            "primary_key_bytes_per_live_tuple": bytes_per_live_tuple,
        },
        "postgresql": {
            "server_version_num": server_version_num,
            "minimum_server_version_num": MIN_SERVER_VERSION_NUM,
            "supports_concurrent_rebuild": server_supports_concurrent_rebuild,
            "invalid_or_not_ready_dedupe_indexes": invalid_indexes,
            "long_transaction_threshold_seconds": LONG_TRANSACTION_SECONDS,
            "long_transactions": long_transactions,
            "prepared_transactions": prepared_transactions,
        },
        "storage_headroom": {
            "path": str(health_path),
            "total_bytes": int(usage.total),
            "used_bytes": int(usage.used),
            "free_bytes": int(usage.free),
            "warning_reserve_bytes": warning_reserve_bytes,
            "free_above_warning_reserve_bytes": free_above_warning_reserve_bytes,
            "minimum_transient_free_bytes": MIN_TRANSIENT_FREE_BYTES,
            "transient_index_multiplier": TRANSIENT_INDEX_MULTIPLIER,
            "transient_required_bytes": transient_required_bytes,
            "transient_headroom_ok": transient_headroom_ok,
        },
        "signals": {
            "all_primary_indexes_healthy": all_primary_indexes_healthy,
            "no_invalid_indexes": no_invalid_indexes,
            "no_long_transactions": no_long_transactions,
            "no_prepared_transactions": no_prepared_transactions,
            "transient_headroom_ok": transient_headroom_ok,
            "reindex_readiness_pass": readiness,
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
                "-c application_name=bp-v4-dedupe-reindex-readiness"
            )
        },
    )
    try:
        with engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            report = build_report(connection, settings)
    finally:
        engine.dispose()

    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    print("PHASE14_V4_DEDUPE_REINDEX_READINESS_STATUS=PASS")
    print("DATABASE_ACCESS=read_only")
    print("DATABASE_WRITES_PERFORMED=false")
    print("SERVICE_MUTATION_PERFORMED=false")
    print("ORDER_SUBMISSION_PERFORMED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
