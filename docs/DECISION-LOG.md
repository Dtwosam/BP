# Decision Log

Decisions are append-only. Superseded decisions remain for history and point to the replacement.

## D-001 — Project objective
**Date:** 20 Aug 2026  
**Status:** Active

Build a system that ultimately trades short-duration Polymarket BTC Up/Down markets using a model-derived probability and market-price/edge comparison.

## D-002 — Accuracy target is aspirational
**Date:** 20 Aug 2026  
**Status:** Active

Approximately 80% accuracy is the desired research target, but must never be represented as guaranteed or proven until strict out-of-sample/live evidence supports it.

## D-003 — Market horizons are configurable
**Date:** 20 Aug 2026  
**Status:** Active

Verified initial Polymarket BTC Up/Down horizons are 5m and 15m. 10m is desired but not currently verified.

## D-004 — Resolution-target alignment
**Date:** 20 Aug 2026  
**Status:** Superseded by D-009

Train and evaluate against the official Polymarket outcome. Early checked examples used Chainlink BTC/USD and end-price-versus-start-price wording; current rules are versioned per D-009.

## D-005 — $0-first infrastructure
**Date:** 20 Aug 2026  
**Status:** Active

Validate the idea with free infrastructure where practical while keeping the architecture portable.

## D-006 — Data recorder before prediction model
**Date:** 20 Aug 2026  
**Status:** Active

Build the continuously running BTC + Polymarket recorder before serious model work.

## D-007 — Controlled retraining
**Date:** 20 Aug 2026  
**Status:** Active

The engine does not blindly learn after every trade. Live history enters a versioned champion/challenger retraining process.

## D-008 — No live money until gated
**Date:** 20 Aug 2026  
**Status:** Active

Progression is Research → Paper → Live. Live trading requires documented validation, security/risk readiness, geographic eligibility checks, and explicit user authorization.

## D-009 — Resolution rules are versioned market data
**Date:** 20 Aug 2026  
**Status:** Active

Current checked BTC 5m/15m Rules use the Chainlink BTC/USD 60-second TWAP stream and TWAP-over-range wording, while older short markets used the regular BTC/USD stream and end-price-versus-start-price wording. The engine must preserve exact rules text/source and a rules fingerprint for every market. Official Polymarket resolution remains the authoritative label.

## D-010 — Phase 2 primary BTC venue starts with Bybit
**Date:** 20 Aug 2026  
**Status:** Active

Phase 2 begins with Bybit public BTCUSDT spot and linear-perpetual WebSocket feeds as the primary BTC venue because the official V5 API exposes real-time public trades, ordered snapshot/delta books, matching-engine timestamps, and separate spot/linear streams without private credentials. A secondary venue is added after the primary path is stable; Coinbase Advanced Trade is the initial secondary candidate because its public `level2`, `market_trades`, and heartbeat channels are available without authentication.

## D-011 — Phase 3 archive retention is additional to hot raw retention
**Date:** 24 Aug 2026  
**Status:** Active

`STORAGE_HOT_RAW_HOURS=24` and `STORAGE_ARCHIVE_RETENTION_HOURS=24` mean approximately 24 hours of hot raw PostgreSQL data followed by 24 additional hours of verified local archive retention, for roughly 48 hours of full-raw recoverability. Archive pruning therefore uses the sum of the hot and archive retention windows when evaluating event-time archive intervals.

An archive may be pruned only after the archive and manifest verify, compact state has advanced beyond the interval, and the exact interval contains no remaining raw rows. The raw-empty guard prevents an interrupted partial deletion from losing the only complete copy of an interval. Maintenance remains fail-closed at critical disk status and never substitutes manual deletion of unarchived raw data.

## D-012 — Raw coverage exclusions are first-class dataset metadata
**Date:** 24 Aug 2026  
**Status:** Active

The exact interval `2026-08-22T20:00:00Z` through `2026-08-22T21:00:00Z` is excluded from raw-dependent model training because 250,000 events are known to be missing from both PostgreSQL and the surviving forensic archive. It may be admitted only if independently recovered from a trustworthy source and revalidated.

A separate Phase 3 rollout-era local coverage limitation is also excluded from raw-dependent research unless independently reacquired. Surviving VM artifacts do not prove whether unavailable local raw coverage after `2026-08-23T21:00:00Z` and before the compact-state rollout was earlier pruning history or capture downtime. Compact state was first observed with all four feeds in the `2026-08-24T10:00:00Z` hour, so absence of compact rows before that rollout is not, by itself, evidence of recorder downtime. Future dataset builders must carry these exclusions explicitly rather than silently treating missing local raw data as complete history.

## D-013 — Historical observations are immutable and reruns fail closed on conflicts
**Date:** 25 Aug 2026  
**Status:** Active

Phase 4 historical storage uses immutable natural keys. Re-fetching an identical historical observation is a no-op/existing-row result. If the same natural key later carries a different value, the backfill raises `HistoricalDataConflict` instead of silently rewriting history.

Every external historical chunk records source/dataset identity, exact request parameters, download timestamp, row count, and canonical SHA-256 provenance. This contract is required for reproducibility and must be preserved by downstream dataset builders.

## D-014 — Bybit historical REST is optional only when a documented host restriction is explicitly audited
**Date:** 25 Aug 2026  
**Status:** Active

Bybit V5 spot and linear BTCUSDT historical candle support remains implemented, but Bybit documents HTTP 403 restrictions for US IP addresses. Both GitHub US-hosted runners and the production GCP `us-east1` recorder host produced that condition during Phase 4 validation.

In standard historical backfill, only the narrowly classified Bybit HTTP 403 restriction may terminate as `unavailable`, with zero rows/chunks and a durable reason. `standard --require-bybit` and explicit Bybit-only commands remain strict. The project will not bypass, tunnel around, or otherwise evade provider geographic/service restrictions. Coinbase BTC-USD public candles are the mandatory verified core BTC historical series for Phase 4.

## D-015 — Phase 4 Polymarket historical market discovery uses deterministic exact BTC slugs
**Date:** 25 Aug 2026  
**Status:** Active

Phase 4 does not depend on Gamma keyset/date-filter market listing for BTC 5m/15m historical coverage. Production acceptance repeatedly received HTTP 500 from the bounded keyset query even with retry, while a separate regular dated market-list live check did not reliably include a recent completed BTC market that exact slug lookup had already returned.

Historical discovery therefore enumerates aligned `btc-updown-<horizon>-<window_start_epoch>` slugs for the verified 5m and 15m horizons and fetches each exact Gamma market-by-slug payload. A 404 is an explicit coverage gap; any returned slug/window mismatch fails closed; HTTP 500/503 exact-slug responses receive only bounded retries. This discovery contract is reproducible and directly matches the recurring market naming contract already validated in Phase 1.

## D-016 — Unavailable historical order-book depth must remain unavailable, never synthesized
**Date:** 25 Aug 2026  
**Status:** Active

Phase 4 did not verify a current first-party Polymarket endpoint that provides historical L2/order-book depth for the required past BTC markets. Historical L2 is therefore represented as unavailable/unverified data, not reconstructed from token prices, trades, compact snapshots, or assumptions.

Downstream feature work must distinguish genuinely observed live/retained book data from historical periods where depth was not captured. Missing historical order-book information must remain explicit rather than being fabricated to create apparent coverage.

## D-017 — Official labels are immutable, post-resolution derivatives of preserved Gamma snapshots
**Date:** 25 Aug 2026  
**Status:** Active

Phase 5 labels use the official resolved Polymarket outcome parsed from preserved Phase 4 Gamma snapshots. Label generation is offline and network-free. A snapshot is eligible only when the market is closed, the official outcome is unambiguous, and the snapshot was observed at or after the market end; any apparently resolved snapshot observed before market end is treated as leakage/data-integrity failure.

For each condition, the canonical source is the earliest eligible resolved snapshot ordered by `downloaded_at` and snapshot id. All eligible snapshots must agree on market identity, window, rules fingerprint/source, and resolved outcome. Contradictory official-resolution evidence raises a source conflict. Stored labels are versioned by `(condition_id, label_version)`; identical reruns are no-ops and semantic relabel attempts fail closed.

## D-018 — Unverified official start/end reference prices remain NULL
**Date:** 25 Aug 2026  
**Status:** Active

Phase 5 does not infer or substitute the official market-resolution start/end reference prices from Coinbase candles, Bybit candles, Polymarket token prices, trades, or other secondary observations. The preserved Gamma evidence used for V1 labels does not independently verify a first-party start/end reference-price field, so `start_reference` and `end_reference` remain NULL in `official-outcome-v1`.

Future work may populate those fields only when a trustworthy first-party resolution source is explicitly verified and provenance/versioning are defined. Market/BTC prices remain valid candidate features under Phase 6 feature-time rules, but they are not silently promoted into the authoritative label contract.

## D-019 — Feature versions freeze source-selection and missing-data semantics
**Date:** 25 Aug 2026  
**Status:** Active

Phase 6 stores immutable feature snapshots keyed by `(condition_id, feature_at, feature_version)`. `core-v1` fixes the source-selection rules, availability cutoffs, staleness threshold, trailing-window lengths, formulas, feature names, and missing-data semantics. Identical reruns are existing/no-op; changing any of those semantics requires a new feature version rather than rewriting a `core-v1` row.

Feature generation must not read official outcomes, official label references, resolution metadata, or label provenance. Historical L2 that was not observed remains unavailable rather than synthesized, raw-dependent groups remain missing when Phase 3 exclusions or unproven feed coverage overlap the required window, and the unverified official reference distance remains NULL with an explicit missing flag.

## D-020 — Compact state is usable only when both bucket time and last event time are in the feature past
**Date:** 25 Aug 2026  
**Status:** Active

A `market_state_1s` row is eligible at feature time `T` only if both `bucket_at <= T` and `last_event_at <= T`. The reader must select the latest row satisfying both conditions, not select a bucket by `bucket_at` and then abort merely because that bucket contains a later sub-second event. Post-selection leakage guards remain as defense in depth.

This rule was promoted to an explicit project decision after production acceptance candidate `d38250c6f5fb68704ce306cfb051111b25c7c680` exposed the same-second sub-second leakage edge case. The fix was regression-tested before implementation and final Phase 6 host acceptance verified zero persisted source cutoffs after feature time.

## D-021 — Supervised splits are chronological and indivisible by market
**Date:** 25 Aug 2026  
**Status:** Active

Phase 7 treats `condition_id` as the indivisible supervised-learning grouping key. Every feature timestamp from one market must remain wholly inside one train, validation, test, or embargo partition. Random row shuffles are forbidden because multiple feature timestamps share the same eventual market outcome and path; splitting those rows independently would leak correlated market information across evaluation boundaries.

The initial split contract is `chronological-market-v1`: unique markets are ordered by `(market_start_at, condition_id)`, assigned chronologically, and protected by one whole-market embargo at each train/validation and validation/test boundary when the sample permits it. Preprocessing is fitted on training data only, and production acceptance requires zero cross-partition condition overlap and both classes in every non-embargo partition.

## D-022 — Validation chooses the champion; the final test cannot rewrite it
**Date:** 25 Aug 2026  
**Status:** Active

Model selection is frozen from validation results before final-test metrics are used. Phase 7 compares the weighted prior, Polymarket market-price baseline, logistic regression, and deterministic XGBoost challenger; `validation_champion` is selected by validation log loss with documented tie-breaks. The final test is evidence about the already-frozen candidates and cannot change the champion, feature schema, threshold, or preprocessing.

XGBoost is promotion-eligible only if it beats the simple baselines under the documented validation log-loss/Brier rule and confirms the required test behavior without a worse Brier score. Phase 7 production acceptance selected `market_price` for both 5m and 15m and recorded `boosted_promotion_eligible=false` for both. This is a successful complexity stop: later work must not escalate model complexity merely because a more complex model exists.

## D-023 — Historical expansion preserves already-frozen feature snapshots
**Date:** 25 Aug 2026  
**Status:** Active

Phase 7 may expand source history over a broader research window after earlier `core-v1` rows have already been accepted. Such later source recovery must not retroactively rewrite those immutable snapshots or silently pretend the recovered observations were part of the original materialization context.

The explicit Phase 7 expansion mode therefore checks for an existing `(condition_id, feature_at, core-v1)` key before feature recomputation, validates that its static market metadata still matches, preserves the existing row untouched, and computes only missing natural keys from the expanded history. Normal/default feature generation remains strict and raises `FeatureConflict` on semantic drift. Production acceptance proved that all 104 previously accepted Phase 6 feature rows were preserved while the full-day feature set was expanded.

## D-024 — Walk-forward evaluation selects timing on validation only and never reuses ordinary test markets
**Date:** 25 Aug 2026  
**Status:** Active

Phase 8 uses deterministic duration-based chronological rolling folds over whole `condition_id` markets. Train, validation, and ordinary test partitions are disjoint, a whole-market embargo protects boundaries, and an ordinary test market cannot appear as test evidence in more than one fold. A final holdout is outside all ordinary folds.

Prediction-offset selection is performed only from each fold's validation candidates. Ordinary test results and the final holdout cannot rewrite the selected offset, source model, feature contract, or evaluation configuration. The Phase 8 production gate requires zero partition overlap, zero ordinary-test reuse, both classes in evaluated partitions, and deterministic semantic reruns.

## D-025 — Backtest fills require an observed selected-side best ask; unavailable books are no-fill
**Date:** 25 Aug 2026  
**Status:** Active

Phase 8 execution diagnostics use only the observed best ask for the side implied by the frozen probability decision: `pm_up_best_ask` for Up and `pm_down_best_ask` for Down, selected dynamically by side. Missing or stale selected-side book state is unavailable/no-fill. Midpoint fills, price-history substitutes, and synthetic fills are forbidden.

Reported execution P&L is explicitly gross before fees, slippage, latency, and other costs. It is diagnostic evidence, not a net-profitability claim. Phase 9 must preserve these availability semantics when calculating edge and must not create apparent trade coverage by inventing executable prices.

## D-026 — Accuracy alone cannot promote a trading rule; Phase 9 must optimize calibrated executable edge and abstention
**Date:** 25 Aug 2026  
**Status:** Active

Phase 8 production evidence demonstrates why the project's aspirational accuracy target is not itself a trading criterion. The 5m accepted walk-forward report reached 0.8264 ordinary OOS accuracy over 144 markets and 0.8333 on the final holdout, yet observed-ask gross P&L was negative in both aggregate ordinary OOS and final holdout. The 15m report reached 0.9792 ordinary OOS accuracy over only 48 markets, but the untouched final holdout fell to 0.625 accuracy and gross P&L was negative.

Therefore the high 15m ordinary-OOS headline and individual offset slices must not be cherry-picked into a trading rule. Phase 9 should calibrate probabilities using permitted training/validation data, compare them with observed executable selected-side prices, account for spread/fees/slippage/uncertainty/staleness, and abstain when a configured minimum net edge is not met. Any threshold or calibration choice must be frozen before untouched evaluation is consulted.
