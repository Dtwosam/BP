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
**Status:** Active

Train and evaluate against the official Polymarket outcome. Current verified examples use Chainlink BTC/USD and resolve Up when end >= start.

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
