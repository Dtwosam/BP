from __future__ import annotations

import argparse
import json
import math
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from bp_engine.config import Settings, TradingMode

INDEX_SUFFIX = "_v4_source_lookup_idx"
BENCHMARK_REQUESTED_AT = datetime(2026, 10, 7, 20, 49, tzinfo=UTC)
BENCHMARK_PARTITION = "raw_market_events_20261007_20"
BENCHMARK_VENUE = ("bybit", "spot", "BTCUSDT")
EXPECTED_RECORDER_BATCH_SIZE = 100


def _partition_name(value: datetime) -> str:
    value = value.astimezone(UTC).replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    return f"raw_market_events_{value:%Y%m%d}_{value:%H}"


def _index_name(partition_name: str) -> str:
    return f"{partition_name}{INDEX_SUFFIX}"


def _quote_identifier(connection, value: str) -> str:
    return connection.dialect.identifier_preparer.quote(value)


def _target_partitions(now: datetime) -> tuple[str, ...]:
    hour = now.astimezone(UTC).replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    starts = (
        BENCHMARK_REQUESTED_AT.replace(
            minute=0,
            second=0,
            microsecond=0,
        ),
        hour - timedelta(hours=1),
        hour,
        hour + timedelta(hours=1),
        hour + timedelta(hours=2),
    )
    return tuple(dict.fromkeys(_partition_name(value) for value in starts))


def _validate_settings(settings: Settings) -> None:
    if settings.mode is not TradingMode.RESEARCH:
        raise SystemExit("MODE must remain research")
    if settings.live_trading_enabled:
        raise SystemExit("LIVE_TRADING_ENABLED must remain false")
    if settings.max_trade_size_usd != 0:
        raise SystemExit("MAX_TRADE_SIZE_USD must remain 0")
    if settings.max_daily_loss_usd != 0:
        raise SystemExit("MAX_DAILY_LOSS_USD must remain 0")
    if settings.recorder_batch_size != EXPECTED_RECORDER_BATCH_SIZE:
        raise SystemExit(
            "RECORDER_BATCH_SIZE must remain "
            f"{EXPECTED_RECORDER_BATCH_SIZE}"
        )


def _attached_partition(connection, name: str) -> bool:
    return bool(
        connection.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_inherits AS inheritance
                    JOIN pg_class AS parent
                      ON parent.oid = inheritance.inhparent
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = parent.relnamespace
                    JOIN pg_class AS child
                      ON child.oid = inheritance.inhrelid
                    WHERE namespace.nspname = current_schema()
                      AND parent.relname = 'raw_market_events'
                      AND child.relname = :name
                )
                """
            ),
            {"name": name},
        ).scalar_one()
    )


def _index_state(connection, name: str) -> dict[str, Any] | None:
    row = connection.execute(
        text(
            """
            SELECT
                index_relation.relname AS index_name,
                table_relation.relname AS table_name,
                pg_get_indexdef(index_relation.oid) AS indexdef,
                index_meta.indisvalid,
                index_meta.indisready,
                pg_relation_size(index_relation.oid) AS index_bytes
            FROM pg_index AS index_meta
            JOIN pg_class AS index_relation
              ON index_relation.oid = index_meta.indexrelid
            JOIN pg_class AS table_relation
              ON table_relation.oid = index_meta.indrelid
            JOIN pg_namespace AS namespace
              ON namespace.oid = table_relation.relnamespace
            WHERE namespace.nspname = current_schema()
              AND index_relation.relname = :name
            """
        ),
        {"name": name},
    ).mappings().one_or_none()
    return None if row is None else dict(row)


def _validate_index_state(
    row: dict[str, Any],
    *,
    partition_name: str,
    index_name: str,
) -> None:
    if row.get("table_name") != partition_name:
        raise SystemExit(
            f"index {index_name} belongs to unexpected table "
            f"{row.get('table_name')}"
        )
    if row.get("indisvalid") is not True:
        raise SystemExit(f"index {index_name} is not valid")
    if row.get("indisready") is not True:
        raise SystemExit(f"index {index_name} is not ready")
    normalized = " ".join(str(row.get("indexdef") or "").split())
    required = (
        "(source, stream, instrument, received_at DESC, id DESC)",
        "WHERE (source_timestamp IS NOT NULL)",
    )
    missing = [fragment for fragment in required if fragment not in normalized]
    if missing:
        raise SystemExit(
            f"index {index_name} definition drifted; missing {missing}: "
            f"{normalized}"
        )


def _create_index(connection, partition_name: str) -> str:
    index_name = _index_name(partition_name)
    quoted_index = _quote_identifier(connection, index_name)
    quoted_partition = _quote_identifier(connection, partition_name)
    connection.execute(
        text(
            f"""
            CREATE INDEX CONCURRENTLY {quoted_index}
            ON {quoted_partition} (
                source,
                stream,
                instrument,
                received_at DESC,
                id DESC
            )
            WHERE source_timestamp IS NOT NULL
            """
        )
    )
    return index_name


def _drop_index(connection, index_name: str) -> None:
    quoted_index = _quote_identifier(connection, index_name)
    connection.execute(
        text(f"DROP INDEX CONCURRENTLY IF EXISTS {quoted_index}")
    )


def _plan_index_names(value: Any) -> set[str]:
    found: set[str] = set()

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            index_name = item.get("Index Name")
            if isinstance(index_name, str):
                found.add(index_name)
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return found


def _benchmark_query(connection) -> dict[str, Any]:
    source, stream, instrument = BENCHMARK_VENUE
    requested_at = BENCHMARK_REQUESTED_AT
    params = {
        "source": source,
        "stream": stream,
        "instrument": instrument,
        "received_lower": requested_at - timedelta(seconds=4),
        "requested_at": requested_at,
        "source_lower": requested_at - timedelta(seconds=2),
        "source_upper": requested_at + timedelta(seconds=1),
    }
    query = """
        SELECT
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
        FROM raw_market_events
        WHERE source = :source
          AND stream = :stream
          AND instrument = :instrument
          AND source_timestamp IS NOT NULL
          AND received_at >= :received_lower
          AND received_at <= :requested_at
          AND source_timestamp >= :source_lower
          AND source_timestamp <= :source_upper
          AND event_type IN ('ticker', 'trade')
        ORDER BY received_at DESC, id DESC
    """
    plan = connection.execute(
        text(f"EXPLAIN (FORMAT JSON) {query}"),
        params,
    ).scalar_one()
    expected_index = _index_name(BENCHMARK_PARTITION)
    plan_indexes = sorted(_plan_index_names(plan))
    if expected_index not in plan_indexes:
        raise SystemExit(
            f"benchmark planner did not choose {expected_index}: "
            f"{plan_indexes}"
        )

    connection.execute(text("SET statement_timeout = '2s'"))
    started = time.perf_counter()
    rows = connection.execute(text(query), params).mappings().all()
    elapsed = time.perf_counter() - started
    if not math.isfinite(elapsed) or elapsed >= 2.0:
        raise SystemExit(
            f"benchmark query did not finish inside 2s: {elapsed:.6f}"
        )
    return {
        "partition": BENCHMARK_PARTITION,
        "requested_at": requested_at.isoformat(),
        "expected_index": expected_index,
        "plan_indexes": plan_indexes,
        "row_count": len(rows),
        "elapsed_seconds": round(elapsed, 6),
    }


def _rollback_created_indexes(
    connection,
    created_indexes: list[str],
) -> list[str]:
    dropped: list[str] = []
    for index_name in reversed(created_indexes):
        _drop_index(connection, index_name)
        dropped.append(index_name)
    return dropped


def _run_migration(settings: Settings) -> dict[str, Any]:
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    created_indexes: list[str] = []
    try:
        concurrent_engine = engine.execution_options(
            isolation_level="AUTOCOMMIT"
        )
        with concurrent_engine.connect() as connection:
            connection.execute(text("SET lock_timeout = '0'"))
            connection.execute(text("SET statement_timeout = '15min'"))

            targets = _target_partitions(datetime.now(UTC))
            for partition_name in targets:
                if not _attached_partition(connection, partition_name):
                    raise SystemExit(
                        f"required target partition is missing or detached: "
                        f"{partition_name}"
                    )
                index_name = _index_name(partition_name)
                state = _index_state(connection, index_name)
                if state is not None:
                    _validate_index_state(
                        state,
                        partition_name=partition_name,
                        index_name=index_name,
                    )

            try:
                for partition_name in targets:
                    index_name = _index_name(partition_name)
                    if _index_state(connection, index_name) is None:
                        _create_index(connection, partition_name)
                        created_indexes.append(index_name)
                    state = _index_state(connection, index_name)
                    if state is None:
                        raise SystemExit(
                            f"index missing after create: {index_name}"
                        )
                    _validate_index_state(
                        state,
                        partition_name=partition_name,
                        index_name=index_name,
                    )

                benchmark = _benchmark_query(connection)
                states = []
                for partition_name in targets:
                    index_name = _index_name(partition_name)
                    state = _index_state(connection, index_name)
                    assert state is not None
                    states.append(
                        {
                            "partition": partition_name,
                            "index_name": index_name,
                            "index_bytes": int(state["index_bytes"]),
                            "created_by_rollout": (
                                index_name in created_indexes
                            ),
                        }
                    )
            except BaseException:
                connection.execute(text("SET statement_timeout = '15min'"))
                _rollback_created_indexes(connection, created_indexes)
                raise
    finally:
        engine.dispose()

    return {
        "report": "v4_source_lookup_index_rollout_v1",
        "recorded_at": datetime.now(UTC).isoformat(),
        "target_indexes": states,
        "created_indexes": created_indexes,
        "benchmark": benchmark,
        "safety": {
            "mode": "research",
            "live_trading_enabled": False,
            "max_trade_size_usd": 0,
            "max_daily_loss_usd": 0,
            "recorder_batch_size": EXPECTED_RECORDER_BATCH_SIZE,
            "service_mutation_performed_by_migration": False,
            "order_submission_performed": False,
        },
    }


def _rollback_from_evidence(
    settings: Settings,
    evidence_path: Path,
) -> dict[str, Any]:
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    raw_indexes = payload.get("created_indexes")
    if not isinstance(raw_indexes, list):
        raise SystemExit("rollback evidence created_indexes is invalid")
    created_indexes = [str(value) for value in raw_indexes]

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        concurrent_engine = engine.execution_options(
            isolation_level="AUTOCOMMIT"
        )
        with concurrent_engine.connect() as connection:
            connection.execute(text("SET lock_timeout = '0'"))
            connection.execute(text("SET statement_timeout = '15min'"))
            dropped = _rollback_created_indexes(
                connection,
                created_indexes,
            )
    finally:
        engine.dispose()

    return {
        "report": "v4_source_lookup_index_rollback_v1",
        "recorded_at": datetime.now(UTC).isoformat(),
        "dropped_indexes": dropped,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Install or roll back the child-local V4 source lookup indexes."
        )
    )
    parser.add_argument("--env-file", default="/etc/bp/bp.env")
    parser.add_argument("--rollback-evidence")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    settings = Settings(_env_file=args.env_file)
    _validate_settings(settings)

    if args.rollback_evidence:
        payload = _rollback_from_evidence(
            settings,
            Path(args.rollback_evidence),
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    payload = _run_migration(settings)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
