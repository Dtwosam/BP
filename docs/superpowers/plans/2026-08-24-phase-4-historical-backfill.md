# Phase 4 Historical Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build reproducible, idempotent historical ingestion for Polymarket BTC Up/Down market/token prices and BTC spot/perpetual candles with durable provenance.

**Architecture:** Isolate each external API behind a parser/client, store observations by immutable natural keys, record every external response checksum in provenance tables, and fail closed when a source attempts to change an existing historical observation. Reuse the existing Polymarket parser/rules guard and PostgreSQL stack.

**Tech Stack:** Python 3.12, httpx, SQLAlchemy 2, PostgreSQL 16, pytest, Ruff, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-24-phase-4-historical-backfill-design.md`

## Global Constraints

- `LIVE_TRADING_ENABLED=false`; Phase 4 must not add execution code.
- Active Polymarket horizons remain configurable; initial verified horizons are `5m` and `15m`.
- Historical source rows are never silently overwritten when the same natural key has different values.
- Do not synthesize unavailable order-book history or missing candle buckets.
- Preserve Phase 3 raw-data exclusions as explicit dataset metadata.
- Every network response used for ingestion must have exact request parameters, download time, row count, and SHA-256 recorded.

---

### Task 1: Historical schema and repositories

**Files:**
- Create: `migrations/0004_historical_backfill.sql`
- Modify: `src/bp_engine/storage/schema.py`
- Create: `src/bp_engine/storage/historical.py`
- Test: `tests/storage/test_historical.py`

**Interfaces:**
- Produces `HistoricalDataConflict`, `HistoricalRepository`, and typed row dataclasses used by source services.
- Natural keys: Polymarket `(asset_id, observed_at, fidelity_minutes)` and BTC `(source, market_type, symbol, interval_seconds, bucket_at)`.

- [ ] Write failing schema/repository tests for insert, identical rerun no-op, and conflicting-value failure.
- [ ] Run focused tests and confirm RED.
- [ ] Add migration/tables/repository implementation.
- [ ] Run focused tests and confirm GREEN.
- [ ] Commit.

### Task 2: Provenance hashing and run tracking

**Files:**
- Create: `src/bp_engine/backfill/models.py`
- Create: `src/bp_engine/backfill/provenance.py`
- Test: `tests/backfill/test_provenance.py`

**Interfaces:**
- `canonical_json_sha256(payload: object) -> str`
- `artifact_key(source: str, dataset: str, request_params: Mapping[str, object]) -> str`
- Repository methods create/finalize run and record response artifact.

- [ ] Write deterministic-hash and repeated-artifact tests.
- [ ] Run focused tests and confirm RED.
- [ ] Implement canonical JSON hashing and provenance persistence.
- [ ] Run focused tests and confirm GREEN.
- [ ] Commit.

### Task 3: Polymarket historical market discovery

**Files:**
- Modify: `src/bp_engine/polymarket/gamma.py`
- Create: `src/bp_engine/backfill/polymarket.py`
- Test: `tests/backfill/test_polymarket_history.py`

**Interfaces:**
- `GammaClient.list_markets_page(...) -> GammaMarketPage`
- `backfill_polymarket_markets(connection, client, start, end, horizons, downloaded_at) -> BackfillStats`

- [ ] Write RED tests for keyset pagination, date parameters, strict BTC slug filtering, snapshot SHA persistence, and existing rules guard reuse.
- [ ] Implement page client and market backfill service.
- [ ] Run focused tests GREEN.
- [ ] Commit.

### Task 4: Polymarket price-history ingestion

**Files:**
- Create: `src/bp_engine/backfill/polymarket_prices.py`
- Test: `tests/backfill/test_polymarket_prices.py`

**Interfaces:**
- `PolymarketPriceHistoryClient.get_history(asset_id, start, end, fidelity_minutes) -> PriceHistoryResponse`
- `backfill_polymarket_prices(...) -> BackfillStats`

- [ ] Write RED tests for request parameter names, `t/p` parsing, UTC conversion, both Up/Down assets, idempotency, conflict propagation, and response checksum recording.
- [ ] Implement client/service.
- [ ] Run focused tests GREEN.
- [ ] Commit.

### Task 5: Bybit historical candles

**Files:**
- Create: `src/bp_engine/backfill/bybit.py`
- Test: `tests/backfill/test_bybit.py`

**Interfaces:**
- `BybitHistoryClient.get_klines(category, symbol, interval, start, end, limit=1000)`
- `backfill_bybit_candles(...) -> BackfillStats`

- [ ] Write RED tests for deterministic <=1000-candle windows, reverse-order parsing, exact decimals, and spot/linear natural-key separation.
- [ ] Implement parser/client/service.
- [ ] Run focused tests GREEN.
- [ ] Commit.

### Task 6: Coinbase public historical candles

**Files:**
- Create: `src/bp_engine/backfill/coinbase.py`
- Test: `tests/backfill/test_coinbase.py`

**Interfaces:**
- `CoinbaseHistoryClient.get_candles(product_id, granularity, start, end, limit)`
- `backfill_coinbase_candles(...) -> BackfillStats`

- [ ] Write RED tests for public endpoint path, deterministic bounded windows, object response parsing, empty-bucket tolerance, and exact decimals.
- [ ] Implement parser/client/service.
- [ ] Run focused tests GREEN.
- [ ] Commit.

### Task 7: Operator CLI and standard sequence

**Files:**
- Create: `src/bp_engine/backfill/__init__.py`
- Create: `scripts/historical_backfill.py`
- Test: `tests/backfill/test_cli.py`

**Interfaces:**
- Commands: `polymarket-markets`, `polymarket-prices`, `btc-candles`, `standard`.
- Explicit timezone-aware `--start` / `--end`; reject invalid/empty ranges.

- [ ] Write RED CLI validation and orchestration tests.
- [ ] Implement CLI using the production env/database URL.
- [ ] Run focused tests GREEN.
- [ ] Commit.

### Task 8: PostgreSQL rerun integration

**Files:**
- Create: `tests/backfill/test_postgres_rerun.py`
- Modify: `.github/workflows/ci.yml` only if needed for PostgreSQL service coverage.

- [ ] Write an integration test that ingests fixture prices/candles twice and proves observation counts do not increase on the second run.
- [ ] Prove a changed value at the same natural key raises `HistoricalDataConflict`.
- [ ] Run full test suite and Ruff.
- [ ] Commit.

### Task 9: Live source smoke and operator evidence

**Files:**
- Create: `scripts/historical_backfill_smoke.py`
- Create: `.github/workflows/historical-backfill-smoke.yml`
- Create: `docs/PHASE-4-DEPLOYMENT.md`
- Test: add parser/unit tests as needed for authentic live response shapes.

- [ ] Add a network-only smoke that reads a tiny recent sample from Gamma, Polymarket CLOB price history, Bybit spot/linear, and Coinbase public candles without credentials.
- [ ] Store a sanitized JSON artifact with endpoint/version assumptions and counts, not massive source payloads.
- [ ] Run CI and live smoke to GREEN.
- [ ] Commit.

### Task 10: Phase 4 host acceptance and closeout

**Files:**
- Create: `scripts/deploy/phase4_host_revalidate.sh`
- Create after evidence: `docs/evidence/phase-4-closeout-202608XX.json`
- Modify after evidence: `PROJECT_STATE.json`, `docs/CHANGELOG.md`, `docs/DECISION-LOG.md`

- [ ] Deploy exact green candidate to `bp-recorder` without stopping the recorder.
- [ ] Apply migration `0004_historical_backfill.sql`.
- [ ] Run a bounded standard backfill window twice.
- [ ] Verify identical counts/checksums and zero duplicate observations on rerun.
- [ ] Verify recorder, storage timers, and disk status remain healthy.
- [ ] Record external-source coverage limitations and Phase 3 exclusion carry-forward.
- [ ] Only then mark Phase 4 complete / Phase 5 ready.
