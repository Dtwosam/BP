from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import create_engine, text

from bp_engine.config import get_settings

_INDEXES: tuple[tuple[str, str], ...] = (
    (
        "ix_raw_market_events_pm_book_replay_anchor",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS
            ix_raw_market_events_pm_book_replay_anchor
        ON raw_market_events (instrument, asset_id, received_at DESC, id DESC)
        WHERE source = 'polymarket'
          AND stream = 'market'
          AND event_type = 'book'
        """,
    ),
    (
        "ix_raw_market_events_pm_price_change_replay",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS
            ix_raw_market_events_pm_price_change_replay
        ON raw_market_events (instrument, received_at, id)
        WHERE source = 'polymarket'
          AND stream = 'market'
          AND event_type = 'price_change'
        """,
    ),
)


def _invalid_indexes(connection, names: Iterable[str]) -> set[str]:
    return set(
        connection.execute(
            text(
                """
                SELECT index_class.relname
                FROM pg_index AS idx
                JOIN pg_class AS index_class ON index_class.oid = idx.indexrelid
                JOIN pg_class AS table_class ON table_class.oid = idx.indrelid
                WHERE table_class.relname = 'raw_market_events'
                  AND index_class.relname = ANY(:names)
                  AND idx.indisvalid IS FALSE
                """
            ),
            {"names": list(names)},
        ).scalars()
    )


def _ready_indexes(connection, names: Iterable[str]) -> set[str]:
    return set(
        connection.execute(
            text(
                """
                SELECT index_class.relname
                FROM pg_index AS idx
                JOIN pg_class AS index_class ON index_class.oid = idx.indexrelid
                JOIN pg_class AS table_class ON table_class.oid = idx.indrelid
                WHERE table_class.relname = 'raw_market_events'
                  AND index_class.relname = ANY(:names)
                  AND idx.indisvalid IS TRUE
                  AND idx.indisready IS TRUE
                """
            ),
            {"names": list(names)},
        ).scalars()
    )


def main() -> int:
    settings = get_settings()
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        isolation_level="AUTOCOMMIT",
    )
    names = tuple(name for name, _statement in _INDEXES)
    try:
        with engine.connect() as connection:
            for invalid_name in sorted(_invalid_indexes(connection, names)):
                connection.execute(
                    text(f'DROP INDEX CONCURRENTLY IF EXISTS "{invalid_name}"')
                )
            for _name, statement in _INDEXES:
                connection.execute(text(statement))
            ready = _ready_indexes(connection, names)
    finally:
        engine.dispose()

    missing = set(names) - ready
    if missing:
        raise SystemExit(f"Phase 12 replay indexes are not ready: {sorted(missing)}")
    print("PHASE12_REPLAY_INDEXES=READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
