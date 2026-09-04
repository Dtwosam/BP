from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import Connection, Engine, text

_DEDUPE_PARTITIONS = 16
_RAW_PARTITION_RE = re.compile(r"^raw_market_events_(\d{8})_(\d{2})$")
_LEGACY_INDEX_RE = re.compile(r"^legacy_\d{2}_(.+)$")
_SEQUENCE_NAME = "raw_market_events_id_seq_v2"
_ROLLBACK_TABLE = "raw_market_events_legacy"

_RAW_COLUMNS = (
    "id",
    "source",
    "stream",
    "instrument",
    "event_type",
    "source_timestamp",
    "received_at",
    "sequence",
    "market_id",
    "asset_id",
    "payload",
    "dedupe_key",
)


class RawStorageMode(StrEnum):
    MISSING = "missing"
    LEGACY = "legacy"
    PARTITIONED = "partitioned"


@dataclass(frozen=True)
class RawStorageSetup:
    mode: RawStorageMode
    migrated_rows: int
    rollback_table: str | None


@dataclass(frozen=True)
class RawPartition:
    name: str
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True)
class RawStorageRollback:
    restored_table: str
    restored_rows: int


def _require_aware_utc(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _floor_hour(value: datetime) -> datetime:
    return _require_aware_utc(value, field="timestamp").replace(
        minute=0,
        second=0,
        microsecond=0,
    )


def _partition_name(start_at: datetime) -> str:
    start = _floor_hour(start_at)
    return f"raw_market_events_{start:%Y%m%d}_{start:%H}"


def _partition_bounds_from_name(name: str) -> tuple[datetime, datetime] | None:
    match = _RAW_PARTITION_RE.fullmatch(name)
    if match is None:
        return None
    start = datetime.strptime(
        f"{match.group(1)}{match.group(2)}",
        "%Y%m%d%H",
    ).replace(tzinfo=UTC)
    return start, start + timedelta(hours=1)


def _quote(connection: Connection, identifier: str) -> str:
    return connection.dialect.identifier_preparer.quote(identifier)


def _timestamp_literal(value: datetime) -> str:
    normalized = _require_aware_utc(value, field="partition bound")
    return f"TIMESTAMPTZ '{normalized.isoformat()}'"


def raw_storage_mode(connection: Connection) -> RawStorageMode:
    if connection.dialect.name != "postgresql":
        return RawStorageMode.LEGACY

    relkind = connection.execute(
        text(
            """
            SELECT c.relkind
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema()
              AND c.relname = 'raw_market_events'
            """
        )
    ).scalar_one_or_none()
    if relkind is None:
        return RawStorageMode.MISSING
    if relkind == "p":
        return RawStorageMode.PARTITIONED
    return RawStorageMode.LEGACY


def _relation_exists(connection: Connection, name: str) -> bool:
    return bool(
        connection.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_class AS c
                    JOIN pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE n.nspname = current_schema()
                      AND c.relname = :name
                )
                """
            ),
            {"name": name},
        ).scalar_one()
    )


def _ensure_sequence(connection: Connection, *, minimum_id: int | None = None) -> None:
    connection.execute(
        text(
            f"""
            CREATE SEQUENCE IF NOT EXISTS {_SEQUENCE_NAME}
            AS BIGINT
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
            """
        )
    )
    if minimum_id is None or minimum_id <= 0:
        return
    last_value = int(
        connection.execute(
            text(f"SELECT last_value FROM {_SEQUENCE_NAME}")
        ).scalar_one()
    )
    if last_value < minimum_id:
        connection.execute(
            text(f"SELECT setval('{_SEQUENCE_NAME}', :value, true)"),
            {"value": minimum_id},
        )


def _create_raw_parent(connection: Connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS raw_market_events (
                id BIGINT NOT NULL,
                source VARCHAR(32) NOT NULL,
                stream VARCHAR(64) NOT NULL,
                instrument VARCHAR(128) NOT NULL,
                event_type VARCHAR(64) NOT NULL,
                source_timestamp TIMESTAMPTZ NULL,
                received_at TIMESTAMPTZ NOT NULL,
                sequence VARCHAR(128) NULL,
                market_id TEXT NULL,
                asset_id TEXT NULL,
                payload JSONB NOT NULL,
                dedupe_key VARCHAR(80) NOT NULL,
                CONSTRAINT ix_raw_market_events_received_at_id
                    PRIMARY KEY (received_at, id)
            ) PARTITION BY RANGE (received_at)
            """
        )
    )


def _ensure_raw_parent_indexes(connection: Connection) -> None:
    statements = (
        """
        CREATE INDEX IF NOT EXISTS ix_raw_market_events_received_at_brin
        ON raw_market_events USING BRIN (received_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_raw_market_events_source_received
        ON raw_market_events (source, stream, received_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_raw_market_events_market_received
        ON raw_market_events (market_id, received_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_raw_market_events_dedupe_key
        ON raw_market_events (dedupe_key)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_raw_market_events_pm_book_replay_anchor
        ON raw_market_events (instrument, asset_id, received_at DESC, id DESC)
        WHERE source = 'polymarket'
          AND stream = 'market'
          AND event_type = 'book'
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_raw_market_events_pm_price_change_replay
        ON raw_market_events (instrument, received_at, id)
        WHERE source = 'polymarket'
          AND stream = 'market'
          AND event_type = 'price_change'
        """,
    )
    for statement in statements:
        connection.execute(text(statement))


def _create_dedupe_parent(connection: Connection) -> None:
    connection.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS raw_event_dedupe (
                dedupe_key VARCHAR(80) NOT NULL,
                id BIGINT NOT NULL DEFAULT nextval('{_SEQUENCE_NAME}'),
                received_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (dedupe_key)
            ) PARTITION BY HASH (dedupe_key)
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_raw_event_dedupe_received_at
            ON raw_event_dedupe (received_at)
            """
        )
    )
    for remainder in range(_DEDUPE_PARTITIONS):
        name = f"raw_event_dedupe_h{remainder:02d}"
        connection.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {name}
                PARTITION OF raw_event_dedupe
                FOR VALUES WITH (
                    MODULUS {_DEDUPE_PARTITIONS},
                    REMAINDER {remainder}
                )
                """
            )
        )


def ensure_hour_partitions(
    connection: Connection,
    *,
    start_at: datetime,
    hours_ahead: int,
) -> tuple[RawPartition, ...]:
    if connection.dialect.name != "postgresql":
        raise ValueError("hourly raw partitions require PostgreSQL")
    if hours_ahead < 0:
        raise ValueError("hours_ahead must be non-negative")
    if raw_storage_mode(connection) is not RawStorageMode.PARTITIONED:
        raise RuntimeError("raw_market_events is not partitioned")

    start = _floor_hour(start_at)
    created: list[RawPartition] = []
    for offset in range(hours_ahead + 1):
        lower = start + timedelta(hours=offset)
        upper = lower + timedelta(hours=1)
        name = _partition_name(lower)
        connection.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {name}
                PARTITION OF raw_market_events
                FOR VALUES FROM ({_timestamp_literal(lower)})
                TO ({_timestamp_literal(upper)})
                """
            )
        )
        created.append(RawPartition(name=name, start_at=lower, end_at=upper))
    return tuple(created)


def _list_raw_partitions_connection(connection: Connection) -> tuple[RawPartition, ...]:
    if connection.dialect.name != "postgresql":
        return ()
    names = connection.execute(
        text(
            """
            SELECT child.relname
            FROM pg_inherits
            JOIN pg_class AS parent ON parent.oid = pg_inherits.inhparent
            JOIN pg_namespace AS parent_ns ON parent_ns.oid = parent.relnamespace
            JOIN pg_class AS child ON child.oid = pg_inherits.inhrelid
            WHERE parent_ns.nspname = current_schema()
              AND parent.relname = 'raw_market_events'
            ORDER BY child.relname
            """
        )
    ).scalars()
    partitions: list[RawPartition] = []
    for name in names:
        bounds = _partition_bounds_from_name(str(name))
        if bounds is None:
            continue
        start_at, end_at = bounds
        partitions.append(
            RawPartition(
                name=str(name),
                start_at=start_at,
                end_at=end_at,
            )
        )
    partitions.sort(key=lambda item: item.start_at)
    return tuple(partitions)


def list_raw_partitions(bind: Engine | Connection) -> tuple[RawPartition, ...]:
    if isinstance(bind, Connection):
        return _list_raw_partitions_connection(bind)
    with bind.connect() as connection:
        return _list_raw_partitions_connection(connection)


def _rename_legacy_indexes(connection: Connection) -> None:
    names = connection.execute(
        text(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND tablename = :table_name
            ORDER BY indexname
            """
        ),
        {"table_name": _ROLLBACK_TABLE},
    ).scalars()
    for position, raw_name in enumerate(names):
        old_name = str(raw_name)
        new_name = f"legacy_{position:02d}_{old_name}"[:63]
        connection.execute(
            text(
                f"ALTER INDEX {_quote(connection, old_name)} "
                f"RENAME TO {_quote(connection, new_name)}"
            )
        )


def _legacy_stats(connection: Connection, table_name: str) -> tuple[object, ...]:
    table = _quote(connection, table_name)
    return tuple(
        connection.execute(
            text(
                f"""
                SELECT
                    count(*),
                    min(id),
                    max(id),
                    min(received_at),
                    max(received_at),
                    count(DISTINCT dedupe_key)
                FROM {table}
                """
            )
        ).one()
    )


def _create_partitioned_objects(connection: Connection, *, minimum_id: int | None) -> None:
    _ensure_sequence(connection, minimum_id=minimum_id)
    _create_raw_parent(connection)
    _ensure_raw_parent_indexes(connection)
    _create_dedupe_parent(connection)


def _ensure_partition_span(
    connection: Connection,
    *,
    first_received_at: datetime,
    last_received_at: datetime,
) -> None:
    first = _floor_hour(first_received_at)
    last = _floor_hour(last_received_at)
    current = first
    while current <= last:
        ensure_hour_partitions(connection, start_at=current, hours_ahead=0)
        current += timedelta(hours=1)


def ensure_partitioned_raw_storage(
    engine: Engine,
    *,
    now: datetime,
    migrate_existing: bool = False,
) -> RawStorageSetup:
    if engine.dialect.name != "postgresql":
        raise ValueError("partitioned raw storage requires PostgreSQL")
    current_hour = _floor_hour(now)

    with engine.begin() as connection:
        mode = raw_storage_mode(connection)
        if mode is RawStorageMode.PARTITIONED:
            max_id = connection.execute(text("SELECT max(id) FROM raw_market_events")).scalar_one()
            _create_partitioned_objects(
                connection,
                minimum_id=int(max_id) if max_id is not None else None,
            )
            ensure_hour_partitions(
                connection,
                start_at=current_hour,
                hours_ahead=2,
            )
            return RawStorageSetup(
                mode=RawStorageMode.PARTITIONED,
                migrated_rows=0,
                rollback_table=None,
            )

        if mode is RawStorageMode.MISSING:
            _create_partitioned_objects(connection, minimum_id=None)
            ensure_hour_partitions(
                connection,
                start_at=current_hour,
                hours_ahead=2,
            )
            return RawStorageSetup(
                mode=RawStorageMode.PARTITIONED,
                migrated_rows=0,
                rollback_table=None,
            )

        row_count = int(
            connection.execute(text("SELECT count(*) FROM raw_market_events")).scalar_one()
        )
        if row_count > 0 and not migrate_existing:
            return RawStorageSetup(
                mode=RawStorageMode.LEGACY,
                migrated_rows=0,
                rollback_table=None,
            )

        if row_count == 0:
            connection.execute(text("DROP TABLE raw_market_events CASCADE"))
            _create_partitioned_objects(connection, minimum_id=None)
            ensure_hour_partitions(
                connection,
                start_at=current_hour,
                hours_ahead=2,
            )
            return RawStorageSetup(
                mode=RawStorageMode.PARTITIONED,
                migrated_rows=0,
                rollback_table=None,
            )

        if _relation_exists(connection, _ROLLBACK_TABLE):
            raise RuntimeError(
                f"refusing migration because rollback table {_ROLLBACK_TABLE!r} already exists"
            )

        source_stats = _legacy_stats(connection, "raw_market_events")
        source_max_id = int(source_stats[2]) if source_stats[2] is not None else None
        first_received_at = source_stats[3]
        last_received_at = source_stats[4]
        if not isinstance(first_received_at, datetime) or not isinstance(
            last_received_at,
            datetime,
        ):
            raise RuntimeError("non-empty legacy raw table has invalid receive-time bounds")

        connection.execute(
            text(f"ALTER TABLE raw_market_events RENAME TO {_ROLLBACK_TABLE}")
        )
        _rename_legacy_indexes(connection)
        _create_partitioned_objects(connection, minimum_id=source_max_id)
        _ensure_partition_span(
            connection,
            first_received_at=first_received_at,
            last_received_at=last_received_at,
        )
        ensure_hour_partitions(
            connection,
            start_at=current_hour,
            hours_ahead=2,
        )

        column_list = ", ".join(_RAW_COLUMNS)
        connection.execute(
            text(
                f"""
                INSERT INTO raw_market_events ({column_list})
                SELECT {column_list}
                FROM {_ROLLBACK_TABLE}
                ORDER BY received_at, id
                """
            )
        )
        connection.execute(
            text(
                f"""
                INSERT INTO raw_event_dedupe (dedupe_key, id, received_at)
                SELECT dedupe_key, id, received_at
                FROM {_ROLLBACK_TABLE}
                ORDER BY received_at, id
                """
            )
        )

        destination_stats = _legacy_stats(connection, "raw_market_events")
        dedupe_count = int(
            connection.execute(text("SELECT count(*) FROM raw_event_dedupe")).scalar_one()
        )
        if destination_stats != source_stats or dedupe_count != row_count:
            raise RuntimeError(
                "partitioned raw migration parity validation failed"
            )

        return RawStorageSetup(
            mode=RawStorageMode.PARTITIONED,
            migrated_rows=row_count,
            rollback_table=_ROLLBACK_TABLE,
        )



def _restore_legacy_indexes(connection: Connection) -> None:
    names = connection.execute(
        text(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND tablename = 'raw_market_events'
            ORDER BY indexname
            """
        )
    ).scalars()
    for raw_name in names:
        old_name = str(raw_name)
        match = _LEGACY_INDEX_RE.fullmatch(old_name)
        if match is None:
            continue
        restored_name = match.group(1)
        connection.execute(
            text(
                f"ALTER INDEX {_quote(connection, old_name)} "
                f"RENAME TO {_quote(connection, restored_name)}"
            )
        )


def rollback_partitioned_raw_storage(engine: Engine) -> RawStorageRollback:
    if engine.dialect.name != "postgresql":
        raise ValueError("partitioned raw rollback requires PostgreSQL")

    with engine.begin() as connection:
        if raw_storage_mode(connection) is not RawStorageMode.PARTITIONED:
            raise RuntimeError("raw_market_events is not partitioned")
        if not _relation_exists(connection, _ROLLBACK_TABLE):
            raise RuntimeError(
                f"rollback table {_ROLLBACK_TABLE!r} is missing"
            )

        rollback_stats = _legacy_stats(connection, _ROLLBACK_TABLE)
        rollback_rows = int(rollback_stats[0])

        connection.execute(text("DROP TABLE raw_market_events CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS raw_event_dedupe CASCADE"))
        connection.execute(text(f"DROP SEQUENCE IF EXISTS {_SEQUENCE_NAME} CASCADE"))
        connection.execute(
            text(f"ALTER TABLE {_ROLLBACK_TABLE} RENAME TO raw_market_events")
        )
        _restore_legacy_indexes(connection)

        restored_stats = _legacy_stats(connection, "raw_market_events")
        if restored_stats != rollback_stats:
            raise RuntimeError("raw rollback parity validation failed")
        if raw_storage_mode(connection) is not RawStorageMode.LEGACY:
            raise RuntimeError("raw rollback did not restore legacy storage mode")

        return RawStorageRollback(
            restored_table="raw_market_events",
            restored_rows=rollback_rows,
        )


def drop_raw_partition(
    connection: Connection,
    *,
    start_at: datetime,
    end_at: datetime,
) -> str | None:
    start = _floor_hour(start_at)
    end = _require_aware_utc(end_at, field="end_at")
    if end != start + timedelta(hours=1):
        raise ValueError("raw partition retirement requires exactly one UTC hour")
    name = _partition_name(start)
    exists = bool(
        connection.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_inherits
                    JOIN pg_class AS parent ON parent.oid = pg_inherits.inhparent
                    JOIN pg_class AS child ON child.oid = pg_inherits.inhrelid
                    JOIN pg_namespace AS parent_ns ON parent_ns.oid = parent.relnamespace
                    WHERE parent_ns.nspname = current_schema()
                      AND parent.relname = 'raw_market_events'
                      AND child.relname = :name
                )
                """
            ),
            {"name": name},
        ).scalar_one()
    )
    if not exists:
        return None
    connection.execute(text(f"DROP TABLE {_quote(connection, name)}"))
    return name
