from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Connection, delete, insert, select, text

from bp_engine.recorder.models import FeedIncident, RawEvent
from bp_engine.recorder.state import MarketStateSnapshot
from bp_engine.storage.partitioned_raw import RawStorageMode, raw_storage_mode
from bp_engine.storage.schema import feed_incidents, market_state_1s, raw_market_events


class RecorderRepository:
    def insert_events(self, connection: Connection, events: Sequence[RawEvent]) -> int:
        if not events:
            return 0

        rows = [self._event_values(event) for event in events]
        dialect = connection.dialect.name

        if dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            statement = sqlite_insert(raw_market_events).values(rows)
            statement = statement.on_conflict_do_nothing(index_elements=["dedupe_key"])
        elif dialect == "postgresql":
            if raw_storage_mode(connection) is RawStorageMode.PARTITIONED:
                return self._insert_partitioned_events(connection, rows)

            from sqlalchemy.dialects.postgresql import insert as postgres_insert

            statement = postgres_insert(raw_market_events).values(rows)
            statement = statement.on_conflict_do_nothing(index_elements=["dedupe_key"])
        else:
            statement = insert(raw_market_events).values(rows)

        result = connection.execute(statement)
        return int(result.rowcount or 0)

    @staticmethod
    def _insert_partitioned_events(
        connection: Connection,
        rows: Sequence[dict[str, object]],
    ) -> int:
        unique_rows: dict[str, dict[str, object]] = {}
        for row in rows:
            dedupe_key = str(row["dedupe_key"])
            unique_rows.setdefault(dedupe_key, row)

        values_sql: list[str] = []
        parameters: dict[str, object] = {}
        for index, (dedupe_key, row) in enumerate(unique_rows.items()):
            dedupe_parameter = f"dedupe_key_{index}"
            received_parameter = f"received_at_{index}"
            values_sql.append(f"(:{dedupe_parameter}, :{received_parameter})")
            parameters[dedupe_parameter] = dedupe_key
            parameters[received_parameter] = row["received_at"]

        claims = connection.execute(
            text(
                """
                INSERT INTO raw_event_dedupe (dedupe_key, received_at)
                VALUES
                """
                + ", ".join(values_sql)
                + """
                ON CONFLICT (dedupe_key) DO NOTHING
                RETURNING dedupe_key, id
                """
            ),
            parameters,
        ).mappings()
        claimed_ids = {
            str(claim["dedupe_key"]): int(claim["id"])
            for claim in claims
        }
        if not claimed_ids:
            return 0

        raw_rows = [
            {**row, "id": claimed_ids[dedupe_key]}
            for dedupe_key, row in unique_rows.items()
            if dedupe_key in claimed_ids
        ]
        connection.execute(insert(raw_market_events).values(raw_rows))
        return len(raw_rows)

    def upsert_state_snapshots(
        self,
        connection: Connection,
        snapshots: Sequence[MarketStateSnapshot],
    ) -> int:
        if not snapshots:
            return 0

        rows = [self._state_values(snapshot) for snapshot in snapshots]
        dialect = connection.dialect.name

        if dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            statement = sqlite_insert(market_state_1s).values(rows)
            statement = statement.on_conflict_do_update(
                index_elements=["bucket_at", "state_key"],
                set_={
                    "source": statement.excluded.source,
                    "stream": statement.excluded.stream,
                    "instrument": statement.excluded.instrument,
                    "market_id": statement.excluded.market_id,
                    "asset_id": statement.excluded.asset_id,
                    "last_event_at": statement.excluded.last_event_at,
                    "state": statement.excluded.state,
                },
            )
        elif dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as postgres_insert

            statement = postgres_insert(market_state_1s).values(rows)
            statement = statement.on_conflict_do_update(
                index_elements=["bucket_at", "state_key"],
                set_={
                    "source": statement.excluded.source,
                    "stream": statement.excluded.stream,
                    "instrument": statement.excluded.instrument,
                    "market_id": statement.excluded.market_id,
                    "asset_id": statement.excluded.asset_id,
                    "last_event_at": statement.excluded.last_event_at,
                    "state": statement.excluded.state,
                },
            )
        else:
            statement = insert(market_state_1s).values(rows)

        result = connection.execute(statement)
        return int(result.rowcount or 0)

    def delete_raw_interval_batch(
        self,
        connection: Connection,
        *,
        start_at: datetime,
        end_at: datetime,
        batch_size: int,
    ) -> int:
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")

        if connection.dialect.name == "postgresql":
            result = connection.execute(
                text(
                    """
                    WITH doomed AS (
                        SELECT id
                        FROM raw_market_events
                        WHERE received_at >= :start_at
                          AND received_at < :end_at
                        ORDER BY received_at, id
                        LIMIT :batch_size
                    )
                    DELETE FROM raw_market_events AS target
                    USING doomed
                    WHERE target.id = doomed.id
                    """
                ),
                {"start_at": start_at, "end_at": end_at, "batch_size": batch_size},
            )
            return int(result.rowcount or 0)

        doomed = (
            select(raw_market_events.c.id)
            .where(raw_market_events.c.received_at >= start_at)
            .where(raw_market_events.c.received_at < end_at)
            .order_by(raw_market_events.c.received_at, raw_market_events.c.id)
            .limit(batch_size)
        )
        result = connection.execute(
            delete(raw_market_events).where(raw_market_events.c.id.in_(doomed))
        )
        return int(result.rowcount or 0)

    def delete_state_before_batch(
        self,
        connection: Connection,
        *,
        cutoff_at: datetime,
        batch_size: int,
    ) -> int:
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")

        if connection.dialect.name == "postgresql":
            result = connection.execute(
                text(
                    """
                    WITH doomed AS (
                        SELECT id
                        FROM market_state_1s
                        WHERE bucket_at < :cutoff_at
                        ORDER BY bucket_at, id
                        LIMIT :batch_size
                    )
                    DELETE FROM market_state_1s AS target
                    USING doomed
                    WHERE target.id = doomed.id
                    """
                ),
                {"cutoff_at": cutoff_at, "batch_size": batch_size},
            )
            return int(result.rowcount or 0)

        doomed = (
            select(market_state_1s.c.id)
            .where(market_state_1s.c.bucket_at < cutoff_at)
            .order_by(market_state_1s.c.bucket_at, market_state_1s.c.id)
            .limit(batch_size)
        )
        result = connection.execute(delete(market_state_1s).where(market_state_1s.c.id.in_(doomed)))
        return int(result.rowcount or 0)

    def record_incident(self, connection: Connection, incident: FeedIncident) -> None:
        connection.execute(
            insert(feed_incidents).values(
                source=incident.source,
                stream=incident.stream,
                incident_type=incident.incident_type,
                observed_at=incident.observed_at,
                details=incident.details,
            )
        )

    @staticmethod
    def _event_values(event: RawEvent) -> dict[str, object]:
        return {
            "source": event.source,
            "stream": event.stream,
            "instrument": event.instrument,
            "event_type": event.event_type,
            "source_timestamp": event.source_timestamp,
            "received_at": event.received_at,
            "sequence": event.sequence,
            "market_id": event.market_id,
            "asset_id": event.asset_id,
            "payload": event.payload,
            "dedupe_key": event.dedupe_key,
        }

    @staticmethod
    def _state_values(snapshot: MarketStateSnapshot) -> dict[str, object]:
        return {
            "bucket_at": snapshot.bucket_at,
            "state_key": snapshot.state_key,
            "source": snapshot.source,
            "stream": snapshot.stream,
            "instrument": snapshot.instrument,
            "market_id": snapshot.market_id,
            "asset_id": snapshot.asset_id,
            "last_event_at": snapshot.last_event_at,
            "state": snapshot.state,
        }
