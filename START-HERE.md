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

Phase 14 Live Readiness engineering is complete, but the Master live gate is blocked. The current project status is `PHASE_14_ENGINEERING_COMPLETE_LIVE_GATE_BLOCKED`; Phase 15 is **not permitted**.

Production host acceptance passed on exact candidate `5854e3003aa3340ce3733bf4532e204c1ec55836` with all required services active, official SDK import passing, interlock/risk/reconciliation checks passing, `REAL_ORDER_SIDE_EFFECTS=0`, and real-money limits still zero. The official Polymarket geoblock check returned `GEOBLOCK_BLOCKED=true`, so geographic eligibility fails closed.

The economic evidence also does not clear the Master gate. Phase 12/13 contain only a tiny prospective paper sample, Phase 9's untouched 5m final holdout was negative after assumed costs, Phase 13 had no positive economic uncertainty or independent confirmation, and prospective live-regime calibration remains insufficiently demonstrated. No explicit authorization for real-money trading has been recorded.

Sanitized closeout evidence and the explicit pass/fail/insufficient matrix are in `docs/evidence/phase-14-closeout-20260830.json`.

Continue **money-disabled prospective paper trading/evaluation only**: accumulate immutable outcomes and fills, measure after-cost expectancy and uncertainty, validate prospective calibration, and re-check geographic/compliance eligibility only through official permitted mechanisms. Do not bypass restrictions. `LIVE_TRADING_ENABLED=false`, maximum real trade size and daily loss remain zero, and Phase 15 stays blocked until every Master live-gate item passes and real-money authorization is explicit.
