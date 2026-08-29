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

Phase 14: begin **Live Readiness**. Phase 13 — Improvement Loop V1 — is production-host accepted on exact operational candidate `4dcdf8955b2c79ea9f130fec5a0dcceef915a678`.

Fresh exact-head Phase 13 CI run #1387 passed 637 Python tests plus Ruff, deployment validation, health in research/live-disabled mode, dashboard tests, strict TypeScript typecheck, and the Next.js production build. Production acceptance returned `PHASE13_HOST_ACCEPTANCE=PASS`, preserved all five production services, and kept paper reconciliation `OK` with zero violations.

The first immutable Phase 13 experiment tested a validation-selected 5m max-spread abstention guard. Challenger and accepted Phase 9 champion were identical on the reused ordinary-OOS comparison: both recorded `+0.148014` assumed-cost P&L, identical calibration metrics, and a paired economic delta/95% bootstrap interval of exactly zero. No independent fresh confirmation was available, so promotion was correctly ineligible and the deliberate immutable decision was `keep_champion`.

Sanitized closeout evidence is in `docs/evidence/phase-13-closeout-20260829.json`.

Phase 14 must complete the Master Source of Truth live-readiness gate before any real-money progression. `LIVE_TRADING_ENABLED=false`, maximum real trade size and daily loss remain zero, real execution remains unavailable, and explicit user authorization is still mandatory before any real order placement.
