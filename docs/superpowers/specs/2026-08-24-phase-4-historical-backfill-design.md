# Phase 4 Historical Backfill Design

**Date:** 24–25 August 2026  
**Phase:** 4 — historical backfill  
**Branch:** `build/phase-4-historical-backfill`  
**Live trading:** Disabled

## Goal

Build a reproducible, idempotent historical ingestion layer that can populate the research database with Polymarket BTC Up/Down market metadata and token-price history plus BTC reference candle history, while preserving exact source parameters, download timestamps, raw response fingerprints, and known coverage limitations.

Phase 4 does not create labels, features, models, backtests, paper execution, or live trading.

## External-source contract

The implementation uses:

- Polymarket Gamma exact market-by-slug lookup for deterministic BTC 5m/15m discovery;
- Polymarket CLOB `GET /prices-history` for outcome-token price series;
- Bybit V5 `GET /v5/market/kline` for optional BTC spot/linear candles where the runtime environment is permitted to access Bybit REST;
- Coinbase public product candles as the mandatory historical BTC reference series.

Bybit's official integration guidance states that US IP addresses are restricted and return HTTP 403. Both US-hosted GitHub runners and the production `us-east1` recorder VM exhibit that restriction.

External APIs may change. All response assumptions are isolated behind clients and regression tests; unexpected shapes, rules, or historical-value conflicts fail closed.

## Selected architecture

### 1. Historical Polymarket market catalog

BTC 5m/15m markets already have a deterministic slug contract enforced by the recorder/parser:

```text
btc-updown-<horizon>-<window_start_epoch>
```

For an explicit half-open UTC range `[start, end)`, Phase 4:

1. validates configured horizons such as `5m` and `15m`;
2. aligns each horizon to the first expected window start at or after `start`;
3. enumerates every expected slug before `end`;
4. calls Gamma `GET /markets/slug/{slug}` for each expected market;
5. treats 404 as an explicit missing-market/coverage gap, not as a fabricated row;
6. requires every returned payload to contain the exact requested slug and parsed horizon/window;
7. requires the existing strict `parse_gamma_market()` and rules fingerprint to pass;
8. stores the normalized market plus a canonical raw snapshot and response checksum.

`chunks_fetched` counts expected slug lookups, including 404 gaps. Found payloads receive `historical_backfill_artifacts` provenance with dataset `market_by_slug` and exact slug request parameters.

Transient Gamma 500/503 responses receive at most three bounded attempts with short exponential delays. A 404 is never retried; other 4xx responses fail immediately; exhausted 500/503 responses fail the run.

#### Rejected list-based discovery

List/date-filter discovery was rejected using acceptance evidence:

- the Gamma keyset request for the fixed Phase 4 host-acceptance window repeatedly returned HTTP 500, including after bounded retries and repeated whole-gate attempts;
- the regular dated `/markets` endpoint responded, but a live test showed that it did not include a recent completed BTC market that the exact-slug endpoint had just returned.

Therefore Phase 4 does not infer BTC trading-window coverage from Gamma list-date semantics. The exact slug is the deterministic discovery key for this market family.

### 2. Polymarket token-price history

For each normalized market, fetch both Up and Down token histories for the exact market window, using one-minute fidelity by default. Price points are sorted, validated, and clipped to the exact half-open market interval before storage. They use the natural key:

`(asset_id, observed_at, fidelity_minutes)`.

An identical rerun is a no-op. A changed value at an existing natural key raises `HistoricalDataConflict` rather than silently rewriting research history.

The source endpoint is a historical price series, not an order-book reconstruction. Phase 4 does not fabricate historical bid/ask depth.

### 3. BTC historical candles

Implement three one-minute series:

- Bybit spot `BTCUSDT` when accessible;
- Bybit linear/perpetual `BTCUSDT` when accessible;
- Coinbase spot `BTC-USD` as the mandatory reference series.

Candles use the natural key:

`(source, market_type, symbol, interval_seconds, bucket_at)`.

All numeric values are stored as exact decimals. Bybit turnover is retained when supplied. Raw source rows/objects are retained for forensic reproducibility.

Bybit HTTP 403 is classified as `BybitHistoryUnavailableError`. Standard multi-source runs persist that condition as `status=unavailable` and continue to mandatory core sources. Other Bybit failures remain failures. `standard --require-bybit` restores strict behavior in a permitted environment where Bybit is mandatory. Phase 4 never routes around the provider restriction.

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

Every external response actually used for ingestion creates a `historical_backfill_artifacts` record containing deterministic request fingerprint, source/dataset, exact request parameters, download timestamp, canonical response SHA-256, response row count, and run id. Revised responses for the same request are preserved as separate checksum versions.

### 5. CLI

`scripts/historical_backfill.py` exposes:

- `polymarket-markets` — enumerate and fetch historical BTC Up/Down slugs;
- `polymarket-prices` — populate Up/Down token price history for persisted markets;
- `btc-candles` — populate explicitly requested BTC candle sources;
- `standard` — run markets, Polymarket prices, Bybit spot, Bybit linear, and Coinbase spot in a fixed auditable order.

All commands require explicit timezone-aware `--start` and `--end` values. `start < end` is enforced. The standard command defaults to active horizons from settings and one-minute source data.

Only a classified Bybit HTTP 403 is optional in standard mode. `standard --require-bybit` converts it into a hard failure. Explicit Bybit source commands remain strict.

### 6. Deterministic request boundaries

- Polymarket markets: exact expected slug enumeration from configured horizons and `[start, end)`;
- Polymarket prices: one request per outcome token per market window, with local half-open clipping;
- Bybit: deterministic windows no larger than 1,000 requested candles, reverse responses normalized ascending;
- Coinbase: deterministic windows no larger than 350 buckets; missing/no-tick buckets remain absent.

Reruns therefore reproduce the same natural keys and request boundaries without synthesizing missing data.

### 7. Storage schema

Migration `0004_historical_backfill.sql` and matching SQLAlchemy tables add:

- `historical_backfill_runs`;
- `historical_backfill_artifacts`;
- `polymarket_market_snapshots`;
- `polymarket_price_history`;
- `btc_candles`.

Indexes support range scans by observed/bucket timestamp plus source/asset lookup. Natural-key uniqueness prevents duplicate historical observations.

### 8. Known Phase 3 exclusions

Dataset builders must continue to exclude:

- `2026-08-22T20:00:00Z`–`2026-08-22T21:00:00Z` from raw-dependent local training unless independently recovered and revalidated; exactly 250,000 raw events are known missing there;
- rollout-era local raw coverage that cannot be proven from surviving Phase 3 VM artifacts after `2026-08-23T21:00:00Z` and before later retained/compact coverage, unless independently reacquired.

Phase 4 APIs may provide higher-level prices/candles for some of those periods, but they do not reconstruct missing private high-frequency recorder events. Provenance must keep those datasets distinct.

## Failure behavior

- transient Gamma exact-slug 500/503 errors receive bounded retry, then fail if exhausted;
- missing Gamma slug 404 is recorded implicitly as a requested gap and stores no fabricated data;
- returned Gamma slug/window mismatch fails immediately;
- other network/API errors fail the current run except the specifically classified optional Bybit HTTP 403 state;
- unexpected response shapes raise source-specific parse errors;
- conflicting existing natural-key values raise `HistoricalDataConflict`;
- rules-hash changes trigger the existing Polymarket rule-change guard;
- no command deletes recorder raw data, archives, compact state, or existing historical rows;
- no command enables trading or changes risk limits.

## Testing strategy

Test-first coverage includes:

- deterministic BTC slug enumeration for 5m/15m ranges;
- missing-slug behavior and provider slug/window mismatch rejection;
- Gamma exact-slug transient retry and 404 no-retry behavior;
- Polymarket price parsing and market-window clipping;
- Bybit/Coinbase candle parsing and deterministic chunks;
- explicit Bybit HTTP 403 availability classification;
- standard-mode continuation plus strict `--require-bybit` behavior;
- natural-key idempotency and conflict detection;
- provenance checksums and terminal statuses;
- CLI validation;
- PostgreSQL 16 migration/rerun behavior;
- live public-source smoke using exact Gamma slug lookup, both Polymarket outcome histories, Coinbase candles, and explicit Bybit availability classification.

## Acceptance

Phase 4 passes only when:

- a bounded historical range can be backfilled with non-empty Polymarket markets, both token-price histories, and Coinbase BTC candles;
- deterministic expected-slug lookup is the market-discovery mechanism used by the accepted build;
- Bybit spot/linear history is either fetched successfully or explicitly recorded as the documented HTTP 403 environment restriction;
- rerunning the exact range creates no duplicate observations or silent rewrites;
- source parameters, download timestamps, and response checksums are recorded;
- unavailable provenance is limited to audited Bybit HTTP 403;
- historical L2 is not fabricated or conflated with token prices;
- known Phase 3 local raw exclusions remain explicit;
- unit/integration/public-smoke checks pass;
- production host acceptance passes without altering the running recorder checkout;
- sanitized Phase 4 evidence, project state, changelog, and decision log are updated before Phase 5.
