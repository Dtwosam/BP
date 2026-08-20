CREATE TABLE IF NOT EXISTS polymarket_markets (
    id BIGSERIAL PRIMARY KEY,
    gamma_market_id TEXT NOT NULL UNIQUE,
    event_id TEXT,
    condition_id TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    question TEXT NOT NULL,
    horizon_seconds INTEGER NOT NULL CHECK (horizon_seconds > 0),
    start_at TIMESTAMPTZ NOT NULL,
    end_at TIMESTAMPTZ NOT NULL,
    up_token_id TEXT NOT NULL,
    down_token_id TEXT NOT NULL,
    resolution_source TEXT NOT NULL,
    rules_text TEXT NOT NULL,
    rules_hash TEXT NOT NULL,
    active BOOLEAN NOT NULL,
    closed BOOLEAN NOT NULL,
    accepting_orders BOOLEAN NOT NULL,
    resolved_outcome TEXT CHECK (resolved_outcome IN ('Up', 'Down') OR resolved_outcome IS NULL),
    discovered_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT polymarket_markets_window_order CHECK (end_at > start_at)
);

CREATE INDEX IF NOT EXISTS polymarket_markets_window_idx
    ON polymarket_markets (start_at, horizon_seconds);

CREATE INDEX IF NOT EXISTS polymarket_markets_status_idx
    ON polymarket_markets (active, closed, end_at);
