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
