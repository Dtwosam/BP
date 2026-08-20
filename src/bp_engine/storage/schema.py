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
