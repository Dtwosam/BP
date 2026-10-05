from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import report_v4_dedupe_reindex_blockers as blockers
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from bp_engine.config import Settings

EXPECTED_CHILD_COUNT = 16
MIN_TRANSIENT_FREE_BYTES = 2 * 1024**3
TRANSIENT_TOTAL_PKEY_MULTIPLIER = 2
INDEX_STATEMENT_TIMEOUT_SECONDS = 1200
# CREATE/DROP INDEX CONCURRENTLY can legitimately wait on virtual transaction
# IDs held by live/idle-in-transaction writers after the relation lock itself
# is available. Give that online DDL a bounded wait budget distinct from the
# fail-fast irreversible PK-drop lock budget below.
INDEX_LOCK_TIMEOUT_SECONDS = 300
INDEX_LOCK_CLEAR_WAIT_SECONDS = 300
INDEX_LOCK_POLL_SECONDS = 0.5
INDEX_LOCK_CLEAR_CONSECUTIVE_SAMPLES = 3
INDEX_LOCK_RETRY_ATTEMPTS = 3
INDEX_LOCK_RETRY_SLEEP_SECONDS = 2.0
DDL_LOCK_TIMEOUT_SECONDS = 5
DROP_CONSTRAINT_TIMEOUT_SECONDS = 30
CLEAN_WINDOW_SAMPLES = 80
CLEAN_WINDOW_INTERVAL_SECONDS = 0.5

RECORDER_UNIT = "bp-recorder.service"
MAINTENANCE_SERVICE = "bp-storage-maintenance.service"
MAINTENANCE_TIMER = "bp-storage-maintenance.timer"
V2_SERVICE = "bp-v2-forward-coverage.service"
V2_TIMER = "bp-v2-forward-coverage.timer"
V4_SERVICE = "bp-v4-forward-coverage.service"
V4_TIMER = "bp-v4-forward-coverage.timer"
DISK_HEALTH_SERVICE = "bp-storage-disk-health.service"
DISK_HEALTH_TIMER = "bp-storage-disk-health.timer"

QUIESCED_TIMERS = (MAINTENANCE_TIMER, V2_TIMER, V4_TIMER)
QUIESCED_ONESHOTS = (MAINTENANCE_SERVICE, V2_SERVICE, V4_SERVICE)
REQUIRED_ACTIVE_SERVICES = (
    "bp-postgres.service",
    RECORDER_UNIT,
    "bp-v3-frozen-predictor.service",
    "bp-v3-paper-execution.service",
    "bp-dashboard-api.service",
    "bp-dashboard-web.service",
    "bp-paper-execution.service",
    "bp-live-predictor.service",
    "bp-prospective-outcomes.service",
)
REQUIRED_TIMERS = (
    MAINTENANCE_TIMER,
    DISK_HEALTH_TIMER,
    V2_TIMER,
    V4_TIMER,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Guarded production migration to compact V4 dedupe indexes"
    )
    parser.add_argument("--repo", default="/opt/bp")
    parser.add_argument("--env-file", default="/etc/bp/bp.env")
    parser.add_argument(
        "--safety-file",
        default="/etc/bp/bp-prospective-runtime-safety.env",
    )
    parser.add_argument("--expected-deployed-head", required=True)
    parser.add_argument("--helper-head", required=True)
    parser.add_argument("--readiness-evidence-sha256", required=True)
    parser.add_argument("--blocker-evidence-sha256", required=True)
    parser.add_argument("--evidence-root", default="/var/lib/bp/evidence")
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=check,
        text=True,
        capture_output=True,
    )


def _systemctl(*args: str) -> str:
    return _run("systemctl", *args).stdout.strip()


def _is_active(unit: str) -> bool:
    return (
        _run(
            "systemctl",
            "is-active",
            "--quiet",
            unit,
            check=False,
        ).returncode
        == 0
    )


def _is_enabled(unit: str) -> bool:
    return (
        _run(
            "systemctl",
            "is-enabled",
            "--quiet",
            unit,
            check=False,
        ).returncode
        == 0
    )


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _read_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise RuntimeError(f"required safety file is missing: {path}")
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        result[key] = value
    return result


def _require_research_zero_money(
    env_file: Path,
    safety_file: Path,
) -> None:
    expected = {
        "MODE": "research",
        "LIVE_TRADING_ENABLED": "false",
        "MAX_TRADE_SIZE_USD": "0",
        "MAX_DAILY_LOSS_USD": "0",
    }
    for path in (env_file, safety_file):
        values = _read_env(path)
        for key, expected_value in expected.items():
            if values.get(key) != expected_value:
                raise RuntimeError(
                    f"safety setting drifted: {path}:{key}={values.get(key)!r}"
                )


def _require_automatic_promotion_false(project_state: Path) -> None:
    payload = json.loads(project_state.read_text(encoding="utf-8"))
    values: list[Any] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "automatic_promotion":
                    values.append(item)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    if not values or any(value is not False for value in values):
        raise RuntimeError("automatic_promotion must remain false")


def _require_recorder_binding(settings: Settings) -> None:
    expected: dict[str, object] = {
        "recorder_queue_maxsize": 50000,
        "recorder_batch_size": 500,
        "recorder_writer_workers": 4,
        "recorder_flush_interval_seconds": 0.25,
    }
    for key, expected_value in expected.items():
        actual = getattr(settings, key)
        if isinstance(expected_value, float):
            if abs(float(actual) - expected_value) > 1e-9:
                raise RuntimeError(f"recorder setting drifted: {key}={actual}")
        elif actual != expected_value:
            raise RuntimeError(f"recorder setting drifted: {key}={actual}")


def _require_services_and_timers() -> None:
    for unit in REQUIRED_ACTIVE_SERVICES:
        if not _is_active(unit):
            raise RuntimeError(f"required service is not active: {unit}")
    for timer in REQUIRED_TIMERS:
        if not _is_enabled(timer):
            raise RuntimeError(f"required timer is not enabled: {timer}")
        if not _is_active(timer):
            raise RuntimeError(f"required timer is not active: {timer}")


def _wait_oneshot_idle_success(unit: str, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        state = _systemctl("show", "-p", "ActiveState", "--value", unit)
        if state == "inactive":
            break
        if state not in {"active", "activating"}:
            raise RuntimeError(f"oneshot entered unexpected state: {unit}:{state}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"oneshot wait timed out: {unit}")
        time.sleep(5)

    result = _systemctl("show", "-p", "Result", "--value", unit)
    if result != "success":
        raise RuntimeError(f"oneshot last result is not success: {unit}:{result}")


def _expected_tables() -> tuple[str, ...]:
    return tuple(
        f"raw_event_dedupe_h{remainder:02d}"
        for remainder in range(EXPECTED_CHILD_COUNT)
    )


def _expected_compact_indexes() -> tuple[str, ...]:
    return tuple(f"{table}_digest_uidx" for table in _expected_tables())


def _index_inventory(connection) -> list[dict[str, Any]]:
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
                    index_meta.indisready AS is_ready,
                    pg_relation_size(index_relation.oid) AS bytes,
                    pg_get_indexdef(index_relation.oid) AS index_definition
                FROM pg_index AS index_meta
                JOIN pg_class AS table_relation
                  ON table_relation.oid = index_meta.indrelid
                JOIN pg_namespace AS namespace
                  ON namespace.oid = table_relation.relnamespace
                JOIN pg_class AS index_relation
                  ON index_relation.oid = index_meta.indexrelid
                WHERE namespace.nspname = current_schema()
                  AND table_relation.relname LIKE 'raw_event_dedupe_h__'
                ORDER BY table_relation.relname, index_relation.relname
                """
            )
        ).mappings()
    ]


def _parent_contract(connection) -> dict[str, Any]:
    row = connection.execute(
        text(
            """
            SELECT
                pg_get_partkeydef(parent.oid) AS partition_key,
                constraint_meta.conname AS primary_constraint_name,
                pg_get_constraintdef(constraint_meta.oid)
                    AS primary_constraint_definition
            FROM pg_class AS parent
            JOIN pg_namespace AS namespace
              ON namespace.oid = parent.relnamespace
            LEFT JOIN pg_constraint AS constraint_meta
              ON constraint_meta.conrelid = parent.oid
             AND constraint_meta.contype = 'p'
            WHERE namespace.nspname = current_schema()
              AND parent.relname = 'raw_event_dedupe'
            """
        )
    ).mappings().one_or_none()
    return dict(row) if row is not None else {}


def _child_tables(connection) -> tuple[str, ...]:
    return tuple(
        str(name)
        for name in connection.execute(
            text(
                """
                SELECT child.relname
                FROM pg_inherits
                JOIN pg_class AS parent
                  ON parent.oid = pg_inherits.inhparent
                JOIN pg_namespace AS namespace
                  ON namespace.oid = parent.relnamespace
                JOIN pg_class AS child
                  ON child.oid = pg_inherits.inhrelid
                WHERE namespace.nspname = current_schema()
                  AND parent.relname = 'raw_event_dedupe'
                ORDER BY child.relname
                """
            )
        ).scalars()
    )


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


def _compact_definition_ok(row: dict[str, Any]) -> bool:
    definition = " ".join(str(row["index_definition"] or "").lower().split())
    return (
        bool(row["is_unique"])
        and bool(row["is_valid"])
        and bool(row["is_ready"])
        and "decode(" in definition
        and "substring" in definition
        and "dedupe_key" in definition
        and "'hex'" in definition
    )


def _storage_headroom(
    settings: Settings,
    primary_key_bytes: int,
) -> dict[str, Any]:
    health_path = Path(settings.storage_health_path or settings.storage_archive_dir)
    usage = shutil.disk_usage(health_path)
    reserve = int(settings.storage_warning_free_gib * 1024**3)
    free_above_reserve = max(0, int(usage.free) - reserve)
    required = max(
        MIN_TRANSIENT_FREE_BYTES,
        primary_key_bytes * TRANSIENT_TOTAL_PKEY_MULTIPLIER,
    )
    return {
        "path": str(health_path),
        "free_bytes": int(usage.free),
        "warning_reserve_bytes": reserve,
        "free_above_warning_reserve_bytes": free_above_reserve,
        "transient_required_bytes": required,
        "transient_headroom_ok": free_above_reserve >= required,
    }


def _state_report(connection, settings: Settings) -> dict[str, Any]:
    inventory = _index_inventory(connection)
    parent = _parent_contract(connection)
    children = _child_tables(connection)
    expected_compact = set(_expected_compact_indexes())

    primary = [row for row in inventory if bool(row["is_primary"])]
    compact = [
        row
        for row in inventory
        if str(row["index_name"]) in expected_compact
    ]
    invalid = [
        row
        for row in inventory
        if not bool(row["is_valid"]) or not bool(row["is_ready"])
    ]
    primary_bytes = sum(int(row["bytes"] or 0) for row in primary)

    return {
        "parent_contract": parent,
        "children": list(children),
        "primary_indexes": primary,
        "compact_indexes": compact,
        "invalid_or_not_ready_indexes": invalid,
        "prepared_transactions": _prepared_transactions(connection),
        "storage_headroom": _storage_headroom(settings, primary_bytes),
    }


def _require_legacy_pre_state(report: dict[str, Any]) -> None:
    parent = dict(report["parent_contract"])
    primary = list(report["primary_indexes"])
    compact = list(report["compact_indexes"])
    expected_primary = {
        f"{table_name}_pkey"
        for table_name in _expected_tables()
    }
    actual_primary = {str(row["index_name"]) for row in primary}

    if tuple(report["children"]) != _expected_tables():
        raise RuntimeError("dedupe child set drifted")
    if parent.get("partition_key") != "HASH (dedupe_key)":
        raise RuntimeError("dedupe partition key drifted")
    if parent.get("primary_constraint_name") != "raw_event_dedupe_pkey":
        raise RuntimeError("dedupe parent primary key is missing")
    if parent.get("primary_constraint_definition") != "PRIMARY KEY (dedupe_key)":
        raise RuntimeError("dedupe parent primary key definition drifted")
    if actual_primary != expected_primary:
        raise RuntimeError("dedupe child primary index set drifted")
    if len(primary) != EXPECTED_CHILD_COUNT:
        raise RuntimeError("dedupe child primary index count drifted")
    if compact:
        raise RuntimeError("compact dedupe indexes already exist")
    if report["invalid_or_not_ready_indexes"]:
        raise RuntimeError("invalid dedupe indexes are present")
    if report["prepared_transactions"]:
        raise RuntimeError("prepared transactions are present")
    if not bool(report["storage_headroom"]["transient_headroom_ok"]):
        raise RuntimeError("insufficient transient storage headroom")


def _require_transition_state(report: dict[str, Any]) -> None:
    _require_legacy_pre_state(
        {
            **report,
            "compact_indexes": [],
        }
    )
    compact = list(report["compact_indexes"])
    expected = set(_expected_compact_indexes())
    actual = {str(row["index_name"]) for row in compact}
    if actual != expected or len(compact) != EXPECTED_CHILD_COUNT:
        raise RuntimeError("compact dedupe index set is incomplete")
    if not all(_compact_definition_ok(row) for row in compact):
        raise RuntimeError("compact dedupe index health mismatch")


def _require_compact_post_state(report: dict[str, Any]) -> None:
    parent = dict(report["parent_contract"])
    compact = list(report["compact_indexes"])
    expected = set(_expected_compact_indexes())
    actual = {str(row["index_name"]) for row in compact}

    if tuple(report["children"]) != _expected_tables():
        raise RuntimeError("dedupe child set drifted after migration")
    if parent.get("partition_key") != "HASH (dedupe_key)":
        raise RuntimeError("dedupe partition key drifted after migration")
    if parent.get("primary_constraint_name") is not None:
        raise RuntimeError("dedupe parent primary key still exists")
    if report["primary_indexes"]:
        raise RuntimeError("dedupe child primary indexes still exist")
    if actual != expected or len(compact) != EXPECTED_CHILD_COUNT:
        raise RuntimeError("compact dedupe index set is incomplete")
    if not all(_compact_definition_ok(row) for row in compact):
        raise RuntimeError("compact dedupe index health mismatch")
    if report["invalid_or_not_ready_indexes"]:
        raise RuntimeError("invalid dedupe indexes are present")
    if report["prepared_transactions"]:
        raise RuntimeError("prepared transactions are present")


def _readonly_engine(settings: Settings):
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={
            "options": (
                "-c default_transaction_read_only=on "
                "-c statement_timeout=5000 "
                "-c application_name=bp-v4-compact-dedupe-evidence"
            )
        },
    )


def _state(settings: Settings) -> dict[str, Any]:
    engine = _readonly_engine(settings)
    try:
        with engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            if (
                str(
                    connection.execute(
                        text("SHOW default_transaction_read_only")
                    ).scalar_one()
                ).lower()
                != "on"
            ):
                raise RuntimeError("evidence connection is not read-only")
            return _state_report(connection, settings)
    finally:
        engine.dispose()


def _clean_window(settings: Settings) -> dict[str, Any]:
    engine = _readonly_engine(settings)
    try:
        with engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            return blockers.build_report(
                connection,
                samples=CLEAN_WINDOW_SAMPLES,
                interval_seconds=CLEAN_WINDOW_INTERVAL_SECONDS,
                long_transaction_seconds=(
                    blockers.DEFAULT_LONG_TRANSACTION_SECONDS
                ),
            )
    finally:
        engine.dispose()


def _require_clean_window(report: dict[str, Any]) -> None:
    summary = dict(report["pid_summary"])
    signals = dict(report["signals"])
    if list(summary.get("persistent_pids", ())):
        raise RuntimeError("persistent long transactions remain")
    if list(summary.get("appeared_pids", ())):
        raise RuntimeError("long transaction appeared at clean-window end")
    if signals.get("persistent_long_transaction_count") != 0:
        raise RuntimeError("persistent long transaction count is nonzero")
    if signals.get("persistent_writer_like_count") != 0:
        raise RuntimeError("persistent writer-like transaction count is nonzero")
    if signals.get("writer_quiesce_likely_required_for_bounded_reindex") is not False:
        raise RuntimeError("writer quiesce signal is not clean")


def _mutation_engine(settings: Settings):
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={
            "options": (
                f"-c statement_timeout={INDEX_STATEMENT_TIMEOUT_SECONDS * 1000} "
                f"-c lock_timeout={INDEX_LOCK_TIMEOUT_SECONDS * 1000} "
                "-c application_name=bp-v4-compact-dedupe-migration"
            )
        },
    )


def _create_compact_index(
    engine,
    *,
    table_name: str,
    index_name: str,
) -> None:
    with engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as connection:
        connection.execute(
            text(
                f'''
                CREATE UNIQUE INDEX CONCURRENTLY "{index_name}"
                ON "{table_name}" (
                    (decode(substring(dedupe_key FROM 8), 'hex'))
                )
                '''
            )
        )


def _drop_compact_index(engine, index_name: str) -> None:
    with engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as connection:
        connection.execute(
            text(f'DROP INDEX CONCURRENTLY IF EXISTS "{index_name}"')
        )


def _is_lock_timeout(exc: BaseException) -> bool:
    if not isinstance(exc, OperationalError):
        return False
    original = getattr(exc, "orig", None)
    sqlstate = getattr(original, "sqlstate", None)
    return sqlstate == "55P03" or "lock timeout" in str(original).lower()


def _relation_lock_snapshot(
    connection,
    *,
    table_name: str,
) -> list[dict[str, Any]]:
    return [
        {
            "pid": int(row["pid"]),
            "backend_type": str(row["backend_type"] or ""),
            "user": str(row["usename"] or ""),
            "application_name": str(row["application_name"] or ""),
            "state": str(row["state"] or ""),
            "lock_mode": str(row["lock_mode"]),
            "wait_event_type": (
                str(row["wait_event_type"])
                if row["wait_event_type"] is not None
                else None
            ),
            "wait_event": (
                str(row["wait_event"])
                if row["wait_event"] is not None
                else None
            ),
            "xact_age_seconds": (
                float(row["xact_age_seconds"])
                if row["xact_age_seconds"] is not None
                else None
            ),
        }
        for row in connection.execute(
            text(
                """
                SELECT
                    lock.pid,
                    activity.backend_type,
                    activity.usename,
                    activity.application_name,
                    activity.state,
                    lock.mode AS lock_mode,
                    activity.wait_event_type,
                    activity.wait_event,
                    CASE
                        WHEN activity.xact_start IS NULL THEN NULL
                        ELSE EXTRACT(
                            EPOCH FROM (
                                clock_timestamp() - activity.xact_start
                            )
                        )
                    END AS xact_age_seconds
                FROM pg_locks AS lock
                JOIN pg_class AS relation
                  ON relation.oid = lock.relation
                JOIN pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                LEFT JOIN pg_stat_activity AS activity
                  ON activity.pid = lock.pid
                WHERE namespace.nspname = current_schema()
                  AND relation.relname = :table_name
                  AND lock.granted IS TRUE
                  AND lock.pid <> pg_backend_pid()
                  AND lock.mode IN (
                      'ShareUpdateExclusiveLock',
                      'ShareLock',
                      'ShareRowExclusiveLock',
                      'ExclusiveLock',
                      'AccessExclusiveLock'
                  )
                ORDER BY lock.mode, lock.pid
                """
            ),
            {"table_name": table_name},
        ).mappings()
    ]


def _wait_for_index_relation_lock_clear(
    settings: Settings,
    *,
    table_name: str,
    evidence_path: Path,
) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    started_monotonic = time.monotonic()
    samples = 0
    samples_with_conflicts = 0
    maximum_conflicting_locks = 0
    clear_samples = 0
    last_conflicts: list[dict[str, Any]] = []

    engine = _readonly_engine(settings)
    try:
        with engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            while True:
                samples += 1
                conflicts = _relation_lock_snapshot(
                    connection,
                    table_name=table_name,
                )
                if conflicts:
                    samples_with_conflicts += 1
                    maximum_conflicting_locks = max(
                        maximum_conflicting_locks,
                        len(conflicts),
                    )
                    clear_samples = 0
                    last_conflicts = conflicts
                else:
                    clear_samples += 1
                    if (
                        clear_samples
                        >= INDEX_LOCK_CLEAR_CONSECUTIVE_SAMPLES
                    ):
                        break

                elapsed = time.monotonic() - started_monotonic
                if elapsed >= INDEX_LOCK_CLEAR_WAIT_SECONDS:
                    report = {
                        "table_name": table_name,
                        "started_at": started_at.isoformat(),
                        "completed_at": datetime.now(UTC).isoformat(),
                        "status": "timeout",
                        "samples": samples,
                        "samples_with_conflicts": samples_with_conflicts,
                        "maximum_conflicting_locks": maximum_conflicting_locks,
                        "last_conflicts": last_conflicts,
                        "wait_seconds": elapsed,
                    }
                    _write_json(evidence_path, report)
                    raise RuntimeError(
                        "conflicting relation lock did not clear for "
                        f"{table_name}"
                    )

                time.sleep(INDEX_LOCK_POLL_SECONDS)
    finally:
        engine.dispose()

    report = {
        "table_name": table_name,
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "status": "clear",
        "samples": samples,
        "samples_with_conflicts": samples_with_conflicts,
        "maximum_conflicting_locks": maximum_conflicting_locks,
        "last_conflicts": last_conflicts,
        "wait_seconds": time.monotonic() - started_monotonic,
    }
    _write_json(evidence_path, report)
    return report


def _compact_index_row(
    settings: Settings,
    *,
    index_name: str,
) -> dict[str, Any] | None:
    state = _state(settings)
    matching = [
        dict(row)
        for row in state["compact_indexes"]
        if str(row["index_name"]) == index_name
    ]
    if len(matching) > 1:
        raise RuntimeError(
            f"duplicate compact index metadata rows for {index_name}"
        )
    return matching[0] if matching else None


def _drop_compact_index_with_retries(
    engine,
    settings: Settings,
    *,
    table_name: str,
    index_name: str,
    evidence_dir: Path,
) -> None:
    for attempt in range(1, INDEX_LOCK_RETRY_ATTEMPTS + 1):
        if _compact_index_row(settings, index_name=index_name) is None:
            return

        _wait_for_index_relation_lock_clear(
            settings,
            table_name=table_name,
            evidence_path=(
                evidence_dir
                / f"lock-wait-drop-{index_name}-{attempt}.json"
            ),
        )

        try:
            _drop_compact_index(engine, index_name)
        except Exception as exc:
            if (
                not _is_lock_timeout(exc)
                or attempt >= INDEX_LOCK_RETRY_ATTEMPTS
            ):
                raise
            time.sleep(INDEX_LOCK_RETRY_SLEEP_SECONDS)
            continue

        if _compact_index_row(settings, index_name=index_name) is None:
            return

        if attempt < INDEX_LOCK_RETRY_ATTEMPTS:
            time.sleep(INDEX_LOCK_RETRY_SLEEP_SECONDS)

    raise RuntimeError(
        f"compact index cleanup did not complete for {index_name}"
    )


def _create_compact_index_with_retries(
    engine,
    settings: Settings,
    *,
    table_name: str,
    index_name: str,
    evidence_dir: Path,
) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    attempts_path = evidence_dir / f"index-attempts-{index_name}.json"

    for attempt in range(1, INDEX_LOCK_RETRY_ATTEMPTS + 1):
        lock_report = _wait_for_index_relation_lock_clear(
            settings,
            table_name=table_name,
            evidence_path=(
                evidence_dir
                / f"lock-wait-create-{index_name}-{attempt}.json"
            ),
        )

        try:
            _create_compact_index(
                engine,
                table_name=table_name,
                index_name=index_name,
            )
        except Exception as exc:
            if not _is_lock_timeout(exc):
                raise

            current = _compact_index_row(
                settings,
                index_name=index_name,
            )
            record = {
                "attempt": attempt,
                "result": "lock_timeout",
                "sqlstate": getattr(
                    getattr(exc, "orig", None),
                    "sqlstate",
                    None,
                ),
                "lock_wait": lock_report,
                "index_present": current is not None,
                "index_valid": (
                    bool(current["is_valid"])
                    if current is not None
                    else None
                ),
                "index_ready": (
                    bool(current["is_ready"])
                    if current is not None
                    else None
                ),
            }
            attempts.append(record)
            _write_json(attempts_path, attempts)

            if current is not None and _compact_definition_ok(current):
                return attempts

            if current is not None:
                _drop_compact_index_with_retries(
                    engine,
                    settings,
                    table_name=table_name,
                    index_name=index_name,
                    evidence_dir=evidence_dir,
                )

            if attempt >= INDEX_LOCK_RETRY_ATTEMPTS:
                raise RuntimeError(
                    "compact index lock retries exhausted for "
                    f"{index_name}"
                ) from exc

            time.sleep(INDEX_LOCK_RETRY_SLEEP_SECONDS)
            continue

        attempts.append(
            {
                "attempt": attempt,
                "result": "created",
                "lock_wait": lock_report,
            }
        )
        _write_json(attempts_path, attempts)
        return attempts

    raise RuntimeError(
        f"compact index creation did not complete for {index_name}"
    )


def _drop_parent_primary_constraint(settings: Settings) -> None:
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "SELECT set_config('lock_timeout', :value, true)"
                ),
                {"value": f"{DDL_LOCK_TIMEOUT_SECONDS}s"},
            )
            connection.execute(
                text(
                    "SELECT set_config('statement_timeout', :value, true)"
                ),
                {"value": f"{DROP_CONSTRAINT_TIMEOUT_SECONDS}s"},
            )
            connection.execute(
                text(
                    """
                    ALTER TABLE raw_event_dedupe
                    DROP CONSTRAINT raw_event_dedupe_pkey
                    """
                )
            )
    finally:
        engine.dispose()


def _stop_recorder() -> None:
    _systemctl("stop", RECORDER_UNIT)
    if _is_active(RECORDER_UNIT):
        raise RuntimeError("recorder remained active after stop")


def _start_recorder() -> None:
    _systemctl("start", RECORDER_UNIT)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if _is_active(RECORDER_UNIT):
            return
        time.sleep(1)
    raise RuntimeError("recorder did not become active")


def _run_soak(repo: Path, env_file: Path) -> dict[str, Any]:
    command = (
        'set -a; source "$1"; set +a; '
        'exec "$2" "$3" --hours 0.01 --minimum-hours 0.008'
    )
    result = _run(
        "sudo",
        "-u",
        "bp",
        "bash",
        "-c",
        command,
        "_",
        str(env_file),
        str(repo / ".venv/bin/python"),
        str(repo / "scripts/soak_report.py"),
    )
    payload = json.loads(result.stdout)
    if payload.get("passed") is not True:
        raise RuntimeError("post-migration recorder soak did not pass")
    return payload


def _git_head(repo: Path) -> str:
    return _run("git", "-C", str(repo), "rev-parse", "HEAD").stdout.strip()


def run_rollout(args: argparse.Namespace) -> dict[str, Any]:
    if os.geteuid() != 0:
        raise RuntimeError("compact dedupe migration requires root")
    if not args.execute:
        raise RuntimeError("--execute is required")

    repo = Path(args.repo)
    env_file = Path(args.env_file)
    safety_file = Path(args.safety_file)
    if _git_head(repo) != args.expected_deployed_head:
        raise RuntimeError("deployed checkout head changed")

    _require_research_zero_money(env_file, safety_file)
    _require_automatic_promotion_false(repo / "PROJECT_STATE.json")

    settings = Settings(_env_file=str(env_file))
    _require_recorder_binding(settings)
    _require_services_and_timers()

    runtime_source = (repo / "src/bp_engine/storage/recorder.py").read_text(
        encoding="utf-8"
    )
    if "ON CONFLICT DO NOTHING" not in runtime_source:
        raise RuntimeError("deployed recorder is not target-free")
    if "ON CONFLICT (dedupe_key) DO NOTHING" in runtime_source:
        raise RuntimeError("deployed recorder still binds the old conflict target")

    partitioned_source = (
        repo / "src/bp_engine/storage/partitioned_raw.py"
    ).read_text(encoding="utf-8")
    for marker in (
        "_compact_dedupe_indexes_healthy",
        "partitioned dedupe uniqueness contract is missing or unhealthy",
    ):
        if marker not in partitioned_source:
            raise RuntimeError(f"compact steady-runtime marker missing: {marker}")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    evidence_dir = (
        Path(args.evidence_root)
        / f"phase14-v4-compact-dedupe-migration-{stamp}-{args.helper_head}"
    )
    evidence_dir.mkdir(parents=True, mode=0o750, exist_ok=False)

    summary: dict[str, Any] = {
        "report": "v4_compact_dedupe_migration_v1",
        "started_at": datetime.now(UTC).isoformat(),
        "completed_at": None,
        "status": "running",
        "helper_head": args.helper_head,
        "deployed_head": args.expected_deployed_head,
        "readiness_evidence_sha256": args.readiness_evidence_sha256,
        "blocker_evidence_sha256": args.blocker_evidence_sha256,
        "irreversible_boundary_crossed": False,
        "attempted_compact_indexes": [],
        "created_compact_indexes": [],
        "index_attempts": {},
        "quiesced_timers": list(QUIESCED_TIMERS),
        "quiesced_timers_restored": False,
    }
    _write_json(evidence_dir / "rollout.json", summary)

    stopped_timers: list[str] = []
    recorder_stopped = False
    mutation_engine = _mutation_engine(settings)

    try:
        for timer in QUIESCED_TIMERS:
            _systemctl("stop", timer)
            stopped_timers.append(timer)
            if _is_active(timer):
                raise RuntimeError(f"quiesced timer remained active: {timer}")
            if not _is_enabled(timer):
                raise RuntimeError(f"quiesced timer became disabled: {timer}")

        for service in QUIESCED_ONESHOTS:
            _wait_oneshot_idle_success(service, 3600)
        _wait_oneshot_idle_success(DISK_HEALTH_SERVICE, 60)

        pre_state = _state(settings)
        _write_json(evidence_dir / "pre-state.json", pre_state)
        _require_legacy_pre_state(pre_state)

        pre_window = _clean_window(settings)
        _write_json(evidence_dir / "pre-clean-window.json", pre_window)
        _require_clean_window(pre_window)

        for table_name, index_name in zip(
            _expected_tables(),
            _expected_compact_indexes(),
            strict=True,
        ):
            summary["attempted_compact_indexes"].append(index_name)
            _write_json(evidence_dir / "rollout.json", summary)
            attempts = _create_compact_index_with_retries(
                mutation_engine,
                settings,
                table_name=table_name,
                index_name=index_name,
                evidence_dir=evidence_dir,
            )
            summary["index_attempts"][index_name] = attempts
            summary["created_compact_indexes"].append(index_name)
            _write_json(evidence_dir / "rollout.json", summary)

            current = _state(settings)
            matching = [
                row
                for row in current["compact_indexes"]
                if str(row["index_name"]) == index_name
            ]
            if len(matching) != 1 or not _compact_definition_ok(matching[0]):
                raise RuntimeError(
                    f"compact index did not become healthy: {index_name}"
                )

        transition_state = _state(settings)
        _write_json(evidence_dir / "transition-state.json", transition_state)
        _require_transition_state(transition_state)

        mutation_window = _clean_window(settings)
        _write_json(evidence_dir / "mutation-clean-window.json", mutation_window)
        _require_clean_window(mutation_window)

        _stop_recorder()
        recorder_stopped = True

        before_drop = _state(settings)
        _write_json(evidence_dir / "before-drop-state.json", before_drop)
        _require_transition_state(before_drop)

        _drop_parent_primary_constraint(settings)
        summary["irreversible_boundary_crossed"] = True
        _write_json(evidence_dir / "rollout.json", summary)

        after_drop = _state(settings)
        _write_json(evidence_dir / "after-drop-state.json", after_drop)
        _require_compact_post_state(after_drop)

        _start_recorder()
        recorder_stopped = False
        time.sleep(45)

        if _git_head(repo) != args.expected_deployed_head:
            raise RuntimeError("deployed checkout changed after migration")
        if not _is_active(RECORDER_UNIT):
            raise RuntimeError("recorder is not active after migration")

        soak = _run_soak(repo, env_file)
        _write_json(evidence_dir / "post-soak.json", soak)

        post_state = _state(settings)
        _write_json(evidence_dir / "post-state.json", post_state)
        _require_compact_post_state(post_state)

        _require_research_zero_money(env_file, safety_file)
        _require_automatic_promotion_false(repo / "PROJECT_STATE.json")
        summary["status"] = "success"
    except Exception as exc:
        summary["status"] = "failure"
        summary["error"] = f"{type(exc).__name__}: {exc}"
        print(f"EVIDENCE_DIR={evidence_dir}", flush=True)
        raise
    finally:
        mutation_engine.dispose()

        if summary["status"] != "success":
            if bool(summary["irreversible_boundary_crossed"]):
                _run(
                    "systemctl",
                    "stop",
                    RECORDER_UNIT,
                    check=False,
                )
                recorder_stopped = True
                summary["failure_policy"] = (
                    "post-boundary fail-closed: recorder stopped; "
                    "quiesced timers remain stopped; old writer not restored"
                )
            else:
                cleanup_results: dict[str, object] = {}
                cleanup_engine = _mutation_engine(settings)
                try:
                    for index_name in reversed(
                        list(summary["attempted_compact_indexes"])
                    ):
                        try:
                            table_name = index_name.removesuffix(
                                "_digest_uidx"
                            )
                            _drop_compact_index_with_retries(
                                cleanup_engine,
                                settings,
                                table_name=table_name,
                                index_name=index_name,
                                evidence_dir=evidence_dir,
                            )
                            cleanup_results[index_name] = "dropped"
                        except Exception as cleanup_exc:
                            cleanup_results[index_name] = (
                                f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                            )
                finally:
                    cleanup_engine.dispose()
                summary["pre_boundary_cleanup"] = cleanup_results

                if recorder_stopped:
                    start_result = _run(
                        "systemctl",
                        "start",
                        RECORDER_UNIT,
                        check=False,
                    )
                    recorder_stopped = not _is_active(RECORDER_UNIT)
                    summary["recorder_restore"] = {
                        "returncode": start_result.returncode,
                        "active": not recorder_stopped,
                    }

        restore_results: dict[str, dict[str, object]] = {}
        if not bool(summary["irreversible_boundary_crossed"]) or (
            summary["status"] == "success"
        ):
            for timer in reversed(stopped_timers):
                restore = _run(
                    "systemctl",
                    "start",
                    timer,
                    check=False,
                )
                restore_results[timer] = {
                    "returncode": restore.returncode,
                    "active": _is_active(timer),
                }

        summary["timer_restore_results"] = restore_results
        summary["quiesced_timers_restored"] = (
            set(restore_results) == set(QUIESCED_TIMERS)
            and all(
                int(result["returncode"]) == 0 and bool(result["active"])
                for result in restore_results.values()
            )
        )
        if (
            summary["status"] == "success"
            and not summary["quiesced_timers_restored"]
        ):
            summary["status"] = "failure"
            summary["error"] = "required timers were not restored"
            if bool(summary["irreversible_boundary_crossed"]):
                _run("systemctl", "stop", RECORDER_UNIT, check=False)
                summary["failure_policy"] = (
                    "post-boundary fail-closed: recorder stopped because "
                    "timer restoration failed"
                )
        summary["recorder_active_at_end"] = _is_active(RECORDER_UNIT)
        summary["completed_at"] = datetime.now(UTC).isoformat()
        _write_json(evidence_dir / "rollout.json", summary)

    if summary["status"] != "success":
        raise RuntimeError(f"compact dedupe migration failed: {summary}")
    if not summary["quiesced_timers_restored"]:
        raise RuntimeError("compact dedupe migration did not restore timers")

    _require_services_and_timers()
    print(f"EVIDENCE_DIR={evidence_dir}", flush=True)
    return {**summary, "evidence_dir": str(evidence_dir)}


def main() -> int:
    args = parse_args()
    payload = run_rollout(args)
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    print("PHASE14_V4_COMPACT_DEDUPE_MIGRATION_GATE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
