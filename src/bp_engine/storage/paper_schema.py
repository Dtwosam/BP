from __future__ import annotations

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from bp_engine.storage.schema import metadata

paper_orders = Table(
    "paper_orders",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("paper_order_id", String(128), nullable=False),
    Column("prediction_id", String(64), nullable=False),
    Column("prediction_semantic_sha256", String(64), nullable=False),
    Column("execution_version", String(64), nullable=False),
    Column("execution_config_sha256", String(64), nullable=False),
    Column("condition_id", Text, nullable=False),
    Column("token_id", Text, nullable=False),
    Column("selected_side", String(8), nullable=False),
    Column("requested_shares", Numeric(38, 18), nullable=False),
    Column("target_notional_usd", Numeric(38, 18), nullable=False),
    Column("submitted_at", DateTime(timezone=True), nullable=False),
    Column("arrival_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("limit_price", Numeric(38, 18), nullable=False),
    Column("signal_selected_ask", Numeric(38, 18), nullable=False),
    Column("signal_fee_rate", Numeric(38, 18), nullable=False),
    Column("signal_slippage_buffer", Numeric(38, 18), nullable=False),
    Column("execution_config", JSON, nullable=False),
    Column("semantic_sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("requested_shares > 0", name="ck_paper_orders_positive_shares"),
    CheckConstraint(
        "target_notional_usd > 0",
        name="ck_paper_orders_positive_target_notional",
    ),
    CheckConstraint(
        "submitted_at <= arrival_at AND arrival_at < expires_at",
        name="ck_paper_orders_timestamp_order",
    ),
    CheckConstraint(
        "limit_price > 0 AND limit_price <= 1",
        name="ck_paper_orders_limit_price",
    ),
    UniqueConstraint("paper_order_id", name="uq_paper_orders_order_id"),
    UniqueConstraint(
        "prediction_id",
        "execution_version",
        name="uq_paper_orders_prediction_execution",
    ),
)

Index("ix_paper_orders_prediction_id", paper_orders.c.prediction_id)
Index("ix_paper_orders_submitted_at", paper_orders.c.submitted_at)

paper_fills = Table(
    "paper_fills",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("paper_order_id", String(128), nullable=False),
    Column("fill_key", String(256), nullable=False),
    Column("fill_at", DateTime(timezone=True), nullable=False),
    Column("shares", Numeric(38, 18), nullable=False),
    Column("price", Numeric(38, 18), nullable=False),
    Column("gross_cost", Numeric(38, 18), nullable=False),
    Column("fee", Numeric(38, 18), nullable=False),
    Column("total_cost", Numeric(38, 18), nullable=False),
    Column("signal_ask_slippage", Numeric(38, 18), nullable=False),
    Column("book_anchor_event_id", Integer, nullable=False),
    Column("book_anchor_dedupe_key", String(80), nullable=False),
    Column("book_applied_event_ids", JSON, nullable=False),
    Column("book_applied_dedupe_keys", JSON, nullable=False),
    Column("replay_cutoff_at", DateTime(timezone=True), nullable=False),
    Column("semantic_sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("shares > 0", name="ck_paper_fills_positive_shares"),
    CheckConstraint("price > 0 AND price <= 1", name="ck_paper_fills_price"),
    CheckConstraint("gross_cost >= 0", name="ck_paper_fills_gross_cost"),
    CheckConstraint("fee >= 0", name="ck_paper_fills_fee"),
    CheckConstraint("total_cost >= 0", name="ck_paper_fills_total_cost"),
    UniqueConstraint(
        "paper_order_id",
        "fill_key",
        name="uq_paper_fills_order_fill_key",
    ),
)

Index("ix_paper_fills_order_time", paper_fills.c.paper_order_id, paper_fills.c.fill_at)

paper_order_terminal_events = Table(
    "paper_order_terminal_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("paper_order_id", String(128), nullable=False),
    Column("terminal_status", String(32), nullable=False),
    Column("remaining_shares", Numeric(38, 18), nullable=False),
    Column("event_at", DateTime(timezone=True), nullable=False),
    Column("reason", String(128), nullable=False),
    Column("semantic_sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "remaining_shares >= 0",
        name="ck_paper_terminal_nonnegative_remaining",
    ),
    UniqueConstraint(
        "paper_order_id",
        name="uq_paper_terminal_order_id",
    ),
)

Index("ix_paper_terminal_event_at", paper_order_terminal_events.c.event_at)

paper_settlements = Table(
    "paper_settlements",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("paper_order_id", String(128), nullable=False),
    Column("label_version", String(64), nullable=False),
    Column("official_outcome", String(8), nullable=False),
    Column("official_target", Integer, nullable=False),
    Column("label_source", String(64), nullable=False),
    Column("label_source_snapshot_sha256", String(64), nullable=False),
    Column("label_source_observed_at", DateTime(timezone=True), nullable=False),
    Column("filled_shares", Numeric(38, 18), nullable=False),
    Column("total_fill_cost", Numeric(38, 18), nullable=False),
    Column("total_fees", Numeric(38, 18), nullable=False),
    Column("payout", Numeric(38, 18), nullable=False),
    Column("realized_pnl", Numeric(38, 18), nullable=False),
    Column("settled_at", DateTime(timezone=True), nullable=False),
    Column("semantic_sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "official_target IN (0, 1)",
        name="ck_paper_settlements_official_target",
    ),
    CheckConstraint("filled_shares > 0", name="ck_paper_settlements_positive_shares"),
    CheckConstraint("total_fill_cost >= 0", name="ck_paper_settlements_fill_cost"),
    CheckConstraint("total_fees >= 0", name="ck_paper_settlements_fees"),
    CheckConstraint("payout >= 0", name="ck_paper_settlements_payout"),
    UniqueConstraint(
        "paper_order_id",
        "label_version",
        name="uq_paper_settlements_order_label",
    ),
)

Index("ix_paper_settlements_settled_at", paper_settlements.c.settled_at)
