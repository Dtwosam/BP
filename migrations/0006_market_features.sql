CREATE TABLE IF NOT EXISTS market_features (
    id BIGSERIAL PRIMARY KEY,
    condition_id TEXT NOT NULL,
    slug VARCHAR(256) NOT NULL,
    horizon_seconds INTEGER NOT NULL,
    market_start_at TIMESTAMPTZ NOT NULL,
    market_end_at TIMESTAMPTZ NOT NULL,
    feature_at TIMESTAMPTZ NOT NULL,
    feature_offset_seconds INTEGER NOT NULL,
    feature_version VARCHAR(64) NOT NULL,
    features JSONB NOT NULL,
    missing_flags JSONB NOT NULL,
    source_cutoffs JSONB NOT NULL,
    input_fingerprint VARCHAR(64) NOT NULL,
    feature_hash VARCHAR(64) NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_market_features_positive_horizon
        CHECK (horizon_seconds > 0),
    CONSTRAINT ck_market_features_window_order
        CHECK (market_end_at > market_start_at),
    CONSTRAINT ck_market_features_after_start
        CHECK (feature_at > market_start_at),
    CONSTRAINT ck_market_features_before_end
        CHECK (feature_at < market_end_at),
    CONSTRAINT uq_market_features_condition_time_version
        UNIQUE (condition_id, feature_at, feature_version)
);

CREATE INDEX IF NOT EXISTS ix_market_features_feature_at
    ON market_features (feature_at);

CREATE INDEX IF NOT EXISTS ix_market_features_condition_feature_at
    ON market_features (condition_id, feature_at);
