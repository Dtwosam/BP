from __future__ import annotations

import argparse
import time

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from bp_engine.config import Settings
from bp_engine.storage.schema import metadata

INDEX_STATEMENTS = (
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_raw_market_events_received_at_brin
    ON raw_market_events USING brin (received_at)
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_raw_market_events_received_at_id
    ON raw_market_events (received_at, id)
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_market_state_1s_bucket_at
    ON market_state_1s (bucket_at)
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_market_state_1s_feed_bucket
    ON market_state_1s (source, stream, bucket_at)
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_market_state_1s_polymarket_market_lookup
    ON market_state_1s (instrument, asset_id, bucket_at DESC, last_event_at DESC, id DESC)
    WHERE source = 'polymarket' AND stream = 'market'
    """,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install Phase 3 storage indexes on new or existing PostgreSQL hosts"
    )
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--database-ready-timeout-seconds", type=int, default=60)
    return parser.parse_args()


def _wait_for_database(engine: object, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: OperationalError | None = None
    while time.monotonic() < deadline:
        try:
            with engine.connect() as connection:  # type: ignore[attr-defined]
                connection.execute(text("SELECT 1"))
            return
        except OperationalError as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError("database did not become ready before index migration") from last_error


def main() -> int:
    args = parse_args()
    if args.database_ready_timeout_seconds <= 0:
        raise SystemExit("database ready timeout must be greater than zero")

    settings = Settings(_env_file=args.env_file)
    engine = create_engine(settings.database_url)
    _wait_for_database(engine, args.database_ready_timeout_seconds)

    # Fresh databases need their tables before indexes can be installed. Existing
    # hosts are left intact because SQLAlchemy create_all does not alter tables.
    metadata.create_all(engine)

    # Concurrent builds avoid blocking the high-rate recorder on existing hosts.
    # AUTOCOMMIT is required because PostgreSQL forbids CREATE INDEX CONCURRENTLY
    # inside a transaction block.
    concurrent_engine = engine.execution_options(isolation_level="AUTOCOMMIT")
    with concurrent_engine.connect() as connection:
        for statement in INDEX_STATEMENTS:
            connection.execute(text(statement))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
