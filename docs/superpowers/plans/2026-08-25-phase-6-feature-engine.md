# Phase 6 Feature Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build deterministic, immutable, leakage-safe `core-v1` feature snapshots for short-duration BTC Polymarket markets from only source observations available at each feature timestamp.

**Architecture:** Persist one JSON feature snapshot per `(condition_id, feature_at, feature_version)`. Source readers enforce as-of availability boundaries before calculators see data; calculators return values plus observation descriptors; the service hashes exact selected inputs, validates source cutoffs, and stores rows immutably.

**Tech Stack:** Python 3.12, SQLAlchemy Core, PostgreSQL 16, pytest, Ruff, standard-library `decimal`, `math`, `hashlib`, and `json`.

**Spec:** `docs/superpowers/specs/2026-08-25-phase-6-feature-engine-design.md`

## Global Constraints

- Initial feature version is exactly `core-v1`.
- Feature computation must never read official outcome, label references, or post-resolution label metadata.
- Feature times are strictly inside the market window: `market_start_at < feature_at < market_end_at`.
- Coinbase one-minute candles are usable only when `bucket_at + interval_seconds <= feature_at`.
- Compact state is usable only when both `bucket_at <= feature_at` and `last_event_at <= feature_at`; V1 freshness threshold is 10 seconds.
- Raw trade-flow window is 60 seconds and must be NULL/flagged when coverage overlaps known Phase 3 exclusions or feed coverage is unproven.
- Official reference distance remains NULL with `official_reference_missing=true` in `core-v1`.
- Historical Polymarket L2 is never synthesized.
- Same natural key plus changed semantics raises `FeatureConflict`; identical rerun is existing/no-op.
- Phase 7 model training and every trading phase remain out of scope.

---

### Task 1: Add immutable feature storage and hashing contract

**Files:**
- Create: `migrations/0006_market_features.sql`
- Modify: `src/bp_engine/storage/schema.py`
- Create: `src/bp_engine/features/__init__.py`
- Create: `src/bp_engine/features/models.py`
- Create: `src/bp_engine/features/repository.py`
- Create: `src/bp_engine/features/hashing.py`
- Create: `tests/features/test_feature_schema.py`
- Create: `tests/features/test_feature_repository.py`
- Create: `tests/features/test_feature_hashing.py`

**Interfaces:**
- Produces `FEATURE_VERSION = "core-v1"`.
- Produces immutable `FeatureTarget`, `MarketFeature`, `FeatureStoreResult` dataclasses.
- Produces `FeatureConflict` and `MarketFeatureRepository.store(connection, feature)`.
- Produces `canonical_hash(value) -> str` for JSON-compatible structures and timezone-aware datetimes/Decimals.

- [ ] **Step 1: Write failing schema tests**

Assert `market_features` metadata and migration contain the natural key, JSON payloads, hashes, timestamps, and window/check constraints.

- [ ] **Step 2: Run RED**

Run: `pytest tests/features/test_feature_schema.py -v`  
Expected: FAIL because migration/table do not exist.

- [ ] **Step 3: Implement additive migration and SQLAlchemy metadata**

Create `market_features` with columns from the design and constraints:

```sql
UNIQUE (condition_id, feature_at, feature_version)
CHECK (horizon_seconds > 0)
CHECK (market_end_at > market_start_at)
CHECK (feature_at > market_start_at)
CHECK (feature_at < market_end_at)
```

- [ ] **Step 4: Write repository/hash RED tests**

Cover first insert, exact rerun preserving original `generated_at`, changed `features`/missing flags/cutoffs/hash raising `FeatureConflict`, canonical mapping-order independence, and rejection of naive datetimes/non-finite floats.

- [ ] **Step 5: Implement models/repository/hashing minimally**

Semantic equality includes every persisted field except `generated_at`. `canonical_hash` canonicalizes datetimes to UTC ISO-8601, Decimals to canonical strings, mappings by sorted key, and sequences in order before SHA-256.

- [ ] **Step 6: Run GREEN**

Run: `pytest tests/features/test_feature_schema.py tests/features/test_feature_repository.py tests/features/test_feature_hashing.py -v`  
Expected: PASS.

- [ ] **Step 7: Commit**

Commit message: `feat: add immutable feature snapshot storage`

---

### Task 2: Build strict as-of source readers

**Files:**
- Create: `src/bp_engine/features/sources.py`
- Create: `tests/features/test_feature_sources.py`

**Interfaces:**
- Produces `FeatureLeakageError`.
- Produces observation dataclasses `PriceObservation`, `CandleObservation`, and `StateObservation`.
- Produces `FeatureSourceReader.latest_polymarket_price(...)`, `closed_candles(...)`, and `latest_state(...)`.

- [ ] **Step 1: Write token-price boundary RED tests**

Fixture rows at `T-1s`, `T`, and `T+1s`; assert the reader chooses `T` and never `T+1s`.

- [ ] **Step 2: Write candle-close RED tests**

For a 60-second candle at `10:00`, assert it is unavailable at `10:00:59.999999` and available at `10:01:00`.

- [ ] **Step 3: Write compact-state RED tests**

Assert a row with `bucket_at <= T` but `last_event_at > T` raises `FeatureLeakageError`; assert a valid row older than 10 seconds is returned with `fresh=False` rather than treated as current.

- [ ] **Step 4: Run RED**

Run: `pytest tests/features/test_feature_sources.py -v`  
Expected: FAIL because source reader does not exist.

- [ ] **Step 5: Implement readers**

Queries must order deterministically by source timestamp then id. Every returned observation exposes an `effective_at` used later in source cutoffs and fingerprints.

- [ ] **Step 6: Run GREEN**

Run: `pytest tests/features/test_feature_sources.py -v`  
Expected: PASS.

- [ ] **Step 7: Commit**

Commit message: `feat: enforce feature as-of source boundaries`

---

### Task 3: Implement deterministic core calculators

**Files:**
- Create: `src/bp_engine/features/calculators.py`
- Create: `tests/features/test_feature_calculators.py`

**Interfaces:**
- Produces `FeatureGroup(values, missing_flags, source_cutoffs, observations)`.
- Produces pure functions for time geometry, Polymarket prices/books, BTC candles, Bybit state, and book imbalance.

- [ ] **Step 1: Write time/price RED tests**

For a 5-minute target at +120 seconds assert:

```python
seconds_elapsed == 120
seconds_remaining == 180
fraction_elapsed == 0.4
```

For Up `0.62` and Down `0.37`, assert `pm_price_sum == 0.99` and `pm_up_minus_down == 0.25`.

- [ ] **Step 2: Write book RED tests**

Assert mid/spread and imbalance formula. Zero total depth must yield NULL imbalance, not divide by zero.

- [ ] **Step 3: Write BTC RED tests**

Use fully closed candle fixtures to assert 1m/5m/15m returns, 5/15 return volatility, and `coinbase_return_from_prestart_close`. Assert insufficient history creates NULLs + explicit missing flags rather than fabricated zeroes.

- [ ] **Step 4: Write reference-distance RED test**

Assert `official_reference_distance is None` and `official_reference_missing is True` for every V1 result.

- [ ] **Step 5: Run RED**

Run: `pytest tests/features/test_feature_calculators.py -v`  
Expected: FAIL because calculators do not exist.

- [ ] **Step 6: Implement pure calculators**

Use finite `Decimal` inputs for price arithmetic and standard-library `math.log`/population standard deviation for realized return volatility. Reject non-finite results.

- [ ] **Step 7: Run GREEN**

Run: `pytest tests/features/test_feature_calculators.py -v`  
Expected: PASS.

- [ ] **Step 8: Commit**

Commit message: `feat: calculate core market and BTC features`

---

### Task 4: Add observed raw trade flow and exclusion rules

**Files:**
- Create: `src/bp_engine/features/exclusions.py`
- Create: `src/bp_engine/features/trade_flow.py`
- Create: `tests/features/test_trade_flow.py`
- Create: `tests/features/test_feature_exclusions.py`

**Interfaces:**
- Produces immutable `RawExclusion(start, end, reason)` and `raw_window_exclusion(start, end)`.
- Produces `TradeFlow(buy_volume, sell_volume, signed_volume, trade_count)`.
- Produces `parse_trade_flow(events, source, stream)` using native source side fields only.

- [ ] **Step 1: Write exclusion RED tests**

Encode exactly:

```text
2026-08-22T20:00:00Z <= t < 2026-08-22T21:00:00Z
2026-08-23T21:00:00Z <= t < 2026-08-24T10:18:16.692122Z
```

Assert any overlap causes raw flow to be excluded.

- [ ] **Step 2: Write parser RED tests**

Fixtures:
- Polymarket `last_trade_price` with `side`/`size`;
- Coinbase `market_trades_*` trades with `side`/`size`;
- Bybit `trade` list entries with `S`/`v`.

Assert buy/sell/signed volumes and counts. Malformed or side-less trade records that would affect totals must fail closed rather than infer side.

- [ ] **Step 3: Write coverage RED test**

A trailing window with no feed events is `missing`, while a window with feed events but zero trade events is valid zero flow.

- [ ] **Step 4: Run RED**

Run: `pytest tests/features/test_trade_flow.py tests/features/test_feature_exclusions.py -v`  
Expected: FAIL because modules do not exist.

- [ ] **Step 5: Implement exclusions and flow**

Only rows with `feature_at - 60s < received_at <= feature_at` may contribute. Observation descriptors use raw `dedupe_key` and selected trade fields.

- [ ] **Step 6: Run GREEN**

Run: `pytest tests/features/test_trade_flow.py tests/features/test_feature_exclusions.py -v`  
Expected: PASS.

- [ ] **Step 7: Commit**

Commit message: `feat: add explicit observed trade flow features`

---

### Task 5: Assemble feature service, planner, fingerprints, and future-data proof

**Files:**
- Create: `src/bp_engine/features/service.py`
- Create: `tests/features/test_feature_service.py`
- Create: `tests/features/test_future_data_leakage.py`

**Interfaces:**
- Produces `plan_feature_times(target, step_seconds=60) -> tuple[datetime, ...]`.
- Produces `build_feature(connection, target, feature_at, generated_at, repository=None) -> FeatureStoreResult`.
- Produces `generate_features(connection, targets, generated_at, step_seconds=60) -> FeatureGenerationStats`.

- [ ] **Step 1: Write planner RED tests**

A 5m market yields offsets `[60, 120, 180, 240]`; 15m yields 14 one-minute offsets; exact market end is excluded.

- [ ] **Step 2: Write service RED test**

With deterministic fixtures, assert merged feature/missing dictionaries, all source cutoffs `<= feature_at`, stable `input_fingerprint`, stable `feature_hash`, and immutable persistence.

- [ ] **Step 3: Write perturbation future-data RED test**

Generate at `T`, insert token-price/candle/compact/raw observations strictly after `T`, regenerate at `T`, and assert byte-for-byte equivalent semantic feature record and identical hashes.

- [ ] **Step 4: Write label-isolation RED test**

Create two databases/fixtures identical except for different `market_labels.official_outcome`; generate the same feature target and assert identical features/hashes. The service must not select `official_outcome`.

- [ ] **Step 5: Run RED**

Run: `pytest tests/features/test_feature_service.py tests/features/test_future_data_leakage.py -v`  
Expected: FAIL because service does not exist.

- [ ] **Step 6: Implement service**

Merge feature groups in fixed key order, validate every cutoff, canonical-hash sorted observation descriptors, compute feature hash from `{features, missing_flags}`, and store through `MarketFeatureRepository`.

- [ ] **Step 7: Run GREEN**

Run: `pytest tests/features/test_feature_service.py tests/features/test_future_data_leakage.py -v`  
Expected: PASS.

- [ ] **Step 8: Commit**

Commit message: `feat: assemble leakage-safe feature generation`

---

### Task 6: Add offline batch CLI and PostgreSQL acceptance

**Files:**
- Create: `src/bp_engine/features/cli.py`
- Create: `scripts/generate_features.py`
- Create: `tests/features/test_feature_cli.py`
- Create: `tests/features/test_postgres_features.py`

**Interfaces:**
- Command:

```text
python scripts/generate_features.py --start <ISO8601> --end <ISO8601> --env-file <path> [--step-seconds 60]
```

- Batch target query selects only static columns from `market_labels`: condition id, slug, horizon, market start/end.
- Output JSON contains target count, planned rows, inserted rows, existing rows, and rows with each missing group.

- [ ] **Step 1: Write CLI RED tests**

Reject naive datetimes, non-increasing target windows, `step_seconds <= 0`, and prove source has no HTTP/WebSocket client imports.

- [ ] **Step 2: Write PostgreSQL RED acceptance test**

Apply migrations through `0006`, seed realistic label/static target plus price/candle/state/raw fixtures, generate twice, and assert second insert count zero, unique keys, cutoff invariants, NULL official reference distance, and no outcome/label keys in feature payloads.

- [ ] **Step 3: Run RED**

Run: `pytest tests/features/test_feature_cli.py tests/features/test_postgres_features.py -v`  
Expected: FAIL because CLI/integration wiring does not exist.

- [ ] **Step 4: Implement CLI and static target loader**

Use existing `Settings`, SQLAlchemy transaction conventions, `datetime.now(UTC)` for generation timestamp, and deterministic sorted JSON output.

- [ ] **Step 5: Run GREEN**

Run: `pytest tests/features/test_feature_cli.py tests/features/test_postgres_features.py -v`  
Expected: PASS.

- [ ] **Step 6: Run full verification**

Run: `ruff check . && pytest`  
Expected: all checks pass.

- [ ] **Step 7: Commit**

Commit message: `feat: add offline feature generation CLI`

---

### Task 7: Add Phase 6 production acceptance and closeout path

**Files:**
- Create: `scripts/deploy/phase6_host_acceptance.sh`
- Create: `scripts/deploy/phase6_cloudshell_accept.sh`
- Modify: `.github/workflows/ci.yml`
- Create: `docs/PHASE-6-DEPLOYMENT.md`
- Test: `tests/features/test_phase6_deployment_assets.py`

**Interfaces:**
- Host gate accepts `EXPECTED_HEAD`, applies migration 0006, runs batch generation twice for the Phase 5 acceptance market-start window, and writes `/var/lib/bp/evidence/phase6-feature-engine/<stamp>/final-summary.txt`.

- [ ] **Step 1: Write deployment-asset RED tests**

Assert Cloud Shell helper performs `/opt/bp` operations only inside the SSH payload; assert host script includes exact-head, trading-disabled, recorder-before/after, idempotence, cutoff, no-label-key, reference-missing, duplicate-key, and disk-health gates.

- [ ] **Step 2: Run RED**

Run: `pytest tests/features/test_phase6_deployment_assets.py -v`  
Expected: FAIL because deployment assets do not exist.

- [ ] **Step 3: Implement host and Cloud Shell helpers**

Host final summary must include at least:

```text
VERDICT=PASS
HEAD=<sha>
TARGET_MARKETS=<n>
FEATURE_ROWS=<n>
SECOND_RUN_INSERTED=0
INVALID_FUTURE_CUTOFFS=0
DUPLICATE_KEYS=0
LABEL_KEY_VIOLATIONS=0
OFFICIAL_REFERENCE_VIOLATIONS=0
RECORDER_BEFORE=active
RECORDER_AFTER=active
LIVE_TRADING_ENABLED=false
```

- [ ] **Step 4: Update CI and runbook**

CI runs `bash -n` on both Phase 6 helpers. Runbook documents one-line Cloud Shell invocation, evidence path, and PASS fields.

- [ ] **Step 5: Run GREEN/full gates**

Run full CI plus Historical Backfill Smoke, Live Recorder Smoke, and Recorder Short Soak on the frozen candidate.

- [ ] **Step 6: Production host acceptance**

Run the verified Cloud Shell one-liner against `bp-recorder`. Do not close Phase 6 unless host output is PASS.

- [ ] **Step 7: Closeout after PASS**

Create `docs/evidence/phase-6-closeout-20260825.json`, update `PROJECT_STATE.json` to Phase 7-ready, update changelog and decision log, rerun exact-head gates, mark PR ready, merge with expected-head guard, and verify `main`.
