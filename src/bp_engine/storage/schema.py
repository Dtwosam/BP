from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
)

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

Index(
    "ix_raw_market_events_received_at_brin",
    raw_market_events.c.received_at,
    postgresql_using="brin",
)
Index(
    "ix_raw_market_events_received_at_id",
    raw_market_events.c.received_at,
    raw_market_events.c.id,
)

market_state_1s = Table(
    "market_state_1s",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("bucket_at", DateTime(timezone=True), nullable=False),
    Column("state_key", String(512), nullable=False),
    Column("source", String(32), nullable=False),
    Column("stream", String(64), nullable=False),
    Column("instrument", String(128), nullable=False),
    Column("market_id", Text, nullable=True),
    Column("asset_id", Text, nullable=True),
    Column("last_event_at", DateTime(timezone=True), nullable=False),
    Column("state", JSON, nullable=False),
    UniqueConstraint(
        "bucket_at",
        "state_key",
        name="uq_market_state_1s_bucket_state_key",
    ),
)

Index("ix_market_state_1s_bucket_at", market_state_1s.c.bucket_at)
Index(
    "ix_market_state_1s_feed_bucket",
    market_state_1s.c.source,
    market_state_1s.c.stream,
    market_state_1s.c.bucket_at,
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

historical_backfill_runs = Table(
    "historical_backfill_runs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(36), nullable=False, unique=True),
    Column("dataset", String(64), nullable=False),
    Column("source", String(64), nullable=False),
    Column("requested_start", DateTime(timezone=True), nullable=False),
    Column("requested_end", DateTime(timezone=True), nullable=False),
    Column("parameters", JSON, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("status", String(24), nullable=False),
    Column("rows_inserted", Integer, nullable=False, default=0),
    Column("rows_existing", Integer, nullable=False, default=0),
    Column("chunks_fetched", Integer, nullable=False, default=0),
    Column("error", Text, nullable=True),
)

historical_backfill_artifacts = Table(
    "historical_backfill_artifacts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(36), nullable=False),
    Column("artifact_key", String(80), nullable=False),
    Column("source", String(64), nullable=False),
    Column("dataset", String(64), nullable=False),
    Column("request_params", JSON, nullable=False),
    Column("downloaded_at", DateTime(timezone=True), nullable=False),
    Column("response_sha256", String(80), nullable=False),
    Column("row_count", Integer, nullable=False),
    UniqueConstraint(
        "run_id",
        "artifact_key",
        "response_sha256",
        name="uq_historical_backfill_artifacts_run_key_sha",
    ),
)

Index(
    "ix_historical_backfill_artifacts_source_dataset",
    historical_backfill_artifacts.c.source,
    historical_backfill_artifacts.c.dataset,
)

polymarket_market_snapshots = Table(
    "polymarket_market_snapshots",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("condition_id", Text, nullable=False),
    Column("gamma_market_id", String(128), nullable=False),
    Column("slug", String(256), nullable=False),
    Column("downloaded_at", DateTime(timezone=True), nullable=False),
    Column("payload_sha256", String(80), nullable=False),
    Column("payload", JSON, nullable=False),
    UniqueConstraint(
        "condition_id",
        "payload_sha256",
        name="uq_polymarket_market_snapshots_condition_sha",
    ),
)

Index(
    "ix_polymarket_market_snapshots_downloaded_at",
    polymarket_market_snapshots.c.downloaded_at,
)

polymarket_price_history = Table(
    "polymarket_price_history",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source", String(64), nullable=False),
    Column("condition_id", Text, nullable=False),
    Column("asset_id", Text, nullable=False),
    Column("outcome", String(8), nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("price", Numeric(24, 12), nullable=False),
    Column("fidelity_minutes", Integer, nullable=False),
    UniqueConstraint(
        "asset_id",
        "observed_at",
        "fidelity_minutes",
        name="uq_polymarket_price_history_asset_time_fidelity",
    ),
)

Index(
    "ix_polymarket_price_history_condition_time",
    polymarket_price_history.c.condition_id,
    polymarket_price_history.c.observed_at,
)
Index("ix_polymarket_price_history_observed_at", polymarket_price_history.c.observed_at)

btc_candles = Table(
    "btc_candles",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source", String(32), nullable=False),
    Column("market_type", String(32), nullable=False),
    Column("symbol", String(32), nullable=False),
    Column("interval_seconds", Integer, nullable=False),
    Column("bucket_at", DateTime(timezone=True), nullable=False),
    Column("open", Numeric(24, 12), nullable=False),
    Column("high", Numeric(24, 12), nullable=False),
    Column("low", Numeric(24, 12), nullable=False),
    Column("close", Numeric(24, 12), nullable=False),
    Column("volume", Numeric(38, 18), nullable=False),
    Column("turnover", Numeric(38, 18), nullable=True),
    Column("raw_payload", JSON, nullable=False),
    UniqueConstraint(
        "source",
        "market_type",
        "symbol",
        "interval_seconds",
        "bucket_at",
        name="uq_btc_candles_source_market_symbol_interval_bucket",
    ),
)

Index(
    "ix_btc_candles_series_bucket",
    btc_candles.c.source,
    btc_candles.c.market_type,
    btc_candles.c.symbol,
    btc_candles.c.interval_seconds,
    btc_candles.c.bucket_at,
)

market_labels = Table(
    "market_labels",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("condition_id", Text, nullable=False),
    Column("gamma_market_id", String(128), nullable=False),
    Column("slug", String(256), nullable=False),
    Column("horizon_seconds", Integer, nullable=False),
    Column("market_start_at", DateTime(timezone=True), nullable=False),
    Column("market_end_at", DateTime(timezone=True), nullable=False),
    Column("official_outcome", String(8), nullable=False),
    Column("start_reference", Numeric(24, 12), nullable=True),
    Column("end_reference", Numeric(24, 12), nullable=True),
    Column("resolution_source", Text, nullable=False),
    Column("rules_hash", String(80), nullable=False),
    Column("label_source", String(64), nullable=False),
    Column("label_version", String(64), nullable=False),
    Column("source_snapshot_sha256", String(80), nullable=False),
    Column("source_observed_at", DateTime(timezone=True), nullable=False),
    Column("generated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "condition_id",
        "label_version",
        name="uq_market_labels_condition_version",
    ),
)

Index("ix_market_labels_market_start_at", market_labels.c.market_start_at)
