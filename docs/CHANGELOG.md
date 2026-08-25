# Changelog

## 0.8.0 — 25 August 2026

Phase 8 — walk-forward backtester — closed after production-host acceptance of deterministic `walk-forward-v1` evaluation for the accepted Phase 7 `market_price` source runs on the verified 5m and 15m BTC Polymarket horizons. The accepted operational candidate was `69d3f9f8967dfcd1c1a68c640c242bd2b77cc089`. Fresh exact-head pre-host gates passed on that commit: CI #774, Historical Backfill Smoke #286, Live Recorder Smoke #390, and Recorder Short Soak #354. Production acceptance used the fixed half-open window `2026-08-24T00:00:00Z <= t < 2026-08-25T00:00:00Z` and returned `VERDICT=PASS`.

The backtester uses whole-market chronological duration-based rolling train/validation/test folds, one-market embargo protection, validation-only prediction-offset selection, non-reused ordinary test markets, and a separate final holdout outside all ordinary folds. Phase 8 acceptance found zero partition-overlap violations, zero ordinary-test reuse violations, zero single-class evaluated partitions, zero prediction-coverage violations, zero non-finite metric violations, and zero execution-semantic violations. Immediate rerun semantics matched exactly and the immutable backtest registry remained unchanged on the second pass.

The accepted 5m run is `phase8-300-efdf493067e9d56419afc4d88452bec6` with dataset SHA-256 `d5d2843ea2882aebe1cd3612e4345062067430d060824209b955a30590d8a6c2`, config SHA-256 `0ad67e69632d3b52c96b4970f0a5de640f0660d28eae86324c5b65454652c75a`, plan SHA-256 `2be73910d90903582ae884d801125ed29e36206b97671edd7e4c1d64efaf6d04`, and semantic SHA-256 `efdf493067e9d56419afc4d88452bec6effb871482664d19f109b3bbe4dd1d93`. Across six ordinary folds it evaluated 144 OOS markets at 0.8264 accuracy, 0.3344 log loss, and 0.1063 Brier score. Observed selected-side best-ask execution coverage was 0.4306 and gross P&L before costs was -1.465. The untouched final holdout was 0.8333 accurate, with 0.8333 execution coverage and gross P&L -0.26. Validation selected 240 seconds in every ordinary fold.

The accepted 15m run is `phase8-900-64aaf2b1774ee7af37bd110b84b37ec1` with dataset SHA-256 `21b71a29a01c97f63af306fdc48c3b88c5cfbd203bfd70999acaff1053a6ed6f`, the same config SHA-256, plan SHA-256 `53bdd6643d6388f19f7dc5a771a4040d50a98248fa3e62d08fd9fb5a763e8328`, and semantic SHA-256 `64aaf2b1774ee7af37bd110b84b37ec19f85bdc875a283986d4dba16ae921828`. Across six ordinary folds it evaluated 48 OOS markets at 0.9792 accuracy, 0.1068 log loss, and 0.0292 Brier score. Observed-ask execution coverage was only 0.2083 and gross P&L before costs was +0.381. The untouched final holdout fell to 0.625 accuracy with 0.625 execution coverage and gross P&L -0.47. Validation-selected ordinary-fold offsets were 840, 840, 780, 780, 840, and 840 seconds. The high ordinary-OOS 15m headline is therefore not treated as a stable trading result and individual timing slices are not cherry-picked.

Execution diagnostics are intentionally conservative. A hypothetical fill exists only when the frozen prediction side has an observed, fresh selected-side best ask; missing or stale book state is unavailable/no-fill. Midpoint fills, price-history substitutes, and synthetic fills are forbidden. Reported P&L is gross before fees, slippage, latency, and other costs, so Phase 8 makes no net-profitability claim. The accepted results explicitly show that predictive accuracy does not automatically imply executable edge.

Two host-gate defects were fixed test-first without weakening research semantics. The first acceptance run reached valid reports but a brittle source-code probe incorrectly demanded literal `pm_up_best_ask` and `pm_down_best_ask` tokens even though execution correctly constructed the selected-side key dynamically; the probe was changed to validate the actual dynamic implementation. The postflight full storage aggregate report was also removed from the critical acceptance path in favor of the bounded `disk-health` check already used preflight, retaining fail-closed disk safety while avoiding the known long-running storage scan.

Final host safety remained intact: disk status was `ok` before and after acceptance with 126,673,256,448 and 126,290,612,224 bytes free respectively; the recorder remained active before and after; `LIVE_TRADING_ENABLED=false`; and maximum trade-size and daily-loss limits remained zero.

Sanitized closeout evidence is stored in `docs/evidence/phase-8-closeout-20260825.json`. Phase 9 — probability calibration + edge engine — is now the next permitted build-order phase. It must calibrate probabilities only on permitted training/validation data, compare them with observed executable prices, account for spread/fees/slippage/uncertainty/staleness, and abstain when the configured minimum edge is not met. Live prediction, paper trading, and live trading remain blocked by later phase gates; live trading remains disabled.

## 0.7.0 — 25 August 2026

Phase 7 — baselines before fancy ML — closed after production-host acceptance of the deterministic `supervised-core-v1` modeling pipeline for the verified 5m and 15m BTC Polymarket horizons. The accepted operational candidate was `66bae5c71eab5e2c154cff1144ce509101d6e985`. Fresh exact-head pre-host gates passed on that commit: CI #635, Historical Backfill Smoke #218, Live Recorder Smoke #322, and Recorder Short Soak #286. Production acceptance used the fixed half-open market-start window `2026-08-24T00:00:00Z <= t < 2026-08-25T00:00:00Z` and returned `VERDICT=PASS`.

The supervised dataset joins immutable `core-v1` features to `official-outcome-v1` labels only after feature generation. `chronological-market-v1` keeps every feature row for a `condition_id` inside one chronological train, validation, test, or embargo partition, with no random feature-row shuffle. Training-only median imputation/scaling, equal-market weighting, deterministic dataset/split hashes, calibration/coverage metrics, immutable model-training registry rows, and external joblib artifact SHA-256 manifests are all part of the accepted contract.

Production acceptance expanded coverage to 288 labeled 5m markets and 96 labeled 15m markets, producing 1,152 5m and 1,344 15m `core-v1` feature rows. The full-day expansion preserved all 104 previously accepted Phase 6 feature rows without recomputing or rewriting them; later recovered source history is used only to create previously missing natural keys. Strict/default generation continues to fail closed on semantic feature conflicts.

The accepted 5m run is `phase7-300-0a822e17ceced11742bf6d3bc8214f44` with dataset SHA-256 `d5d2843ea2882aebe1cd3612e4345062067430d060824209b955a30590d8a6c2`, split SHA-256 `50a05c4240decee89de7f1b1341d61068a972636ff9b530477159b2c9653d9e0`, and semantic SHA-256 `0a822e17ceced11742bf6d3bc8214f44f4755c7bc23bb1d3f2dcfa897f3edcc0`. The accepted 15m run is `phase7-900-e36d978aecc29816c5b9e2b67b30d6e2` with dataset SHA-256 `21b71a29a01c97f63af306fdc48c3b88c5cfbd203bfd70999acaff1053a6ed6f`, split SHA-256 `2d70e523dabfbd3282b8b93e343459512244c4c4de2864881f7b75d252a7e695`, and semantic SHA-256 `e36d978aecc29816c5b9e2b67b30d6e218a0af6e08e6b7f31c10161ec1fc2a0b`.

For both verified horizons the validation champion is the simple Polymarket `market_price` baseline. XGBoost recorded `boosted_promotion_eligible=false` for both horizons, so Phase 7 does not justify escalating model complexity. The final test cannot rewrite the validation champion. The accepted report includes per-offset metrics for later timing analysis; for example, the 15m market-price baseline recorded 0.80 accuracy at 780 seconds and 0.85 accuracy at 840 seconds on 20-market offset slices, but Phase 7 does not select an optimal prediction time from those final-test slices.

The immediate training rerun matched semantically, the model registry remained at two rows on the second run, cross-partition condition violations were zero, single-class partition violations were zero, and artifact hash violations were zero. Bybit historical REST remained the already-audited production-host HTTP 403 limitation; no route-around or synthesized history was used. Disk status was `ok` before and after acceptance with 128,369,307,648 and 127,244,443,648 bytes free respectively; the recorder remained active; `LIVE_TRADING_ENABLED=false`; and trade-size/daily-loss limits remained zero.

Several failed acceptance attempts are preserved as regression evidence. The candidate runner was changed from a shared root-created worktree to a verified root-owned Git worktree plus exact `git archive` export into a `bp`-owned non-Git build tree, eliminating package-build and Git dubious-ownership coupling without global `safe.directory` exceptions. The full-day expansion then exposed immutable-feature conflicts after historical source enrichment; an explicit preserve-existing mode was added test-first so frozen Phase 6 rows remain authoritative while missing full-day keys can be generated. The earlier expensive preflight full storage report was also replaced by a lightweight disk-health preflight while retaining the full post-run report.

Sanitized closeout evidence is stored in `docs/evidence/phase-7-closeout-20260825.json`. Phase 8 — walk-forward backtester — is now the next permitted build-order phase. It must preserve chronological evaluation, purging/embargo where required, frozen reproducible outputs, regime/timing breakdowns, and realistic executable prices. Live prediction, paper trading, and live trading remain blocked by later phases; live trading remains disabled.

## 0.6.0 — 25 August 2026

Phase 6 — feature engine — closed after production-host acceptance of deterministic, immutable, versioned `core-v1` feature snapshots built only from observations provably available at each feature timestamp.

The final host-accepted operational candidate was `71ab67f178f8dc30a1d933ff2e553a508bb08f02`. Fresh exact-head pre-host gates passed on that commit: CI #562, Historical Backfill Smoke #183, Live Recorder Smoke #287, and Recorder Short Soak #251. Production acceptance used the fixed half-open market-start window `2026-08-24T18:00:00Z <= t < 2026-08-24T19:00:00Z`, a 60-second feature cadence, and returned `VERDICT=PASS`.

The feature store is immutable at natural key `(condition_id, feature_at, feature_version)`. `core-v1` includes market-time geometry, Polymarket price state, BTC momentum/volatility from fully closed Coinbase candles, compact book/state observations when provably available and fresh, observed trailing trade flow when raw coverage is eligible, explicit missing/stale flags, and source cutoffs/fingerprints. Official outcome, official label references, resolution metadata, and label provenance are not feature inputs. `official_reference_distance` remains NULL with `official_reference_missing=true` because no independently verified first-party reference series was established for V1.

Production acceptance verified 16 target markets and 104 persisted feature rows. Both final feature-generation passes were existing-only (`inserted=0`, `existing=104`), proving immediate idempotence against the already-populated immutable rows. The gate found zero source cutoffs after feature time, zero duplicate natural keys, zero forbidden label/outcome keys, and zero official-reference contract violations. Disk status was `ok` with 133,556,445,184 bytes free, and the recorder remained active before and after acceptance. Live trading remained disabled with zero trade-size and daily-loss limits.

Two failed production acceptance attempts are intentionally preserved. Candidate `d38250c6f5fb68704ce306cfb051111b25c7c680` exposed a real leakage edge case: second-rounded compact-state buckets could contain `last_event_at` values a fraction of a second after `feature_at`. A RED regression test reproduced the failure, and `latest_state` was corrected to require both `bucket_at <= feature_at` and `last_event_at <= feature_at` in the source query while retaining post-selection fail-closed guards. Candidate `c76dfabb100129efd94501721c3d52820b13f4fa` then populated all 104 immutable rows but the later acceptance checker failed because it called `json_each_text` on a PostgreSQL `jsonb` column. A second RED regression test isolated that checker defect and the final candidate changed it to `jsonb_each_text` without rewriting feature data.

Missing-data semantics remain explicit rather than imputed. In the accepted window the final rerun reported 26 `coinbase_candles_missing`, 104 `official_reference_missing`, 76 missing and 72 stale rows for each Polymarket outcome book group, and 72 missing rows for both Polymarket trade flow and aggregate raw trade flow. These counts are research evidence about source coverage, not zeros or synthesized observations. Phase 3 raw-data exclusions and Phase 4 historical-source limitations remain binding downstream.

Sanitized closeout evidence is stored in `docs/evidence/phase-6-closeout-20260825.json`. Phase 7 — baseline modeling and model training — is now the next permitted build-order phase. Model work must join frozen feature rows to official labels only after feature generation, use leakage-safe time-ordered evaluation, and report calibration/coverage alongside accuracy. Backtesting, live prediction, paper trading, and live trading remain blocked by later phase gates; live trading remains disabled.

## 0.5.0 — 25 August 2026

Phase 5 — official outcome/label pipeline — closed after production-host acceptance of deterministic, immutable, leakage-safe labels derived only from preserved official Polymarket Gamma resolution evidence.

The host-accepted operational candidate was `3c39b626a37c7ac7c0a9c10caaabd4d0b6cf0325`. Fresh exact-head pre-host gates passed on that commit: CI #483, Historical Backfill Smoke #145, Live Recorder Smoke #249, and Recorder Short Soak #213. Production acceptance used the fixed half-open market-start window `2026-08-24T18:00:00Z <= t < 2026-08-24T19:00:00Z` and returned `VERDICT=PASS`.

The label pipeline stores immutable `official-outcome-v1` rows keyed by `(condition_id, label_version)`. Labels are generated offline from the Phase 4 `polymarket_market_snapshots` store, not from a live HTTP dependency. A snapshot may become label evidence only when the market is closed, the official outcome is unambiguous, and the snapshot was observed at or after market end. For each condition the canonical source is the earliest eligible resolved snapshot ordered by download time and snapshot id. Conflicting official-resolution semantics or attempts to change an existing semantic label fail closed rather than rewriting history.

Production acceptance generated 16 labels on the first pass and then immediately reran the exact same window with zero inserts and 16 existing rows. The gate verified zero leakage violations, zero contract violations, zero missing exact snapshot-provenance joins, zero duplicate natural keys, and matching condition coverage across the two runs. The recorder remained active before and after acceptance.

Official start/end reference prices remain NULL in V1. The preserved Gamma payloads and current Phase 5 evidence do not establish independently verified first-party start/end reference-price fields, so Coinbase, Bybit, CLOB token prices, trades, or inferred prices are not substituted into the authoritative label contract. Downstream feature work may use market/BTC observations as features only under its own timestamp and provenance rules; those observations do not become official label references.

Phase 3 raw-data exclusions and Phase 4 source-availability limitations remain binding downstream. In particular, unavailable/unverified historical Polymarket L2 remains unavailable rather than synthesized, and audited source gaps must remain explicit through feature missing-data flags.

Sanitized closeout evidence is stored in `docs/evidence/phase-5-closeout-20260825.json`. Phase 6 — feature engine — is now the next permitted build-order phase. Model training, backtesting, live prediction, paper trading, and live trading remain blocked by later phase gates; live trading remains disabled.

## 0.4.0 — 25 August 2026

Phase 4 — historical backfill — closed after production-host acceptance of deterministic Polymarket market discovery, official token-price history, Coinbase BTC-USD candles, immutable/idempotent historical storage, provenance/checksums, and environment-aware Bybit handling.

The host-accepted operational candidate was `29fa75b500858ae50f50b863d0c62ff2acb4ec52`. Fresh exact-head gates passed before closeout: CI #421, Historical Backfill Smoke #112, Live Recorder Smoke #220, and Recorder Short Soak #184. Production acceptance used the fixed half-open window `2026-08-24T18:00:00Z <= t < 2026-08-24T19:00:00Z` and returned `VERDICT=PASS`.

Polymarket historical market discovery now deterministically enumerates aligned `btc-updown-<horizon>-<window_start_epoch>` slugs for the verified 5m/15m horizons and fetches each exact Gamma market-by-slug payload. This replaces list/date-filter discovery after production acceptance repeatedly exposed HTTP 500 behavior on Gamma keyset queries and live verification showed the regular dated market list did not reliably include a known recent BTC market. A missing exact slug is an explicit coverage gap; a returned slug/window mismatch fails closed; exact-slug HTTP 500/503 responses receive bounded retries.

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
