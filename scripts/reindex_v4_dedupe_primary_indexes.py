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
INDEX_NAMES = tuple(
    f"raw_event_dedupe_h{value:02d}_pkey"
    for value in range(EXPECTED_CHILD_COUNT)
)
TABLE_NAMES = tuple(
    f"raw_event_dedupe_h{value:02d}"
    for value in range(EXPECTED_CHILD_COUNT)
)
DEFAULT_STATEMENT_TIMEOUT_SECONDS = 1200
MIN_TRANSIENT_FREE_BYTES = 2 * 1024**3
TRANSIENT_INDEX_MULTIPLIER = 4


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sequential online rebuild of the 16 V4 dedupe primary-key indexes"
    )
    parser.add_argument("--env-file", default="/etc/bp/bp.env")
    parser.add_argument("--evidence-path", required=True)
    parser.add_argument(
        "--statement-timeout-seconds",
        type=int,
        default=DEFAULT_STATEMENT_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Required acknowledgement that the caller already passed the rollout gate.",
    )
    return parser.parse_args()


def _index_state(connection, index_name: str) -> dict[str, Any] | None:
    row = connection.execute(
        text(
            """
            SELECT
                table_relation.relname AS table_name,
                index_relation.relname AS index_name,
                index_relation.oid::bigint AS index_oid,
                pg_relation_filenode(index_relation.oid)::bigint AS relfilenode,
                pg_relation_size(index_relation.oid)::bigint AS bytes,
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
              AND index_relation.relname = :index_name
            """
        ),
        {"index_name": index_name},
    ).mappings().one_or_none()
    return None if row is None else dict(row)


def _invalid_dedupe_indexes(connection) -> list[dict[str, Any]]:
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
                pg_relation_size(index_relation.oid)::bigint AS bytes
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
    return [dict(row) for row in rows]


def _assert_expected_state(
    state: dict[str, Any] | None,
    *,
    index_name: str,
    expected_table: str,
) -> dict[str, Any]:
    if state is None:
        raise RuntimeError(f"required primary index missing: {index_name}")
    if str(state["table_name"]) != expected_table:
        raise RuntimeError(
            f"primary index table mismatch: {index_name}:"
            f"{state['table_name']} != {expected_table}"
        )
    for key in ("is_primary", "is_unique", "is_valid", "is_ready"):
        if state[key] is not True:
            raise RuntimeError(
                f"primary index health mismatch: {index_name}:{key}={state[key]!r}"
            )
    return state


def _storage_snapshot(
    settings: Settings,
    *,
    index_bytes: int,
) -> dict[str, Any]:
    path = Path(settings.storage_health_path or settings.storage_archive_dir)
    if not path.exists():
        raise RuntimeError(f"storage health path is missing: {path}")
    usage = shutil.disk_usage(path)
    warning_reserve_bytes = int(settings.storage_warning_free_gib * 1024**3)
    transient_required_bytes = max(
        MIN_TRANSIENT_FREE_BYTES,
        int(index_bytes) * TRANSIENT_INDEX_MULTIPLIER,
    )
    free_above_warning_reserve_bytes = max(
        0,
        int(usage.free) - warning_reserve_bytes,
    )
    return {
        "path": str(path),
        "free_bytes": int(usage.free),
        "warning_reserve_bytes": warning_reserve_bytes,
        "free_above_warning_reserve_bytes": free_above_warning_reserve_bytes,
        "transient_required_bytes": transient_required_bytes,
        "headroom_ok": (
            free_above_warning_reserve_bytes >= transient_required_bytes
        ),
    }


def _write_evidence(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run(
    *,
    settings: Settings,
    evidence_path: Path,
    statement_timeout_seconds: int,
) -> dict[str, Any]:
    if statement_timeout_seconds <= 0:
        raise ValueError("statement_timeout_seconds must be greater than zero")

    started_at = datetime.now(UTC)
    payload: dict[str, Any] = {
        "report": "v4_dedupe_pk_reindex_v1",
        "started_at": started_at.isoformat(),
        "completed_at": None,
        "status": "running",
        "expected_indexes": list(INDEX_NAMES),
        "statement_timeout_seconds": statement_timeout_seconds,
        "indexes": [],
        "invalid_indexes_after": None,
        "safety": {
            "database_mutation_expected": True,
            "operation": "REINDEX INDEX CONCURRENTLY",
            "service_mutation_performed_by_python": False,
            "order_submission_performed": False,
        },
    }
    _write_evidence(evidence_path, payload)

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        with engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            server_version_num = int(
                connection.execute(
                    text("SELECT current_setting('server_version_num')::int")
                ).scalar_one()
            )
            if server_version_num < 120000:
                raise RuntimeError(
                    f"PostgreSQL version does not support required operation:"
                    f" {server_version_num}"
                )
            connection.exec_driver_sql("SET lock_timeout = '0'")
            connection.exec_driver_sql(
                f"SET statement_timeout = '{statement_timeout_seconds}s'"
            )

            initial_states: list[dict[str, Any]] = []
            for index_name, table_name in zip(
                INDEX_NAMES,
                TABLE_NAMES,
                strict=True,
            ):
                initial_states.append(
                    _assert_expected_state(
                        _index_state(connection, index_name),
                        index_name=index_name,
                        expected_table=table_name,
                    )
                )
            payload["initial_total_primary_key_bytes"] = sum(
                int(row["bytes"] or 0) for row in initial_states
            )
            payload["server_version_num"] = server_version_num
            _write_evidence(evidence_path, payload)

            for ordinal, (index_name, table_name) in enumerate(
                zip(INDEX_NAMES, TABLE_NAMES, strict=True),
                start=1,
            ):
                before = _assert_expected_state(
                    _index_state(connection, index_name),
                    index_name=index_name,
                    expected_table=table_name,
                )
                storage_before = _storage_snapshot(
                    settings,
                    index_bytes=int(before["bytes"] or 0),
                )
                if not storage_before["headroom_ok"]:
                    raise RuntimeError(
                        f"insufficient transient storage headroom before {index_name}"
                    )

                record: dict[str, Any] = {
                    "ordinal": ordinal,
                    "index_name": index_name,
                    "table_name": table_name,
                    "started_at": datetime.now(UTC).isoformat(),
                    "completed_at": None,
                    "status": "running",
                    "before": before,
                    "storage_before": storage_before,
                    "after": None,
                    "error": None,
                }
                payload["indexes"].append(record)
                _write_evidence(evidence_path, payload)

                try:
                    connection.exec_driver_sql(
                        f'REINDEX INDEX CONCURRENTLY "{index_name}"'
                    )
                    after = _assert_expected_state(
                        _index_state(connection, index_name),
                        index_name=index_name,
                        expected_table=table_name,
                    )
                    record["after"] = after
                    record["completed_at"] = datetime.now(UTC).isoformat()
                    record["status"] = "success"
                    record["bytes_reclaimed"] = (
                        int(before["bytes"] or 0)
                        - int(after["bytes"] or 0)
                    )
                except Exception as exc:
                    record["completed_at"] = datetime.now(UTC).isoformat()
                    record["status"] = "failure"
                    record["error"] = f"{type(exc).__name__}: {exc}"
                    record["after"] = _index_state(connection, index_name)
                    payload["invalid_indexes_after"] = _invalid_dedupe_indexes(
                        connection
                    )
                    payload["completed_at"] = datetime.now(UTC).isoformat()
                    payload["status"] = "failure"
                    _write_evidence(evidence_path, payload)
                    raise

                _write_evidence(evidence_path, payload)

            final_states = [
                _assert_expected_state(
                    _index_state(connection, index_name),
                    index_name=index_name,
                    expected_table=table_name,
                )
                for index_name, table_name in zip(
                    INDEX_NAMES,
                    TABLE_NAMES,
                    strict=True,
                )
            ]
            invalid_after = _invalid_dedupe_indexes(connection)
            if invalid_after:
                raise RuntimeError(
                    f"invalid dedupe indexes remain after rebuild: {invalid_after}"
                )

            payload["final_total_primary_key_bytes"] = sum(
                int(row["bytes"] or 0) for row in final_states
            )
            payload["total_bytes_reclaimed"] = (
                int(payload["initial_total_primary_key_bytes"])
                - int(payload["final_total_primary_key_bytes"])
            )
            payload["invalid_indexes_after"] = invalid_after
            payload["completed_at"] = datetime.now(UTC).isoformat()
            payload["status"] = "success"
            _write_evidence(evidence_path, payload)
            return payload
    except Exception as exc:
        if payload["status"] == "running":
            payload["completed_at"] = datetime.now(UTC).isoformat()
            payload["status"] = "failure"
            payload["error"] = f"{type(exc).__name__}: {exc}"
            _write_evidence(evidence_path, payload)
        raise
    finally:
        engine.dispose()


def main() -> int:
    args = parse_args()
    if not args.execute:
        raise SystemExit("--execute is required")
    settings = Settings(_env_file=args.env_file)
    payload = run(
        settings=settings,
        evidence_path=Path(args.evidence_path),
        statement_timeout_seconds=args.statement_timeout_seconds,
    )
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    print("PHASE14_V4_DEDUPE_PK_REINDEX_STATUS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
