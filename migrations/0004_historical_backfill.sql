CREATE TABLE IF NOT EXISTS historical_backfill_runs (
    id BIGSERIAL PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL UNIQUE,
    dataset VARCHAR(64) NOT NULL,
    source VARCHAR(64) NOT NULL,
    requested_start TIMESTAMPTZ NOT NULL,
    requested_end TIMESTAMPTZ NOT NULL,
    parameters JSON NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    status VARCHAR(24) NOT NULL,
    rows_inserted INTEGER NOT NULL DEFAULT 0,
    rows_existing INTEGER NOT NULL DEFAULT 0,
    chunks_fetched INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS historical_backfill_artifacts (
    id BIGSERIAL PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL,
    artifact_key VARCHAR(80) NOT NULL,
    source VARCHAR(64) NOT NULL,
    dataset VARCHAR(64) NOT NULL,
    request_params JSON NOT NULL,
    downloaded_at TIMESTAMPTZ NOT NULL,
    response_sha256 VARCHAR(80) NOT NULL,
    row_count INTEGER NOT NULL,
    CONSTRAINT uq_historical_backfill_artifacts_run_key
        UNIQUE (run_id, artifact_key)
);

CREATE INDEX IF NOT EXISTS ix_historical_backfill_artifacts_source_dataset
    ON historical_backfill_artifacts (source, dataset);

CREATE TABLE IF NOT EXISTS polymarket_market_snapshots (
    id BIGSERIAL PRIMARY KEY,
    condition_id TEXT NOT NULL,
    gamma_market_id VARCHAR(128) NOT NULL,
    slug VARCHAR(256) NOT NULL,
    downloaded_at TIMESTAMPTZ NOT NULL,
    payload_sha256 VARCHAR(80) NOT NULL,
    payload JSON NOT NULL,
    CONSTRAINT uq_polymarket_market_snapshots_condition_sha
        UNIQUE (condition_id, payload_sha256)
);

CREATE INDEX IF NOT EXISTS ix_polymarket_market_snapshots_downloaded_at
    ON polymarket_market_snapshots (downloaded_at);

CREATE TABLE IF NOT EXISTS polymarket_price_history (
    id BIGSERIAL PRIMARY KEY,
    source VARCHAR(64) NOT NULL,
    condition_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    outcome VARCHAR(8) NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    price NUMERIC(24, 12) NOT NULL,
    fidelity_minutes INTEGER NOT NULL,
    CONSTRAINT uq_polymarket_price_history_asset_time_fidelity
        UNIQUE (asset_id, observed_at, fidelity_minutes)
);

CREATE INDEX IF NOT EXISTS ix_polymarket_price_history_condition_time
    ON polymarket_price_history (condition_id, observed_at);

CREATE INDEX IF NOT EXISTS ix_polymarket_price_history_observed_at
    ON polymarket_price_history (observed_at);

CREATE TABLE IF NOT EXISTS btc_candles (
    id BIGSERIAL PRIMARY KEY,
    source VARCHAR(32) NOT NULL,
    market_type VARCHAR(32) NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    interval_seconds INTEGER NOT NULL,
    bucket_at TIMESTAMPTZ NOT NULL,
    open NUMERIC(24, 12) NOT NULL,
    high NUMERIC(24, 12) NOT NULL,
    low NUMERIC(24, 12) NOT NULL,
    close NUMERIC(24, 12) NOT NULL,
    volume NUMERIC(38, 18) NOT NULL,
    turnover NUMERIC(38, 18),
    raw_payload JSON NOT NULL,
    CONSTRAINT uq_btc_candles_source_market_symbol_interval_bucket
        UNIQUE (source, market_type, symbol, interval_seconds, bucket_at)
);

CREATE INDEX IF NOT EXISTS ix_btc_candles_series_bucket
    ON btc_candles (source, market_type, symbol, interval_seconds, bucket_at);
