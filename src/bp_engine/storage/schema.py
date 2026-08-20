from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, Text

metadata = MetaData()

polymarket_markets = Table(
    "polymarket_markets",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("gamma_market_id", String(128), nullable=False, unique=True),
    Column("event_id", String(128), nullable=True),
    Column("condition_id", String(256), nullable=False, unique=True),
    Column("slug", String(256), nullable=False, unique=True),
    Column("question", Text, nullable=False),
    Column("horizon_seconds", Integer, nullable=False),
    Column("start_at", DateTime(timezone=True), nullable=False),
    Column("end_at", DateTime(timezone=True), nullable=False),
    Column("up_token_id", Text, nullable=False),
    Column("down_token_id", Text, nullable=False),
    Column("resolution_source", Text, nullable=False),
    Column("rules_text", Text, nullable=False),
    Column("rules_hash", String(80), nullable=False),
    Column("active", Boolean, nullable=False),
    Column("closed", Boolean, nullable=False),
    Column("accepting_orders", Boolean, nullable=False),
    Column("resolved_outcome", String(8), nullable=True),
    Column("discovered_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

from sqlalchemy import JSON, UniqueConstraint

raw_market_events = Table(
    "raw_market_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source", String(32), nullable=False),
    Column("stream", String(64), nullable=False),
    Column("instrument", String(128), nullable=False),
    Column("event_type", String(64), nullable=False),
    Column("source_timestamp", DateTime(timezone=True), nullable=True),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("sequence", String(128), nullable=True),
    Column("market_id", Text, nullable=True),
    Column("asset_id", Text, nullable=True),
    Column("payload", JSON, nullable=False),
    Column("dedupe_key", String(80), nullable=False),
    UniqueConstraint("dedupe_key", name="uq_raw_market_events_dedupe_key"),
)

feed_incidents = Table(
    "feed_incidents",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source", String(32), nullable=False),
    Column("stream", String(64), nullable=False),
    Column("incident_type", String(64), nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("details", JSON, nullable=False),
)

feed_status = Table(
    "feed_status",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source", String(32), nullable=False),
    Column("stream", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("last_received_at", DateTime(timezone=True), nullable=True),
    Column("last_source_timestamp", DateTime(timezone=True), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("details", JSON, nullable=False),
    UniqueConstraint("source", "stream", name="uq_feed_status_source_stream"),
)
