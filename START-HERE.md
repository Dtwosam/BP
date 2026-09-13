# START HERE

This folder is the handoff pack for the **BTC Polymarket Prediction Engine**.

If you are opening a new ChatGPT/Codex chat, upload/add this pack to the project and say:

> Read `docs/MASTER-SOURCE-OF-TRUTH.md`, `PROJECT_STATE.json`, `docs/BUILD-ORDER.md`, `docs/DECISION-LOG.md`, `AGENTS.md`, and `docs/superpowers/specs/2026-09-13-phase-14-btc-first-v3-challenger-design.md`. Continue the project from the current phase. Do not redesign or restart it from memory unless the source of truth explicitly requires a change.

## Authority order

1. `docs/MASTER-SOURCE-OF-TRUTH.md` — canonical project definition
2. `PROJECT_STATE.json` — where the build currently stands
3. `docs/BUILD-ORDER.md` — what to build next
4. `docs/DECISION-LOG.md` — why key decisions were made
5. `docs/CHANGELOG.md` — what changed over time
6. `AGENTS.md` — working rules for AI/developers
7. `docs/superpowers/specs/2026-09-13-phase-14-btc-first-v3-challenger-design.md` — active Phase 14 BTC-first research correction

## Current next step

Phase 14 Live Readiness engineering is complete, but the Master live gate is blocked. The current project status remains `PHASE_14_ENGINEERING_COMPLETE_LIVE_GATE_BLOCKED`; Phase 15 is **not permitted**.

A read-only production investigation on 13 September 2026 found that the current paper signal is not a reliable independent BTC-direction forecast. Across 84 settled paper trades it was correct against the official outcome 26 times (30.95%) and realized about `-100 USD`. Fresh Coinbase BTC state showed simple pre-decision BTC direction matching final Coinbase direction 64/84 times (76.19%), while the paper machine matched final Coinbase direction only 31/84 times (36.90%).

The investigation also isolated the apparent-edge failure: the live probability path uses an older Polymarket Up-token historical price while execution uses a fresher current book ask. Gap bands of 30% or more were 2/45 correct and lost about `-120.24 USD`; high-gap trades placed against BTC momentum were 0/38 correct in the diagnosis sample. Token mapping, selected-book mapping, and calibration-side-flip checks were clean.

Therefore the current `core-v2-last-trade` adaptive training cycle is **paused even though readiness is true**. Do not run `adaptive-train`, do not reset the existing bootstrap/readiness evidence, and do not treat the 84-trade diagnosis cohort as a validation/test/holdout set for new policy selection.

The active research direction is the separately versioned **BTC-first V3 challenger** described in `docs/superpowers/specs/2026-09-13-phase-14-btc-first-v3-challenger-design.md`:

```text
feature_version = core-v3-btc-native
label_version   = official-outcome-v1
horizon_seconds = 300
feature_offsets = 60, 120, 180, 240
```

Its forecast must be produced from BTC-native Coinbase/Bybit state. Polymarket price/book data is used afterward as the executable benchmark/price-to-beat, not as the source of the first V3 forecast probability. V1 and V2 evidence remain immutable.

The exact next repository task is **V3 Gate A only**: write the test-first implementation plan and then implement timestamp-coherent BTC-native source/calculator/feature generation for `core-v3-btc-native`, preserving all V1/V2 behavior and with no model training or production mutation.

Safety remains unchanged: `LIVE_TRADING_ENABLED=false`, real trade-size and daily-loss limits remain zero, automatic promotion remains false, no production deployment/restart/migration/model activation/final-holdout access/paper activation/live trading/geographic bypass is authorized.