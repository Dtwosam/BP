# Changelog

## 0.5.0 — 25 August 2026

Phase 5 — official outcome/label pipeline — closed after production-host acceptance of deterministic, immutable, leakage-safe labels derived only from preserved official Polymarket Gamma resolution evidence.

The host-accepted operational candidate was `3c39b626a37c7ac7c0a9c10caaabd4d0b6cf0325`. Fresh exact-head pre-host gates passed on that commit: CI #483, Historical Backfill Smoke #145, Live Recorder Smoke #249, and Recorder Short Soak #213. Production acceptance used the fixed half-open market-start window `2026-08-24T18:00:00Z <= t < 2026-08-24T19:00:00Z` and returned `VERDICT=PASS`.

The label pipeline stores immutable `official-outcome-v1` rows keyed by `(condition_id, label_version)`. Labels are generated offline from the Phase 4 `polymarket_market_snapshots` store, not from a live HTTP dependency. A snapshot may become label evidence only when the market is closed, the official outcome is unambiguous, and the snapshot was observed at or after market end. For each condition the canonical source is the earliest eligible resolved snapshot ordered by download time and snapshot id. Conflicting official-resolution semantics or attempts to change an existing semantic label fail closed rather than rewriting history.

Production acceptance generated 16 labels on the first pass and then immediately reran the exact same window with zero inserts and 16 existing rows. The gate verified zero leakage violations, zero contract violations, zero missing exact snapshot-provenance joins, zero duplicate natural keys, and matching condition coverage across the two runs. The recorder remained active before and after acceptance.

Official start/end reference prices remain NULL in V1. The preserved Gamma payloads and current Phase 5 evidence do not establish independently verified first-party start/end reference-price fields, so Coinbase, Bybit, CLOB token prices, or inferred prices are not substituted into the authoritative label contract. Downstream feature work may use market/BTC observations as features only under its own timestamp and provenance rules; those observations do not become official label references.

Phase 3 raw-data exclusions and Phase 4 source-availability limitations remain binding downstream. In particular, unavailable/unverified historical Polymarket L2 remains unavailable rather than synthesized, and audited source gaps must remain explicit through feature missing-data flags.

Sanitized closeout evidence is stored in `docs/evidence/phase-5-closeout-20260825.json`. Phase 6 — feature engine — is now the next permitted build-order phase. Model training, backtesting, live prediction, paper trading, and live trading remain blocked by later phase gates; live trading remains disabled.

## 0.4.0 — 25 August 2026

Phase 4 — historical backfill — closed after production-host acceptance of deterministic Polymarket market discovery, official token-price history, Coinbase BTC-USD candles, immutable/idempotent historical storage, provenance/checksums, and environment-aware Bybit handling.

The host-accepted operational candidate was `29fa75b500858ae50f50b863d0c62ff2acb4ec52`. Fresh exact-head gates passed before closeout: CI #421, Historical Backfill Smoke #112, Live Recorder Smoke #220, and Recorder Short Soak #184. Production acceptance used the fixed half-open window `2026-08-24T18:00:00Z <= t < 2026-08-24T19:00:00Z` and returned `VERDICT=PASS`.

Polymarket historical market discovery now deterministically enumerates aligned `btc-updown-<horizon>-<window_start_epoch>` slugs for verified 5m/15m horizons and fetches each exact Gamma market-by-slug payload. This replaces list/date-filter discovery after production acceptance repeatedly exposed HTTP 500 behavior on Gamma keyset queries and live verification showed the regular dated market list did not reliably include a known recent BTC market. A missing exact slug is an explicit coverage gap; a returned slug/window mismatch fails closed; exact-slug HTTP 500/503 responses receive bounded retries.

Historical Up/Down token prices use the official Polymarket CLOB `/prices-history` endpoint and persist only observations inside the market's half-open window while retaining provenance for the complete fetched response. Coinbase public BTC-USD candles are the mandatory core BTC historical series for Phase 4.

Bybit spot and linear BTCUSDT historical kline support is implemented, but the production GCP host and GitHub US-hosted runners receive documented HTTP 403 restrictions from Bybit. Standard backfill therefore records only that narrowly classified condition as audited `unavailable` with zero rows/chunks; explicit Bybit-only commands and `standard --require-bybit` remain strict. The project does not route around provider restrictions.

The production host gate verified 10 new dataset-run records across two standard runs, zero invalid terminal statuses, four audited Bybit-unavailable runs, zero rows inserted by the second run, non-empty/existing core-source coverage, recorder active before and after acceptance, maintenance and disk-health timers enabled, disk status `ok`, and the preserved Phase 3 forensic SHA-256 unchanged. The boot disk was safely expanded to 200 GiB after PostgreSQL growth crossed the Phase 3 warning threshold; no protected research data was deleted.

No verified first-party historical Polymarket L2/order-book endpoint was found for this phase. Historical depth is therefore marked unavailable/unverified and is never synthesized. Phase 3 raw-data exclusions remain binding for downstream raw-dependent research.

Sanitized closeout evidence is stored in `docs/evidence/phase-4-closeout-20260825.json`. Phase 5 — official outcome/label pipeline — is now the next permitted build-order phase. Feature engineering, model training, backtesting, paper trading, and live trading remain blocked by their later phase gates; live trading remains disabled.

## 0.3.0 — 24 August 2026

Phase 3 — retention and aggregation — closed after host validation of bounded raw retention, verified archive-before-delete behavior, compact one-second state, disk protection, and scheduled maintenance.

Final host validation ran on commit `d90919a29f7beca7c7b1b5ba4cfd2964c11747c4`. Fresh branch gates passed on that commit: CI #206, Live Recorder Smoke #114, and Recorder Short Soak #78. The host proof returned `FAIL_COUNT=0` and `PHASE3_RETENTION_SEMANTICS_HOST_REVALIDATION_PASS`, with zero premature archive removals, archive/delete row parity, all managed archives verifying, zero stale archive temp files, four compact feeds present, recorder active with zero warning lines during the proof, disk status `ok`, successful systemd maintenance, and both maintenance/disk-health timers enabled.

A late closeout review found that the accepted archive-retention contract is 24 hours of hot PostgreSQL raw data plus 24 additional hours of verified local archive retention, for roughly 48 hours of full-raw recoverability. The earlier operator wiring counted archive retention from event time and was corrected in `6d329b2707b7897cbd73baa2aae5a87990fe7975`; a regression test first failed on the old wiring and then passed after the fix.

The interrupted-maintenance incident remains explicitly preserved: exactly 250,000 events from `2026-08-22T20:00:00Z` through `2026-08-22T21:00:00Z` are missing from both PostgreSQL and the surviving archive. The preserved forensic archive SHA-256 is `423f22c58ed356a207684b794f401537ba60e009f08aa89fe54fc7f58efbe9ef`. That exact interval is excluded from raw-dependent model training unless independently recovered from a trustworthy source.

A separate rollout-era local coverage limitation is also recorded. After final revalidation, surviving managed archives covered `2026-08-23T18:00:00Z` through `2026-08-23T21:00:00Z`; compact state was first observed for all four feeds in the `2026-08-24T10:00:00Z` hour and the earliest retained raw row was `2026-08-24T10:18:16.692122Z`. Surviving VM artifacts cannot prove whether unavailable earlier local raw coverage was caused by pruning history or capture downtime, so that unavailable interval is excluded from raw-dependent research unless independently reacquired. Compact-state absence before the compact-state rollout is not, by itself, treated as proof of recorder downtime.

Storage evidence measured approximately 39.5 GB/day of projected raw PostgreSQL growth at the observed event rate. Near-steady-state maintenance completed in 1,375 seconds against a 55-minute service timeout, and the first scheduled cycle also completed successfully. PostgreSQL retention scans use the `(received_at, id)` index path and autovacuum/reuse was observed; `VACUUM FULL` was not used.

Sanitized closeout evidence is stored in `docs/evidence/phase-3-closeout-20260824.json`. Phase 4 — historical backfill — is now the next permitted build-order phase. Live trading remains disabled.

## 0.2.3 — 23 August 2026

Phase 2 closed after the genuine 24-hour always-on recorder gate and continuity review.

The accepted frozen window is `2026-08-22T20:12:57.033984Z` through `2026-08-23T20:12:57.033984Z`. Revalidation of that exact window passed with 45,669,676 persisted events across Polymarket, Bybit spot, Bybit linear/perpetual, and Coinbase spot.

Continuity review found:

- zero systemd recorder restarts during the run;
- zero unresolved disconnects;
- zero `clock_skew` incidents;
- zero internal `backpressure` incidents;
- all 35 recorded disconnects followed by prompt reconnects;
- bounded and explicitly logged Bybit stale gaps of 30.734 s (spot) and 51.229 s (linear), followed by resumed ingestion.

The original formal report remains preserved as failed evidence because a stale-recovery incident persistence bug incorrectly left the two Bybit stale states unresolved in the incident table. The defect was reproduced test-first, fixed, and verified. Final reliability/evaluator commit `8c4c35b654b46a8bd8235daa2a03d43496693c2a` passed CI run 79 with Ruff clean and 77 tests, Live Recorder Smoke run 48, and Recorder Short Soak run 16.

The earlier host attempt that filled the original 40 GB disk is also retained as failed evidence and is not counted toward acceptance. That storage failure directly motivates Phase 3 retention/aggregation work.

Sanitized closeout evidence is stored in `docs/evidence/phase-2-host-soak-20260823.json`; the narrative closeout is `docs/PHASE-2-CLOSEOUT.md`.

Phase 3 — retention and aggregation — is now the next permitted build-order phase. Live trading remains disabled.

## 0.2.2 — 21 August 2026

Phase 2 reached the pre-host recorder checkpoint. The phase remains open pending the required genuine 24-hour always-on soak test.

Verified on commit `cf85c9139cfd887188bb10b60d6a75cf98e0e389`:

- CI passed with 74 automated tests and Ruff clean;
- live recorder smoke passed against Polymarket, Bybit spot, Bybit perpetual, and Coinbase spot;
- PostgreSQL-backed short soak passed with 17,506 live events and no health failures;
- Polymarket book snapshot timestamps are preserved in raw payloads without being misclassified as transport clock-skew evidence;
- Coinbase secondary feed uses ticker/top-of-book plus market trades and heartbeats, while Bybit remains the primary deep-book source;
- SQLAlchemy PostgreSQL connections use the installed psycopg v3 driver;
- secure always-on Ubuntu deployment assets were added;
- production PostgreSQL is bound to localhost only;
- the recorder runs as a dedicated unprivileged systemd user without Docker-socket access;
- host NTP synchronization remains required;
- CI validates deployment shell syntax and production Docker Compose configuration;
- a formal 24-hour soak report command and protected evidence location are documented.

No model training, paper trading, or live trading has been added. `LIVE_TRADING_ENABLED` remains false and Phase 3 must not begin until the actual 24-hour Phase 2 host gate is passed and documented.

## 0.2.1 — 20 August 2026

Phase 1 market discovery closed and Phase 2 opened.

Verified:

- GitHub Actions successfully queried live Polymarket Gamma;
- authentic 5m and 15m market payloads were captured in-repo;
- live payloads confirmed real condition/event/CLOB token IDs and current Chainlink BTC/USD 60-second TWAP rule metadata;
- focused unit fixtures were replaced with authentic captured values;
- 15 local tests pass against the authentic fixture shapes;
- compile and safe `RESEARCH` health checks pass.

No model, paper trading, or live trading has been added. Phase 2 is the 24/7 raw recorder.

## 0.2.0 — 20 August 2026

Phase 1 market-discovery implementation prepared.

Added:

- official Gamma `GET /markets/slug/{slug}` client;
- deterministic UTC-aligned BTC 5m/15m recurring slug discovery;
- strict Gamma market parser and Up/Down token-label mapping;
- per-market resolution-source/rules fingerprinting;
- normalized `polymarket_markets` schema, PostgreSQL migration, and repository;
- rule-change guard for an existing condition;
- live Gamma smoke workflow/artifact capture for network-enabled GitHub Actions;
- Phase 1 parser, client, discovery, storage, and service tests.

Corrected source-of-truth resolution details after verifying that current short BTC markets use the Chainlink BTC/USD 60-second TWAP stream while older examples used a different Chainlink rule version.

Phase 1 remains open until the live Gamma smoke succeeds and authentic live response fixtures are inspected. No model, paper trading, or live trading has been added.

## 0.1.1 — 20 August 2026

Phase 0 repository bootstrap completed locally and prepared for repository publication.

Added:

- Python 3.12+ project configuration;
- safe runtime defaults for Research/Paper/Live modes;
- active 5m/15m and optional 10m horizon configuration;
- machine-readable health command;
- JSON logging foundation;
- PostgreSQL 16 Docker Compose development service;
- `.env.example` and secret-safe `.gitignore`;
- pytest + Ruff development tooling;
- GitHub Actions CI;
- source-of-truth and project handoff documentation;
- Phase 0 implementation plan.

No market collector, prediction model, paper execution, or live trading code exists yet.

## 0.1.0 — 20 August 2026

Initial project/source-of-truth freeze.
