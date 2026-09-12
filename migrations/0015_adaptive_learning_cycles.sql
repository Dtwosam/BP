CREATE TABLE IF NOT EXISTS adaptive_learning_cycles (
    id BIGSERIAL PRIMARY KEY,
    cycle_id VARCHAR(128) NOT NULL,
    cycle_version VARCHAR(64) NOT NULL,
    horizon_seconds INTEGER NOT NULL,
    feature_version VARCHAR(128) NOT NULL,
    label_version VARCHAR(128) NOT NULL,
    trigger_count INTEGER NOT NULL,
    since_at TIMESTAMPTZ NOT NULL,
    cutoff_at TIMESTAMPTZ NOT NULL,
    eligible_resolved_market_count INTEGER NOT NULL,
    readiness_semantic_sha256 VARCHAR(64) NOT NULL,
    training_start_at TIMESTAMPTZ NOT NULL,
    training_run_id VARCHAR(128) NOT NULL,
    training_semantic_sha256 VARCHAR(64) NOT NULL,
    summary JSONB NOT NULL,
    semantic_sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_adaptive_learning_cycles_positive_horizon CHECK (horizon_seconds > 0),
    CONSTRAINT ck_adaptive_learning_cycles_positive_trigger CHECK (trigger_count > 0),
    CONSTRAINT ck_adaptive_learning_cycles_trigger_satisfied CHECK (
        eligible_resolved_market_count >= trigger_count
    ),
    CONSTRAINT ck_adaptive_learning_cycles_readiness_sha256 CHECK (
        length(readiness_semantic_sha256) = 64
    ),
    CONSTRAINT ck_adaptive_learning_cycles_training_sha256 CHECK (
        length(training_semantic_sha256) = 64
    ),
    CONSTRAINT ck_adaptive_learning_cycles_semantic_sha256 CHECK (
        length(semantic_sha256) = 64
    ),
    UNIQUE (cycle_id)
);

CREATE INDEX IF NOT EXISTS ix_adaptive_learning_cycles_stream_cutoff
    ON adaptive_learning_cycles (
        horizon_seconds,
        feature_version,
        label_version,
        cutoff_at
    );
