CREATE TABLE IF NOT EXISTS market_labels (
    id BIGSERIAL PRIMARY KEY,
    condition_id TEXT NOT NULL,
    gamma_market_id VARCHAR(128) NOT NULL,
    slug VARCHAR(256) NOT NULL,
    horizon_seconds INTEGER NOT NULL,
    market_start_at TIMESTAMPTZ NOT NULL,
    market_end_at TIMESTAMPTZ NOT NULL,
    official_outcome VARCHAR(8) NOT NULL,
    start_reference NUMERIC(24, 12),
    end_reference NUMERIC(24, 12),
    resolution_source TEXT NOT NULL,
    rules_hash VARCHAR(80) NOT NULL,
    label_source VARCHAR(64) NOT NULL,
    label_version VARCHAR(64) NOT NULL,
    source_snapshot_sha256 VARCHAR(80) NOT NULL,
    source_observed_at TIMESTAMPTZ NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_market_labels_official_outcome
        CHECK (official_outcome IN ('Up', 'Down')),
    CONSTRAINT ck_market_labels_positive_horizon
        CHECK (horizon_seconds > 0),
    CONSTRAINT ck_market_labels_window_order
        CHECK (market_end_at > market_start_at),
    CONSTRAINT ck_market_labels_source_after_end
        CHECK (source_observed_at >= market_end_at),
    CONSTRAINT uq_market_labels_condition_version
        UNIQUE (condition_id, label_version)
);

CREATE INDEX IF NOT EXISTS ix_market_labels_market_start_at
    ON market_labels (market_start_at);
