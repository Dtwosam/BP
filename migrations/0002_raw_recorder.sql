CREATE TABLE IF NOT EXISTS raw_market_events (
    id BIGSERIAL PRIMARY KEY,
    source VARCHAR(32) NOT NULL,
    stream VARCHAR(64) NOT NULL,
    instrument VARCHAR(128) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    source_timestamp TIMESTAMPTZ NULL,
    received_at TIMESTAMPTZ NOT NULL,
    sequence VARCHAR(128) NULL,
    market_id TEXT NULL,
    asset_id TEXT NULL,
    payload JSONB NOT NULL,
    dedupe_key VARCHAR(80) NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS ix_raw_market_events_source_received
    ON raw_market_events (source, stream, received_at);
CREATE INDEX IF NOT EXISTS ix_raw_market_events_market_received
    ON raw_market_events (market_id, received_at);

CREATE TABLE IF NOT EXISTS feed_incidents (
    id BIGSERIAL PRIMARY KEY,
    source VARCHAR(32) NOT NULL,
    stream VARCHAR(64) NOT NULL,
    incident_type VARCHAR(64) NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    details JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_feed_incidents_source_observed
    ON feed_incidents (source, stream, observed_at);

CREATE TABLE IF NOT EXISTS feed_status (
    id BIGSERIAL PRIMARY KEY,
    source VARCHAR(32) NOT NULL,
    stream VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    last_received_at TIMESTAMPTZ NULL,
    last_source_timestamp TIMESTAMPTZ NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    details JSONB NOT NULL,
    CONSTRAINT uq_feed_status_source_stream UNIQUE (source, stream)
);
