from __future__ import annotations

from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB

from bp_engine.storage import schema
from bp_engine.storage.live_readiness_schema import (
    live_order_events,
    live_order_intents,
    live_readiness_checks,
    live_reconciliation_runs,
    live_risk_decisions,
)


def test_live_readiness_schema_keeps_shared_metadata_sqlite_portable() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    schema.metadata.create_all(engine)

    table_names = set(inspect(engine).get_table_names())
    assert {
        "live_readiness_checks",
        "live_risk_decisions",
        "live_order_intents",
        "live_order_events",
        "live_reconciliation_runs",
    } <= table_names


def test_live_readiness_json_evidence_resolves_to_jsonb_on_postgresql() -> None:
    dialect = postgresql.dialect()
    for table in (
        live_readiness_checks,
        live_risk_decisions,
        live_order_intents,
        live_order_events,
        live_reconciliation_runs,
    ):
        assert isinstance(table.c.evidence.type.dialect_impl(dialect), JSONB)
