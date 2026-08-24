# Phase 4 Historical Backfill Design

**Date:** 24 August 2026  
**Phase:** 4 — historical backfill  
**Branch:** `build/phase-4-historical-backfill`  
**Live trading:** Disabled

## Goal

Build a reproducible, idempotent historical ingestion layer that can populate the research database with Polymarket BTC Up/Down market metadata and token-price history plus BTC spot/perpetual candle history, while preserving exact source parameters, download timestamps, raw response fingerprints, and known coverage limitations.

Phase 4 does not create labels, features, models, backtests, paper execution, or live trading.

## Current external-source contract

The implementation is based on official public API documentation checked on 24 August 2026:

- Polymarket Gamma `GET /markets/keyset` supports stable cursor pagination, closed-market filtering, and start/end date filters.
- Polymarket CLOB `GET /prices-history` returns historical token-price points and accepts token asset id, start/end Unix timestamps, interval, and fidelity. The batch endpoint accepts up to 20 token asset ids.
- Bybit V5 `GET /v5/market/kline` provides historical spot and linear-contract candles, with start/end milliseconds and up to 1,000 rows per request.
- Coinbase Advanced Trade exposes a public product-candles endpoint; Coinbase Exchange also documents public historic product candles and explicitly warns that historical candle coverage can be incomplete when there are no ticks.

These APIs are external and may change. Request/response assumptions must be isolated behind clients and regression fixtures.

## Selected architecture

### 1. Historical market catalog

Use Gamma keyset pagination over the requested time range and `closed=true`. Normalize only markets whose slug matches the existing BTC Up/Down slug contract (`btc-updown-<horizon>-<epoch>`) and whose horizon is configured for the run.

Each accepted payload is:

1. parsed through the existing strict `parse_gamma_market()` path;
2. upserted into the existing `polymarket_markets` table, preserving the existing rules-hash guard;
3. preserved as a raw historical market snapshot with a canonical SHA-256 fingerprint.

The raw snapshot keeps nested Gamma event metadata even though Phase 4 does not yet normalize events into a separate label table.

### 2. Polymarket token-price history

For each normalized market, fetch both Up and Down token histories for the exact market window, using one-minute fidelity by default. Price points are stored by natural key:

`(asset_id, observed_at, fidelity_minutes)`.

A rerun with the same value is a no-op. A rerun that produces a different value for an existing natural key raises `HistoricalDataConflict` instead of silently rewriting history.

The source endpoint is a historical price series, not an order-book reconstruction. Phase 4 does not fabricate bid/ask depth for intervals where verified historical book detail is unavailable.

### 3. BTC historical candles

Backfill three initial series at one-minute granularity:

- Bybit spot `BTCUSDT`;
- Bybit linear/perpetual `BTCUSDT`;
- Coinbase spot `BTC-USD` through the public candle endpoint.

Candles use the natural key:

`(source, market_type, symbol, interval_seconds, bucket_at)`.

All numeric values are stored as exact decimals. Bybit turnover is retained when supplied. The raw source row/object is also retained for forensic reproducibility.

As with token prices, identical reruns are no-ops and conflicting values fail closed.

### 4. Run and artifact provenance

Every CLI invocation creates a `historical_backfill_runs` row containing:

- generated run id;
- dataset/action name;
- source or source set;
- requested start/end;
- exact JSON parameters;
- start/completion timestamps;
- status;
- rows inserted / rows already present / chunks fetched;
- error text when failed.

Every external response chunk creates a `historical_backfill_artifacts` record containing:

- deterministic request fingerprint (`artifact_key`);
- source and dataset;
- exact request parameters;
- download timestamp;
- canonical response SHA-256;
- response row count;
- run id.

Repeated retrieval of the same request and checksum is allowed and remains auditable. Different checksums for the same request are retained as separate artifacts rather than silently overwriting provenance.

### 5. CLI

Add `scripts/historical_backfill.py` with four operator-facing commands:

- `polymarket-markets` — discover and persist historical BTC Up/Down markets;
- `polymarket-prices` — populate Up/Down token price history for already persisted markets in the requested range;
- `btc-candles` — populate one configured BTC candle series;
- `standard` — run the Phase 4 baseline sequence: markets, Polymarket prices, Bybit spot, Bybit linear, Coinbase spot.

All commands require explicit UTC `--start` and `--end` values. `start < end` is enforced. The standard command defaults to active horizons from settings and one-minute source data.

### 6. Pagination and chunking

- Gamma: cursor-based pages, maximum 100 markets per request.
- Polymarket price history: market-window requests, both outcome tokens; use public CLOB history only.
- Bybit: split into windows no larger than 1,000 requested candles; parse reverse-sorted responses into ascending storage order.
- Coinbase: split into windows no larger than the documented public candle limit; tolerate genuinely missing empty buckets but never synthesize candles.

Chunk boundaries are deterministic from the requested range and interval so reruns produce identical request parameters.

### 7. Storage schema

Add migration `0004_historical_backfill.sql` and matching SQLAlchemy tables:

- `historical_backfill_runs`;
- `historical_backfill_artifacts`;
- `polymarket_market_snapshots`;
- `polymarket_price_history`;
- `btc_candles`.

Indexes support range scans by observed/bucket timestamp plus source/asset lookup. Natural-key uniqueness prevents duplicate historical observations.

### 8. Known Phase 3 exclusions

Dataset builders must continue to exclude:

- `2026-08-22T20:00:00Z`–`2026-08-22T21:00:00Z` from raw-dependent local training unless independently recovered and revalidated; exactly 250,000 raw events are known missing there;
- rollout-era local raw coverage that cannot be proven from surviving Phase 3 VM artifacts after `2026-08-23T21:00:00Z` and before the later retained/compact coverage, unless independently reacquired.

Phase 4 historical APIs may provide replacement higher-level history for some of those periods, but that does not recreate missing private high-frequency raw order-book events. Provenance must distinguish backfilled candles/prices from private recorder raw data.

## Failure behavior

- Network/API errors fail the current run and record the error; already committed chunks remain valid and rerunnable.
- Unexpected response shapes raise source-specific parse errors.
- Conflicting values at an existing natural key raise `HistoricalDataConflict` and stop the run.
- Rule-hash changes still trigger the existing Polymarket rule-change guard.
- No command deletes recorder raw data, archives, compact state, or existing historical rows.
- No command enables trading or changes risk limits.

## Testing strategy

Use test-first development for:

- deterministic chunk boundaries;
- Gamma keyset pagination and BTC Up/Down filtering;
- Polymarket price parsing;
- Bybit and Coinbase candle parsing;
- natural-key idempotency;
- conflict detection;
- provenance artifact checksums;
- CLI validation;
- a PostgreSQL-backed integration rerun proving the second identical run creates no duplicate historical observations.

Add a live GitHub Actions smoke that fetches a very small recent closed-market/candle sample from public endpoints without credentials and validates only response shape and parser compatibility. It must not write to production and must not trade.

## Acceptance

Phase 4 passes only when:

- a bounded historical date range can be backfilled for Polymarket markets and token prices plus Bybit spot/linear and Coinbase spot candles;
- rerunning the exact same range does not duplicate observations or corrupt existing values;
- source parameters, download timestamps, and response checksums are recorded;
- official-source limitations are documented, especially missing candle buckets and the distinction between price history and true historical order books;
- known Phase 3 local raw exclusions remain explicit;
- unit/integration tests pass;
- live historical-source smoke passes;
- sanitized Phase 4 evidence, project state, changelog, and decision log are updated before moving to Phase 5.
