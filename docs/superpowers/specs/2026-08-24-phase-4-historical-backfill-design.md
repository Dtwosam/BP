# Phase 4 Historical Backfill Design

**Date:** 24 August 2026  
**Phase:** 4 — historical backfill  
**Branch:** `build/phase-4-historical-backfill`  
**Live trading:** Disabled

## Goal

Build a reproducible, idempotent historical ingestion layer that can populate the research database with Polymarket BTC Up/Down market metadata and token-price history plus BTC reference candle history, while preserving exact source parameters, download timestamps, raw response fingerprints, and known coverage limitations.

Phase 4 does not create labels, features, models, backtests, paper execution, or live trading.

## Current external-source contract

The implementation is based on official public API documentation checked on 24–25 August 2026:

- Polymarket Gamma `GET /markets/keyset` supports stable cursor pagination, closed-market filtering, and start/end date filters.
- Polymarket CLOB `GET /prices-history` returns historical token-price points and accepts token asset id, start/end Unix timestamps, interval, and fidelity.
- Bybit V5 `GET /v5/market/kline` provides historical spot and linear-contract candles, with start/end milliseconds and up to 1,000 rows per request. Bybit's official integration guidance also states that US IP addresses are restricted and return HTTP 403.
- Coinbase Advanced Trade exposes a public product-candles endpoint; Coinbase also documents that historical candle coverage can be incomplete when there are no ticks.

These APIs are external and may change. Request/response assumptions must be isolated behind clients and regression fixtures.

## Selected architecture

### 1. Historical market catalog

Use Gamma keyset pagination over the requested time range and `closed=true`. Normalize only markets whose slug matches the existing BTC Up/Down slug contract (`btc-updown-<horizon>-<epoch>`) and whose horizon is configured for the run.

Each accepted payload is:

1. parsed through the existing strict `parse_gamma_market()` path;
2. independently checked to ensure the normalized market start lies inside the requested half-open UTC window;
3. upserted into the existing `polymarket_markets` table, preserving the existing rules-hash guard;
4. preserved as a raw historical market snapshot with a canonical SHA-256 fingerprint.

The raw snapshot keeps nested Gamma event metadata even though Phase 4 does not yet normalize events into a separate label table.

### 2. Polymarket token-price history

For each normalized market, fetch both Up and Down token histories for the exact market window, using one-minute fidelity by default. Price points are sorted, validated, and clipped to the exact half-open market interval before storage. They are stored by natural key:

`(asset_id, observed_at, fidelity_minutes)`.

A rerun with the same value is a no-op. A rerun that produces a different value for an existing natural key raises `HistoricalDataConflict` instead of silently rewriting history.

The source endpoint is a historical price series, not an order-book reconstruction. Phase 4 does not fabricate bid/ask depth for intervals where verified historical book detail is unavailable.

### 3. BTC historical candles

Implement three one-minute series:

- Bybit spot `BTCUSDT` when the runtime environment is permitted to access Bybit REST;
- Bybit linear/perpetual `BTCUSDT` when the runtime environment is permitted to access Bybit REST;
- Coinbase spot `BTC-USD` through the public candle endpoint as the mandatory historical BTC reference series.

Candles use the natural key:

`(source, market_type, symbol, interval_seconds, bucket_at)`.

All numeric values are stored as exact decimals. Bybit turnover is retained when supplied. The raw source row/object is also retained for forensic reproducibility.

Bybit HTTP 403 is a source-availability state, not a reason to route around provider restrictions. `BybitHistoryUnavailableError` classifies that exact condition. Standard multi-source runs persist it as `status=unavailable` and continue to mandatory core sources. Other Bybit failures remain failures. A strict `--require-bybit` mode is available for permitted environments where Bybit history is a hard requirement.

As with token prices, identical reruns are no-ops and conflicting values fail closed.

### 4. Run and artifact provenance

Every dataset attempt creates a `historical_backfill_runs` row containing:

- generated run id;
- dataset/action name;
- source;
- requested start/end;
- exact JSON parameters;
- start/completion timestamps;
- terminal status;
- rows inserted / rows already present / chunks fetched;
- error/reason text when failed or unavailable.

Terminal statuses are:

- `success` — source fetched and committed;
- `unavailable` — limited to the explicitly classified Bybit HTTP 403 environment restriction;
- `failed` — every other failure.

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
- `btc-candles` — populate explicitly requested BTC candle sources;
- `standard` — run markets, Polymarket prices, Bybit spot, Bybit linear, and Coinbase spot in a fixed auditable order.

All commands require explicit timezone-aware `--start` and `--end` values. `start < end` is enforced. The standard command defaults to active horizons from settings and one-minute source data.

The standard command treats only a classified Bybit HTTP 403 as optional/unavailable. `standard --require-bybit` converts that condition back into a hard failure. Explicit Bybit source commands remain strict.

### 6. Pagination and chunking

- Gamma: cursor-based pages, maximum 100 markets per request, with local range revalidation.
- Polymarket price history: market-window requests for both outcome tokens, with local half-open range clipping.
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

- Network/API errors fail the current run except for the specifically classified optional Bybit HTTP 403 state.
- Unexpected response shapes raise source-specific parse errors.
- Conflicting values at an existing natural key raise `HistoricalDataConflict` and stop the run.
- Rule-hash changes still trigger the existing Polymarket rule-change guard.
- No command deletes recorder raw data, archives, compact state, or existing historical rows.
- No command enables trading or changes risk limits.
- No operator workflow routes around a provider's documented geographic/IP restriction.

## Testing strategy

Use test-first development for:

- deterministic chunk boundaries;
- Gamma keyset pagination, BTC Up/Down filtering, and local date-window enforcement;
- Polymarket price parsing and market-window clipping;
- Bybit and Coinbase candle parsing;
- explicit Bybit HTTP 403 availability classification;
- standard-mode continuation plus strict `--require-bybit` behavior;
- natural-key idempotency;
- conflict detection;
- provenance artifact checksums and terminal statuses;
- CLI validation;
- a PostgreSQL-backed integration rerun proving the second identical run creates no duplicate historical observations.

Add a live GitHub Actions smoke that fetches a very small recent closed-market/candle sample from public endpoints without credentials and validates response shape/parser compatibility. The smoke must use `pipefail`, explicitly report a restricted Bybit 403, and must not write to production or trade.

## Acceptance

Phase 4 passes only when:

- a bounded historical date range can be backfilled with non-empty Polymarket markets, both token-price histories, and Coinbase BTC candles;
- Bybit spot/linear history is either fetched successfully or explicitly recorded as the documented HTTP 403 environment restriction;
- rerunning the exact same range does not duplicate observations or corrupt existing values;
- source parameters, download timestamps, and response checksums are recorded;
- unavailable provenance is limited to the audited Bybit HTTP 403 condition;
- official-source limitations are documented, especially missing candle buckets, Bybit deployment restrictions, and the distinction between price history and true historical order books;
- known Phase 3 local raw exclusions remain explicit;
- unit/integration tests pass;
- live historical-source smoke passes under those semantics;
- sanitized Phase 4 evidence, project state, changelog, and decision log are updated before moving to Phase 5.
