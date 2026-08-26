CREATE TABLE IF NOT EXISTS calibration_edge_runs (
    id BIGSERIAL PRIMARY KEY,
    run_id VARCHAR(128) NOT NULL,
    calibration_version VARCHAR(64) NOT NULL,
    edge_policy_version VARCHAR(64) NOT NULL,
    source_backtest_run_id VARCHAR(128) NOT NULL,
    source_backtest_semantic_sha256 VARCHAR(64) NOT NULL,
    source_training_run_id VARCHAR(128) NOT NULL,
    source_training_semantic_sha256 VARCHAR(64) NOT NULL,
    dataset_version VARCHAR(64) NOT NULL,
    feature_version VARCHAR(64) NOT NULL,
    label_version VARCHAR(64) NOT NULL,
    horizon_seconds INTEGER NOT NULL,
    requested_start TIMESTAMPTZ NOT NULL,
    requested_end TIMESTAMPTZ NOT NULL,
    dataset_sha256 VARCHAR(64) NOT NULL,
    config JSONB NOT NULL,
    config_sha256 VARCHAR(64) NOT NULL,
    source_plan_sha256 VARCHAR(64) NOT NULL,
    source_fold_membership_sha256 JSONB NOT NULL,
    report JSONB NOT NULL,
    semantic_sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_calibration_edge_runs_positive_horizon CHECK (horizon_seconds > 0),
    CONSTRAINT ck_calibration_edge_runs_window_order CHECK (requested_end > requested_start),
    CONSTRAINT uq_calibration_edge_runs_run_id UNIQUE (run_id)
);

CREATE INDEX IF NOT EXISTS ix_calibration_edge_runs_horizon_created
    ON calibration_edge_runs (horizon_seconds, created_at);
