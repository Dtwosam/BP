from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, create_engine, text

from bp_engine.config import Settings
from bp_engine.recorder.models import RawEvent
from bp_engine.storage.partitioned_raw import (
    RawStorageMode,
    ensure_partitioned_raw_storage,
    list_raw_partitions,
    raw_storage_mode,
    rollback_partitioned_raw_storage,
)
from bp_engine.storage.recorder import RecorderRepository

_RAW_TABLES = {
    "raw_market_events",
    "raw_market_events_legacy",
    "raw_event_dedupe",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrate, verify, or roll back Phase 14 partitioned raw storage"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    apply = subparsers.add_parser("apply", help="Migrate retained legacy raw storage")
    apply.add_argument("--env-file", required=True)

    verify = subparsers.add_parser("verify", help="Verify partitioned raw storage")
    verify.add_argument("--env-file", required=True)

    rollback = subparsers.add_parser("rollback", help="Restore preserved legacy raw storage")
    rollback.add_argument("--env-file", required=True)
    return parser


def _settings(env_file: str) -> Settings:
    return Settings(_env_file=env_file)


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _raw_stats(engine: Engine, table_name: str) -> dict[str, Any]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                f"""
                SELECT
                    count(*) AS row_count,
                    min(id) AS min_id,
                    max(id) AS max_id,
                    min(received_at) AS min_received_at,
                    max(received_at) AS max_received_at,
                    count(DISTINCT dedupe_key) AS distinct_dedupe_keys
                FROM {table_name}
                """
            )
        ).mappings().one()
    return dict(row)


def _feed_ranges(engine: Engine, table_name: str) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                f"""
                SELECT
                    source,
                    stream,
                    count(*) AS row_count,
                    min(id) AS min_id,
                    max(id) AS max_id,
                    min(received_at) AS min_received_at,
                    max(received_at) AS max_received_at
                FROM {table_name}
                GROUP BY source, stream
                ORDER BY source, stream
                """
            )
        ).mappings()
        return [dict(row) for row in rows]


def _exact_raw_diff_count(engine: Engine) -> int:
    columns = """
        id,
        source,
        stream,
        instrument,
        event_type,
        source_timestamp,
        received_at,
        sequence,
        market_id,
        asset_id,
        payload,
        dedupe_key
    """
    with engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    f"""
                    SELECT count(*)
                    FROM (
                        (SELECT {columns} FROM raw_market_events_legacy
                         EXCEPT ALL
                         SELECT {columns} FROM raw_market_events)
                        UNION ALL
                        (SELECT {columns} FROM raw_market_events
                         EXCEPT ALL
                         SELECT {columns} FROM raw_market_events_legacy)
                    ) AS diff
                    """
                )
            ).scalar_one()
        )


def non_raw_table_counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        table_names = [
            str(name)
            for name in connection.execute(
                text(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = current_schema()
                      AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                    """
                )
            ).scalars()
            if str(name) not in _RAW_TABLES
            and not str(name).startswith("raw_market_events_")
            and not str(name).startswith("raw_event_dedupe_h")
        ]
        return {
            table_name: int(
                connection.execute(
                    text(f'SELECT count(*) FROM "{table_name}"')
                ).scalar_one()
            )
            for table_name in table_names
        }


def _ledger_stats(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT
                    count(*) AS row_count,
                    count(DISTINCT dedupe_key) AS distinct_dedupe_keys
                FROM raw_event_dedupe
                """
            )
        ).mappings().one()
        mapping_diff = connection.execute(
            text(
                """
                SELECT count(*)
                FROM (
                    (SELECT dedupe_key, id, received_at FROM raw_event_dedupe
                     EXCEPT ALL
                     SELECT dedupe_key, id, received_at FROM raw_market_events)
                    UNION ALL
                    (SELECT dedupe_key, id, received_at FROM raw_market_events
                     EXCEPT ALL
                     SELECT dedupe_key, id, received_at FROM raw_event_dedupe)
                ) AS diff
                """
            )
        ).scalar_one()
    return {
        "row_count": int(row["row_count"]),
        "distinct_dedupe_keys": int(row["distinct_dedupe_keys"]),
        "mapping_diff_count": int(mapping_diff),
    }


def _sequence_position(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text("SELECT last_value FROM raw_market_events_id_seq_v2")
            ).scalar_one()
        )


def _synthetic_transaction_checks(engine: Engine, now: datetime) -> dict[str, Any]:
    repository = RecorderRepository()
    event = RawEvent.build(
        source="phase14_storage_verify",
        stream="synthetic",
        instrument="SYNTHETIC",
        event_type="partition_probe",
        source_timestamp=now,
        received_at=now,
        sequence=now.isoformat(),
        payload={"verification": "rolled_back"},
    )
    expected_partition = f"raw_market_events_{now:%Y%m%d}_{now:%H}"

    connection = engine.connect()
    transaction = connection.begin()
    try:
        first = repository.insert_events(connection, [event])
        second = repository.insert_events(connection, [event])
        routed_partition = str(
            connection.execute(
                text(
                    """
                    SELECT tableoid::regclass::text
                    FROM raw_market_events
                    WHERE dedupe_key = :dedupe_key
                    """
                ),
                {"dedupe_key": event.dedupe_key},
            ).scalar_one()
        )
        if first != 1 or second != 0:
            raise RuntimeError("synthetic duplicate suppression verification failed")
        if routed_partition != expected_partition:
            raise RuntimeError(
                "synthetic event routed to unexpected partition: "
                f"{routed_partition!r} != {expected_partition!r}"
            )
    finally:
        transaction.rollback()
        connection.close()

    with engine.connect() as verify_connection:
        committed_raw = int(
            verify_connection.execute(
                text(
                    "SELECT count(*) FROM raw_market_events WHERE dedupe_key = :dedupe_key"
                ),
                {"dedupe_key": event.dedupe_key},
            ).scalar_one()
        )
        committed_ledger = int(
            verify_connection.execute(
                text(
                    "SELECT count(*) FROM raw_event_dedupe WHERE dedupe_key = :dedupe_key"
                ),
                {"dedupe_key": event.dedupe_key},
            ).scalar_one()
        )
    if committed_raw != 0 or committed_ledger != 0:
        raise RuntimeError("synthetic verification transaction leaked committed rows")

    return {
        "synthetic_duplicate_suppressed": True,
        "synthetic_partition_routing_verified": True,
        "synthetic_rows_committed": 0,
        "routed_partition": expected_partition,
    }


def _verify(engine: Engine) -> dict[str, Any]:
    now = datetime.now(UTC)
    with engine.connect() as connection:
        mode = raw_storage_mode(connection)
    if mode is not RawStorageMode.PARTITIONED:
        raise RuntimeError(f"expected partitioned raw storage, found {mode.value!r}")

    legacy = _raw_stats(engine, "raw_market_events_legacy")
    current = _raw_stats(engine, "raw_market_events")
    legacy_feeds = _feed_ranges(engine, "raw_market_events_legacy")
    current_feeds = _feed_ranges(engine, "raw_market_events")
    exact_diff_count = _exact_raw_diff_count(engine)
    ledger = _ledger_stats(engine)
    sequence_position = _sequence_position(engine)

    if current != legacy:
        raise RuntimeError("raw aggregate parity verification failed")
    if current_feeds != legacy_feeds:
        raise RuntimeError("raw per-feed parity verification failed")
    if exact_diff_count != 0:
        raise RuntimeError("raw exact-row parity verification failed")
    if ledger["row_count"] != int(current["row_count"]):
        raise RuntimeError("dedupe ledger cardinality does not match raw rows")
    if ledger["distinct_dedupe_keys"] != ledger["row_count"]:
        raise RuntimeError("dedupe ledger contains duplicate keys")
    if ledger["mapping_diff_count"] != 0:
        raise RuntimeError("dedupe ledger mapping differs from raw rows")
    max_id = int(current["max_id"] or 0)
    if sequence_position < max_id:
        raise RuntimeError("shared event sequence is behind retained raw IDs")

    partitions = list_raw_partitions(engine)
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    required_hours = {
        current_hour + timedelta(hours=offset)
        for offset in range(3)
    }
    available_hours = {partition.start_at for partition in partitions}
    if not required_hours.issubset(available_hours):
        raise RuntimeError("current + two future raw partitions are not provisioned")

    synthetic = _synthetic_transaction_checks(engine, now)
    return {
        "storage_mode": mode.value,
        "raw_stats": current,
        "raw_feed_ranges": current_feeds,
        "exact_raw_diff_count": exact_diff_count,
        "dedupe": ledger,
        "sequence_position": sequence_position,
        "partition_count": len(partitions),
        "current_plus_two_future_present": True,
        **synthetic,
    }


def _apply(engine: Engine) -> dict[str, Any]:
    before_non_raw = non_raw_table_counts(engine)
    with engine.connect() as connection:
        before_mode = raw_storage_mode(connection)
    if before_mode is not RawStorageMode.LEGACY:
        raise RuntimeError(
            f"apply requires populated legacy raw storage, found {before_mode.value!r}"
        )

    setup = ensure_partitioned_raw_storage(
        engine,
        now=datetime.now(UTC),
        migrate_existing=True,
    )
    if setup.mode is not RawStorageMode.PARTITIONED:
        raise RuntimeError("raw storage migration did not enter partitioned mode")
    if setup.rollback_table != "raw_market_events_legacy":
        raise RuntimeError("migration did not retain the required rollback table")

    verification = _verify(engine)
    after_non_raw = non_raw_table_counts(engine)
    if after_non_raw != before_non_raw:
        raise RuntimeError("non-raw table counts changed during migration")

    return {
        "action": "apply",
        "migrated_rows": setup.migrated_rows,
        "rollback_table": setup.rollback_table,
        "rollback_material_retained": True,
        "non_raw_table_counts_before": before_non_raw,
        "non_raw_table_counts_after": after_non_raw,
        "non_raw_table_counts": after_non_raw,
        "verification": verification,
    }


def _rollback(engine: Engine) -> dict[str, Any]:
    before_non_raw = non_raw_table_counts(engine)
    result = rollback_partitioned_raw_storage(engine)
    after_non_raw = non_raw_table_counts(engine)
    if before_non_raw != after_non_raw:
        raise RuntimeError("non-raw table counts changed during rollback")
    with engine.connect() as connection:
        mode = raw_storage_mode(connection)
    if mode is not RawStorageMode.LEGACY:
        raise RuntimeError("rollback did not restore legacy raw storage")
    return {
        "action": "rollback",
        "storage_mode": mode.value,
        "restored_table": result.restored_table,
        "restored_rows": result.restored_rows,
        "non_raw_table_counts": after_non_raw,
    }


def main() -> int:
    args = _parser().parse_args()
    settings = _settings(args.env_file)
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        if args.command == "apply":
            payload = _apply(engine)
        elif args.command == "verify":
            payload = {
                "action": "verify",
                "non_raw_table_counts": non_raw_table_counts(engine),
                "verification": _verify(engine),
            }
        elif args.command == "rollback":
            payload = _rollback(engine)
        else:
            raise SystemExit(f"unsupported command: {args.command}")
    finally:
        engine.dispose()

    _print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
