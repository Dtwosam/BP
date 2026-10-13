"""Stage B1 committed per-class insert evidence using an OFFLINE test database.

This is intentionally NOT wired into the recorder, observer, BatchWriter or
normal repository insert path. It splits the same production repository
insert operation by disjoint classes inside one transaction, and only
returns counts AFTER its transaction context successfully exits.

The original grouping helper changes SQL statements and ID allocation.
The later same-batch path reuses partitioned PostgreSQL's existing dedupe
RETURNING claims without any extra SQL; it is still test-only. Neither
helper is an approved production diagnostic.
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


def insert_same_batch_attributed_offline(
    engine: Engine,
    events: Sequence[RawEvent],
    *,
    repository: RecorderRepository | None = None,
) -> dict[str, object]:
    """Local partitioned-PG only; original batch order and SQL are preserved.

    This uses the identical dedupe INSERT ... RETURNING + raw INSERT
    statements as normal insert_events(), and classifies returned dedupe
    keys entirely in memory. Counts are exposed ONLY after COMMIT.
    No event, dedupe key, row ID, query result or payload escapes the report.
    """
    _local_test_engine_only(engine)
    if engine.url.get_backend_name() != "postgresql":
        raise ValueError("B1 same-batch receipt needs local partitioned PostgreSQL")
    if not isinstance(events, (tuple, list)):
        raise ValueError("B1 offline events must be a bounded tuple or list")
    if not 1 <= len(events) <= _MAX_EVENTS:
        raise ValueError("B1 offline batch must have 1-64 events")
    if any(not isinstance(event, RawEvent) for event in events):
        raise ValueError("B1 offline batch contains a non-RawEvent")

    attempted = dict.fromkeys((*_CATEGORIES, _NON_BULK), 0)
    category_for_key: dict[str, str] = {}
    for event in events:
        category = _raw_category(event) or _NON_BULK
        attempted[category] += 1
        previous = category_for_key.setdefault(event.dedupe_key, category)
        if previous != category:
            raise ValueError("B1 cross-category dedupe key collision")

    repo = repository or RecorderRepository()
    with engine.begin() as connection:
        inserted_total, claimed_keys = repo.insert_partitioned_events_with_claim_keys(
            connection, events
        )
        if inserted_total != len(claimed_keys):
            raise RuntimeError("B1 claim count and inserted rows disagree")
        if not claimed_keys.issubset(category_for_key):
            raise RuntimeError("B1 unexpected dedupe key returned by storage")
        inserted = dict.fromkeys((*_CATEGORIES, _NON_BULK), 0)
        for dedupe_key in claimed_keys:
            inserted[category_for_key[dedupe_key]] += 1

    # PostgreSQL transaction context exited successfully. DO NOT call this
    # from the normal recorder or mistake test counts for a host attestation.
    return {
        "report": "stage_b1_local_same_batch_postcommit_claims_v1",
        "status": "LOCAL_TEST_TRANSACTION_COMMITTED_NO_GO",
        "attempted_by_class": attempted,
        "inserted_by_class": inserted,
        "deduplicated_by_class": {
            category: attempted[category] - inserted[category]
            for category in attempted
        },
        "storage_insert_method": "existing_partitioned_SQL_claims_same_batch_order",
        "real_local_db_commit_observed": True,
        "additional_db_statements_vs_normal_insert": 0,
        "production_insert_attribution_proven": False,
        "production_execution_authorized": False,
        "queries_or_mutations_on_production": False,
        "limitations": [
            "Only local PostgreSQL partitioned test writes are measured.",
            "No live recorder, external socket, VM, or DB thread is accessed.",
            "Normal recorder does not activate this code; host overhead unmeasured.",
        ],
    }
