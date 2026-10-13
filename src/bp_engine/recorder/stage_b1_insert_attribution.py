"""Stage B1 committed per-class insert evidence using an OFFLINE test database.

This is intentionally NOT wired into the recorder, observer, BatchWriter or
normal repository insert path. It splits the same production repository
insert operation by disjoint classes inside one transaction, and only
returns counts AFTER its transaction context successfully exits.

Splitting batches increases SQL/partition-metadata calls and can change
ID/allocation order. Therefore it is NOT an approved production diagnostic.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Engine

from bp_engine.recorder.models import RawEvent
from bp_engine.recorder.stage_b1_observer import _raw_category
from bp_engine.storage.recorder import RecorderRepository

_CATEGORIES = ("trade", "orderbook", "liquidation", "other")
_NON_BULK = "excluded_non_bulk"
_MAX_EVENTS = 64


def _local_test_engine_only(engine: Engine) -> None:
    """Refuse remote DB URLs, including any non-local PostgreSQL hostname."""
    url = engine.url
    if url.get_backend_name() == "sqlite":
        if url.database not in (None, "", ":memory:"):
            raise ValueError("B1 requires an in-memory SQLite test engine")
        return
    if url.get_backend_name() == "postgresql":
        if url.host not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("B1 offline attribution prohibits nonlocal PostgreSQL")
        if url.database != "bp":
            raise ValueError("B1 offline attribution requires the local bp test database")
        return
    raise ValueError("B1 offline attribution requires a local test SQL engine")


def insert_attributed_offline(
    engine: Engine,
    events: Sequence[RawEvent],
    *,
    repository: RecorderRepository | None = None,
) -> dict[str, object]:
    """Insert through the REAL repository, aggregate after successful COMMIT.

    Counts are actual inserted rows returned by RecorderRepository, not the
    number of attempted events. For partitioned PostgreSQL this is the
    raw_event_dedupe RETURNING claim count followed by its raw insert,
    and the enclosing transaction commit must succeed before returning.
    This is deliberately limited to 64 synthetic events and local test DBs.
    """
    _local_test_engine_only(engine)
    if not isinstance(events, (list, tuple)):
        raise ValueError("B1 offline events must be a fixed bounded sequence")
    if not 1 <= len(events) <= _MAX_EVENTS:
        raise ValueError("B1 offline batch must have 1-64 events")
    if any(not isinstance(event, RawEvent) for event in events):
        raise ValueError("B1 offline batch contains a non-RawEvent")

    categories: dict[str, list[RawEvent]] = {
        **{key: [] for key in _CATEGORIES},
        _NON_BULK: [],
    }
    assigned: dict[str, str] = {}
    for event in events:
        category = _raw_category(event) or _NON_BULK
        previous = assigned.setdefault(event.dedupe_key, category)
        if previous != category:
            # This could change the winning row after grouping.
            raise ValueError("B1 cross-category dedupe key collision")
        categories[category].append(event)

    repo = repository or RecorderRepository()
    inserted: dict[str, int] = {}
    # All group inserts share ONE transaction. If any group raises, the
    # transaction rolls back, and no success/row count is returned.
    with engine.begin() as connection:
        for category, batch in categories.items():
            inserted[category] = repo.insert_events(connection, batch) if batch else 0
            if not 0 <= inserted[category] <= len(batch):
                raise ValueError("B1 insert result outside expected bounds")

    # Counts leave the method ONLY after the transaction commit succeeds.
    # No dedupe keys, event payloads or class-specific prices are returned.
    return {
        "report": "stage_b1_local_inserted_rows_after_commit_v1",
        "status": "LOCAL_TEST_TRANSACTION_COMMITTED_NO_GO",
        "attempted_by_class": {key: len(batch) for key, batch in categories.items()},
        "inserted_by_class": inserted,
        "deduplicated_by_class": {
            key: len(categories[key]) - count for key, count in inserted.items()
        },
        "storage_insert_method": "existing_recorder_repository_grouped_in_one_transaction",
        "real_local_db_commit_observed": True,
        "production_insert_attribution_proven": False,
        "production_execution_authorized": False,
        "queries_or_mutations_on_production": False,
        "limitations": [
            "Grouping changes SQL statement count and insertion/ID ordering.",
            "Local dedupe and commit do not prove production socket/storage behavior.",
            "The ordinary production recorder does not call this function.",
        ],
    }
