CREATE TABLE IF NOT EXISTS improvement_experiments (
    id BIGSERIAL PRIMARY KEY,
    experiment_id VARCHAR(128) NOT NULL,
    experiment_version VARCHAR(64) NOT NULL,
    horizon_seconds INTEGER NOT NULL,
    change_family VARCHAR(32) NOT NULL,
    champion_calibration_run_id VARCHAR(128) NOT NULL,
    champion_calibration_semantic_sha256 VARCHAR(64) NOT NULL,
    hypothesis TEXT NOT NULL,
    spec JSONB NOT NULL,
    semantic_sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_improvement_experiments_positive_horizon CHECK (horizon_seconds > 0),
    CONSTRAINT ck_improvement_experiments_semantic_sha256 CHECK (
        length(semantic_sha256) = 64
    ),
    CONSTRAINT ck_improvement_experiments_champion_sha256 CHECK (
        length(champion_calibration_semantic_sha256) = 64
    ),
    CONSTRAINT uq_improvement_experiments_experiment_id UNIQUE (experiment_id)
);

CREATE INDEX IF NOT EXISTS ix_improvement_experiments_horizon_created
    ON improvement_experiments (horizon_seconds, created_at);

CREATE TABLE IF NOT EXISTS improvement_evaluations (
    id BIGSERIAL PRIMARY KEY,
    evaluation_id VARCHAR(128) NOT NULL,
    evaluation_version VARCHAR(64) NOT NULL,
    experiment_id VARCHAR(128) NOT NULL,
    challenger_id VARCHAR(128) NOT NULL,
    challenger_semantic_sha256 VARCHAR(64) NOT NULL,
    evidence_manifest JSONB NOT NULL,
    champion_metrics JSONB NOT NULL,
    challenger_metrics JSONB NOT NULL,
    comparison JSONB NOT NULL,
    promotion_eligible BOOLEAN NOT NULL,
    ineligibility_reasons JSONB NOT NULL,
    report JSONB NOT NULL,
    semantic_sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_improvement_evaluations_semantic_sha256 CHECK (
        length(semantic_sha256) = 64
    ),
    CONSTRAINT ck_improvement_evaluations_challenger_sha256 CHECK (
        length(challenger_semantic_sha256) = 64
    ),
    CONSTRAINT uq_improvement_evaluations_evaluation_id UNIQUE (evaluation_id)
);

CREATE INDEX IF NOT EXISTS ix_improvement_evaluations_experiment_created
    ON improvement_evaluations (experiment_id, created_at);
CREATE INDEX IF NOT EXISTS ix_improvement_evaluations_promotion_created
    ON improvement_evaluations (promotion_eligible, created_at);

CREATE TABLE IF NOT EXISTS improvement_promotion_decisions (
    id BIGSERIAL PRIMARY KEY,
    decision_id VARCHAR(128) NOT NULL,
    decision_version VARCHAR(64) NOT NULL,
    evaluation_id VARCHAR(128) NOT NULL,
    experiment_id VARCHAR(128) NOT NULL,
    decision VARCHAR(32) NOT NULL,
    rationale TEXT NOT NULL,
    resulting_champion JSONB NOT NULL,
    decision_record JSONB NOT NULL,
    semantic_sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_improvement_decisions_semantic_sha256 CHECK (
        length(semantic_sha256) = 64
    ),
    CONSTRAINT ck_improvement_decisions_decision CHECK (
        decision IN ('reject_challenger', 'keep_champion', 'promote_challenger')
    ),
    CONSTRAINT uq_improvement_decisions_decision_id UNIQUE (decision_id)
);

CREATE INDEX IF NOT EXISTS ix_improvement_decisions_evaluation_created
    ON improvement_promotion_decisions (evaluation_id, created_at);
CREATE INDEX IF NOT EXISTS ix_improvement_decisions_experiment_created
    ON improvement_promotion_decisions (experiment_id, created_at);
