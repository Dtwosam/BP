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

Phase 7: build the leakage-safe baseline modeling and model-training pipeline from frozen `core-v1` feature rows joined to `official-outcome-v1` targets only after feature generation. Phase 6 feature engineering is complete and production-host accepted.

Start with simple auditable baselines, time-ordered evaluation, calibration/coverage metrics, and explicit source/missingness handling. Do not begin Phase 8 backtesting until Phase 7 acceptance is recorded.

Live prediction, paper trading, and live trading remain blocked by later phase gates. Live trading remains disabled.
