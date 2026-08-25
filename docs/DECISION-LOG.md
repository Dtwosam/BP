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
