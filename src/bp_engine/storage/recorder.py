from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Connection, insert

from bp_engine.recorder.models import FeedIncident, RawEvent
from bp_engine.recorder.state import MarketStateSnapshot
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
            from sqlalchemy.dialects.postgresql import insert as postgres_insert

            statement = postgres_insert(raw_market_events).values(rows)
            statement = statement.on_conflict_do_nothing(index_elements=["dedupe_key"])
        else:
            statement = insert(raw_market_events).values(rows)

        result = connection.execute(statement)
        return int(result.rowcount or 0)

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
