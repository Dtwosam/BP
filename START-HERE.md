# START HERE

This folder is the handoff pack for the **BTC Polymarket Prediction Engine**.

If you are opening a new ChatGPT/Codex chat, upload/add this pack to the project and say:

> Read `docs/MASTER-SOURCE-OF-TRUTH.md`, `PROJECT_STATE.json`, `docs/BUILD-ORDER.md`, and `AGENTS.md`. Continue the project from the current phase. Do not redesign or restart it from memory unless the source of truth explicitly requires a change.

## Authority order

1. `docs/MASTER-SOURCE-OF-TRUTH.md` — canonical project definition
2. `PROJECT_STATE.json` — where the build currently stands
3. `docs/BUILD-ORDER.md` — what to build next
4. `docs/DECISION-LOG.md` — why key decisions were made
5. `docs/CHANGELOG.md` — what changed over time
6. `AGENTS.md` — working rules for AI/developers

## Current next step

Phase 12: build **Paper Execution**. Phase 11 — Dashboard V1 — is production-host accepted and permanently installed on exact operational candidate `126959eaef973b061c3c7ea619b6d6313f3f4e4e`.

Fresh exact-head Phase 11 CI passed on that candidate with 511 Python tests plus dashboard test/typecheck/production-build gates. Isolated production-host acceptance returned `PHASE11_HOST_ACCEPTANCE=PASS` with 4 active markets, 4 feed-health rows, 2 performance rows, 26 immutable prediction-history rows, localhost-only candidate listeners, and the recorder still active. Permanent installation then returned `PHASE11_INSTALL=PASS`; `bp-recorder`, PostgreSQL, dashboard API, and dashboard web were all active, permanent listeners remained `127.0.0.1:8787` and `127.0.0.1:3000`, API health remained `RESEARCH` with live trading disabled, and POST mutation requests returned HTTP 405.

The dashboard remains read-only. At acceptance there were zero official evaluations, so performance surfaces correctly show only available append-only evidence and do not manufacture results. Paper P&L remains explicitly `UNAVAILABLE_UNTIL_PHASE_12` until simulated fills exist.

Phase 12 must implement realistic simulated execution against the immutable 5m/15m prediction signals using the same interface intended for later live trading. Model bid/ask, depth, partial fills, latency, slippage, cancellations, expiry, and fees; reconcile every paper trade to its immutable signal; and feed paper positions/P&L/execution diagnostics back into the dashboard. `LIVE_TRADING_ENABLED=false`; no wallet/signing/real-order path is authorized; Phase 14 live readiness and explicit user authorization remain mandatory before any real-money trading.
