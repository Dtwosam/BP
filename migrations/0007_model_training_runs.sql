CREATE TABLE IF NOT EXISTS model_training_runs (
    id BIGSERIAL PRIMARY KEY,
    run_id VARCHAR(128) NOT NULL,
    dataset_version VARCHAR(64) NOT NULL,
    split_version VARCHAR(64) NOT NULL,
    feature_version VARCHAR(64) NOT NULL,
    label_version VARCHAR(64) NOT NULL,
    horizon_seconds INTEGER NOT NULL,
    requested_start TIMESTAMPTZ NOT NULL,
    requested_end TIMESTAMPTZ NOT NULL,
    dataset_sha256 VARCHAR(64) NOT NULL,
    split_sha256 VARCHAR(64) NOT NULL,
    predictor_names JSONB NOT NULL,
    dropped_all_missing JSONB NOT NULL,
    model_configs JSONB NOT NULL,
    validation_champion VARCHAR(64) NOT NULL,
    report JSONB NOT NULL,
    artifact_manifest JSONB NOT NULL,
    semantic_sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_model_training_runs_positive_horizon CHECK (horizon_seconds > 0),
    CONSTRAINT ck_model_training_runs_window_order CHECK (requested_end > requested_start),
    CONSTRAINT uq_model_training_runs_run_id UNIQUE (run_id)
);

CREATE INDEX IF NOT EXISTS ix_model_training_runs_horizon_created
    ON model_training_runs (horizon_seconds, created_at);
