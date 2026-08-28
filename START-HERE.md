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

Phase 13: begin the **Improvement Loop**. Phase 12 — Paper Execution — is production-host accepted and permanently installed on exact operational candidate `159ce77af9a51ae208511d216bee52d5732cee3b`.

Fresh exact-head Phase 12 CI run #1328 passed 564 Python tests plus Ruff, deployment validation, health, dashboard tests, strict TypeScript typecheck, and the Next.js production build. Host acceptance and permanent install both passed; paper execution is available, real execution remains unavailable, cash is nonnegative, and reconciliation has zero violations.

Phase 13 must use explicit hypotheses and champion/challenger experiments. Candidate model, calibration, timing, feature, or abstention changes must be judged on permitted out-of-sample evidence and executable-price economics; untouched holdouts must not be reused for selection, and one unusually good backtest cannot promote a challenger.

`LIVE_TRADING_ENABLED=false`, maximum real trade size and daily loss remain zero, and no wallet/signing/order-placement path is authorized. Phase 14 live readiness and explicit authorization remain mandatory before real-money trading.
