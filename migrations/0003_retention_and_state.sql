CREATE TABLE IF NOT EXISTS market_state_1s (
    id BIGSERIAL PRIMARY KEY,
    bucket_at TIMESTAMPTZ NOT NULL,
    state_key VARCHAR(512) NOT NULL,
    source VARCHAR(32) NOT NULL,
    stream VARCHAR(64) NOT NULL,
    instrument VARCHAR(128) NOT NULL,
    market_id TEXT,
    asset_id TEXT,
    last_event_at TIMESTAMPTZ NOT NULL,
    state JSON NOT NULL,
    CONSTRAINT uq_market_state_1s_bucket_state_key UNIQUE (bucket_at, state_key)
);

CREATE INDEX IF NOT EXISTS ix_market_state_1s_bucket_at
    ON market_state_1s (bucket_at);

CREATE INDEX IF NOT EXISTS ix_market_state_1s_feed_bucket
    ON market_state_1s (source, stream, bucket_at);

CREATE INDEX IF NOT EXISTS ix_raw_market_events_received_at_brin
    ON raw_market_events USING BRIN (received_at);
