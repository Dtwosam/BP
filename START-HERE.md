# START HERE

This folder is the handoff pack for the **BTC Polymarket Prediction Engine**.

If you are opening a new ChatGPT/Codex chat, upload/add this pack to the project and say:

> Read `docs/MASTER-SOURCE-OF-TRUTH.md`, `PROJECT_STATE.json`, `docs/BUILD-ORDER.md`, `docs/DECISION-LOG.md`, `AGENTS.md`, `docs/superpowers/specs/2026-09-13-phase-14-btc-first-v3-challenger-design.md`, and `docs/evidence/phase-14-v3-preregistration-handoff-20260913.json`. Continue the project from the current phase. Do not redesign or restart it from memory unless the source of truth explicitly requires a change.

## Authority order

1. `docs/MASTER-SOURCE-OF-TRUTH.md` — canonical project definition
2. `PROJECT_STATE.json` — where the build currently stands
3. `docs/BUILD-ORDER.md` — what to build next
4. `docs/DECISION-LOG.md` — why key decisions were made
5. `docs/CHANGELOG.md` — what changed over time
6. `AGENTS.md` — working rules for AI/developers
7. `docs/superpowers/specs/2026-09-13-phase-14-btc-first-v3-challenger-design.md` — active Phase 14 BTC-first research correction
8. `docs/evidence/phase-14-v3-preregistration-handoff-20260913.json` — durable V3 Gate A acceptance and Gate B preregistration delta handoff

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

V3 Gate A repository implementation was merged through PR #192. The audited implementation checkpoint is `e09e9834260996553fa3cff7c18bf5269e48f4f0`, CI run `34757241403` passed, and merged `main` is `8b2d983ec75692de7ed39043c4a0e19dcf691942`.

Gate A production coverage is now accepted **PASS**. Sanitized evidence is frozen at `docs/evidence/phase-14-v3-gate-a-production-20260913.json`. It covers 17 markets / 68 immutable V3 rows from `2026-09-13T13:45:00Z` through `2026-09-13T15:10:00Z`, with coverage hash `32c283a7769681ebe5b2e0d1fe255ad6c38aa5b0301303f8fe86f4e7b2278ffb`, zero future-cutoff violations, zero Polymarket predictor keys, and complete current-state availability for Coinbase, Bybit spot, and Bybit linear. No model training or activation occurred as part of that acceptance.

V3 Gate B preregistration is also frozen. The approved design is commit `c9e179c91ea990ca4a25a13f69fc5932811fb32a`, with the implementation sequence durably tracked in Issue #193. The read-only readiness/planning implementation checkpoint is `4efa403f0fcfcc9a7d4717c48d6da721fb3a1f38`, and CI run `34773596855` passed the full repository suite, deployment-asset validation, health check, and dashboard checks. The frozen research plan identity is `v3-gate-b-preregister-v1`: epoch `2026-09-13T13:45:00Z` through `2026-09-16T13:45:00Z`, exactly five ordinary folds, a final 12-hour reserved window, and separate hash-bound `diagnosis` / `consumed_v2_final_holdout` exclusions. Readiness remains outcome-blind and planning remains feature-only/read-only.

The durable V3 delta handoff is `docs/evidence/phase-14-v3-preregistration-handoff-20260913.json`. It does **not** authorize model fitting, label access for the final reserved window, final-holdout evaluation, activation, deployment, or live trading.

## Immediate next task

Continue prospective `core-v3-btc-native` feature collection through the frozen epoch end `2026-09-16T13:45:00Z`. After the epoch is complete, run the outcome-blind V3 `readiness` command with both frozen exclusion manifests. Only if readiness passes, run the feature-only `plan` command to freeze the five ordinary folds, final reserved membership, and deterministic hashes.

Do not start model fitting during this step. Do not read final-holdout labels or evaluate the final holdout. Those remain separate authorization boundaries.

Safety remains unchanged: `LIVE_TRADING_ENABLED=false`, real trade-size and daily-loss limits remain zero, automatic promotion remains false, no production deployment/restart/migration/model activation/final-holdout access/paper activation/live trading/geographic bypass is authorized.