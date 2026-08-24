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
