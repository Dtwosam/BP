CREATE TABLE IF NOT EXISTS live_readiness_checks (
    id BIGSERIAL PRIMARY KEY,
    check_id VARCHAR(128) NOT NULL UNIQUE,
    candidate_git_sha VARCHAR(64) NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    eligible BOOLEAN NOT NULL,
    reasons JSONB NOT NULL,
    evidence JSONB NOT NULL,
    semantic_sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_live_readiness_candidate_sha CHECK (char_length(candidate_git_sha) = 64),
    CONSTRAINT ck_live_readiness_semantic_sha CHECK (char_length(semantic_sha256) = 64)
);

CREATE INDEX IF NOT EXISTS ix_live_readiness_observed_at
    ON live_readiness_checks (observed_at);
CREATE INDEX IF NOT EXISTS ix_live_readiness_eligible
    ON live_readiness_checks (eligible);

CREATE TABLE IF NOT EXISTS live_risk_decisions (
    id BIGSERIAL PRIMARY KEY,
    decision_id VARCHAR(128) NOT NULL UNIQUE,
    prediction_id VARCHAR(128) NOT NULL,
    prediction_semantic_sha256 VARCHAR(64) NOT NULL,
    policy_version VARCHAR(64) NOT NULL,
    policy_sha256 VARCHAR(64) NOT NULL,
    eligible BOOLEAN NOT NULL,
    reasons JSONB NOT NULL,
    rules JSONB NOT NULL,
    account_snapshot JSONB NOT NULL,
    evidence JSONB NOT NULL,
    semantic_sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_live_risk_prediction_sha CHECK (char_length(prediction_semantic_sha256) = 64),
    CONSTRAINT ck_live_risk_policy_sha CHECK (char_length(policy_sha256) = 64),
    CONSTRAINT ck_live_risk_semantic_sha CHECK (char_length(semantic_sha256) = 64)
);

CREATE INDEX IF NOT EXISTS ix_live_risk_prediction_id
    ON live_risk_decisions (prediction_id);
CREATE INDEX IF NOT EXISTS ix_live_risk_created_at
    ON live_risk_decisions (created_at);

CREATE TABLE IF NOT EXISTS live_order_intents (
    id BIGSERIAL PRIMARY KEY,
    intent_id VARCHAR(128) NOT NULL UNIQUE,
    prediction_id VARCHAR(128) NOT NULL,
    policy_version VARCHAR(64) NOT NULL,
    request_id VARCHAR(128) NOT NULL,
    risk_decision_id VARCHAR(128) NOT NULL,
    token_id TEXT NOT NULL,
    side VARCHAR(8) NOT NULL,
    size NUMERIC(38, 18) NOT NULL,
    limit_price NUMERIC(38, 18) NOT NULL,
    pre_submit_at TIMESTAMPTZ NOT NULL,
    evidence JSONB NOT NULL,
    semantic_sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_live_intent_buy_only CHECK (side = 'BUY'),
    CONSTRAINT ck_live_intent_positive_size CHECK (size > 0),
    CONSTRAINT ck_live_intent_limit_price CHECK (limit_price > 0 AND limit_price <= 1),
    CONSTRAINT ck_live_intent_semantic_sha CHECK (char_length(semantic_sha256) = 64),
    UNIQUE (prediction_id, policy_version)
);

CREATE INDEX IF NOT EXISTS ix_live_intent_prediction_id
    ON live_order_intents (prediction_id);
CREATE INDEX IF NOT EXISTS ix_live_intent_pre_submit_at
    ON live_order_intents (pre_submit_at);

CREATE TABLE IF NOT EXISTS live_order_events (
    id BIGSERIAL PRIMARY KEY,
    event_key VARCHAR(192) NOT NULL,
    intent_id VARCHAR(128) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    external_order_id VARCHAR(256),
    external_trade_id VARCHAR(256),
    observed_at TIMESTAMPTZ NOT NULL,
    evidence JSONB NOT NULL,
    semantic_sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_live_event_semantic_sha CHECK (char_length(semantic_sha256) = 64),
    UNIQUE (event_key)
);

CREATE INDEX IF NOT EXISTS ix_live_event_intent_time
    ON live_order_events (intent_id, observed_at);
CREATE INDEX IF NOT EXISTS ix_live_event_external_order
    ON live_order_events (external_order_id);

CREATE TABLE IF NOT EXISTS live_reconciliation_runs (
    id BIGSERIAL PRIMARY KEY,
    reconciliation_id VARCHAR(128) NOT NULL UNIQUE,
    observed_at TIMESTAMPTZ NOT NULL,
    unresolved_count INTEGER NOT NULL,
    critical_count INTEGER NOT NULL,
    evidence JSONB NOT NULL,
    semantic_sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_live_reconciliation_unresolved_nonnegative CHECK (unresolved_count >= 0),
    CONSTRAINT ck_live_reconciliation_critical_count CHECK (
        critical_count >= 0 AND critical_count <= unresolved_count
    ),
    CONSTRAINT ck_live_reconciliation_semantic_sha CHECK (char_length(semantic_sha256) = 64)
);

CREATE INDEX IF NOT EXISTS ix_live_reconciliation_observed_at
    ON live_reconciliation_runs (observed_at);
CREATE INDEX IF NOT EXISTS ix_live_reconciliation_critical
    ON live_reconciliation_runs (critical_count);
