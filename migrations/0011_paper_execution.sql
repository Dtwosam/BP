CREATE TABLE IF NOT EXISTS paper_orders (
    id BIGSERIAL PRIMARY KEY,
    paper_order_id VARCHAR(128) NOT NULL,
    prediction_id VARCHAR(64) NOT NULL,
    prediction_semantic_sha256 VARCHAR(64) NOT NULL,
    execution_version VARCHAR(64) NOT NULL,
    execution_config_sha256 VARCHAR(64) NOT NULL,
    condition_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    selected_side VARCHAR(8) NOT NULL,
    requested_shares NUMERIC(38, 18) NOT NULL,
    target_notional_usd NUMERIC(38, 18) NOT NULL,
    submitted_at TIMESTAMPTZ NOT NULL,
    arrival_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    limit_price NUMERIC(38, 18) NOT NULL,
    signal_selected_ask NUMERIC(38, 18) NOT NULL,
    signal_fee_rate NUMERIC(38, 18) NOT NULL,
    signal_slippage_buffer NUMERIC(38, 18) NOT NULL,
    execution_config JSONB NOT NULL,
    semantic_sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_paper_orders_positive_shares CHECK (requested_shares > 0),
    CONSTRAINT ck_paper_orders_positive_target_notional CHECK (target_notional_usd > 0),
    CONSTRAINT ck_paper_orders_timestamp_order CHECK (
        submitted_at <= arrival_at AND arrival_at < expires_at
    ),
    CONSTRAINT ck_paper_orders_limit_price CHECK (limit_price > 0 AND limit_price <= 1),
    CONSTRAINT uq_paper_orders_order_id UNIQUE (paper_order_id),
    CONSTRAINT uq_paper_orders_prediction_execution UNIQUE (
        prediction_id,
        execution_version
    )
);

CREATE INDEX IF NOT EXISTS ix_paper_orders_prediction_id
    ON paper_orders (prediction_id);
CREATE INDEX IF NOT EXISTS ix_paper_orders_submitted_at
    ON paper_orders (submitted_at);

CREATE TABLE IF NOT EXISTS paper_fills (
    id BIGSERIAL PRIMARY KEY,
    paper_order_id VARCHAR(128) NOT NULL,
    fill_key VARCHAR(256) NOT NULL,
    fill_at TIMESTAMPTZ NOT NULL,
    shares NUMERIC(38, 18) NOT NULL,
    price NUMERIC(38, 18) NOT NULL,
    gross_cost NUMERIC(38, 18) NOT NULL,
    fee NUMERIC(38, 18) NOT NULL,
    total_cost NUMERIC(38, 18) NOT NULL,
    signal_ask_slippage NUMERIC(38, 18) NOT NULL,
    book_anchor_event_id BIGINT NOT NULL,
    book_anchor_dedupe_key VARCHAR(80) NOT NULL,
    book_applied_event_ids JSONB NOT NULL,
    book_applied_dedupe_keys JSONB NOT NULL,
    replay_cutoff_at TIMESTAMPTZ NOT NULL,
    semantic_sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_paper_fills_positive_shares CHECK (shares > 0),
    CONSTRAINT ck_paper_fills_price CHECK (price > 0 AND price <= 1),
    CONSTRAINT ck_paper_fills_gross_cost CHECK (gross_cost >= 0),
    CONSTRAINT ck_paper_fills_fee CHECK (fee >= 0),
    CONSTRAINT ck_paper_fills_total_cost CHECK (total_cost >= 0),
    CONSTRAINT uq_paper_fills_order_fill_key UNIQUE (paper_order_id, fill_key)
);

CREATE INDEX IF NOT EXISTS ix_paper_fills_order_time
    ON paper_fills (paper_order_id, fill_at);

CREATE TABLE IF NOT EXISTS paper_order_terminal_events (
    id BIGSERIAL PRIMARY KEY,
    paper_order_id VARCHAR(128) NOT NULL,
    terminal_status VARCHAR(32) NOT NULL,
    remaining_shares NUMERIC(38, 18) NOT NULL,
    event_at TIMESTAMPTZ NOT NULL,
    reason VARCHAR(128) NOT NULL,
    semantic_sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_paper_terminal_nonnegative_remaining CHECK (remaining_shares >= 0),
    CONSTRAINT uq_paper_terminal_order_id UNIQUE (paper_order_id)
);

CREATE INDEX IF NOT EXISTS ix_paper_terminal_event_at
    ON paper_order_terminal_events (event_at);

CREATE TABLE IF NOT EXISTS paper_settlements (
    id BIGSERIAL PRIMARY KEY,
    paper_order_id VARCHAR(128) NOT NULL,
    label_version VARCHAR(64) NOT NULL,
    official_outcome VARCHAR(8) NOT NULL,
    official_target INTEGER NOT NULL,
    label_source VARCHAR(64) NOT NULL,
    label_source_snapshot_sha256 VARCHAR(64) NOT NULL,
    label_source_observed_at TIMESTAMPTZ NOT NULL,
    filled_shares NUMERIC(38, 18) NOT NULL,
    total_fill_cost NUMERIC(38, 18) NOT NULL,
    total_fees NUMERIC(38, 18) NOT NULL,
    payout NUMERIC(38, 18) NOT NULL,
    realized_pnl NUMERIC(38, 18) NOT NULL,
    settled_at TIMESTAMPTZ NOT NULL,
    semantic_sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_paper_settlements_official_target CHECK (official_target IN (0, 1)),
    CONSTRAINT ck_paper_settlements_positive_shares CHECK (filled_shares > 0),
    CONSTRAINT ck_paper_settlements_fill_cost CHECK (total_fill_cost >= 0),
    CONSTRAINT ck_paper_settlements_fees CHECK (total_fees >= 0),
    CONSTRAINT ck_paper_settlements_payout CHECK (payout >= 0),
    CONSTRAINT uq_paper_settlements_order_label UNIQUE (paper_order_id, label_version)
);

CREATE INDEX IF NOT EXISTS ix_paper_settlements_settled_at
    ON paper_settlements (settled_at);
