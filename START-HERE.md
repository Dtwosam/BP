# START HERE

This folder is the handoff pack for the **BTC Polymarket Prediction Engine**.

If you are opening a new ChatGPT/Codex chat, upload/add this pack to the project and say:

> Read `docs/MASTER-SOURCE-OF-TRUTH.md`, `PROJECT_STATE.json`, `docs/BUILD-ORDER.md`, `docs/DECISION-LOG.md`, `AGENTS.md`, `docs/superpowers/specs/2026-09-13-phase-14-btc-first-v3-challenger-design.md`, `docs/superpowers/specs/2026-09-14-phase-14-v3-gate-b-successor-preregistration.md`, `docs/evidence/phase-14-v3-preregistration-handoff-20260913.json`, and `docs/evidence/phase-14-v3-gate-b-successor-preregistration-20260914.json`. Continue the project from the current phase. Do not redesign or restart it from memory unless the source of truth explicitly requires a change.

## Authority order

1. `docs/MASTER-SOURCE-OF-TRUTH.md` — canonical project definition
2. `PROJECT_STATE.json` — where the build currently stands
3. `docs/BUILD-ORDER.md` — what to build next
4. `docs/DECISION-LOG.md` — why key decisions were made
5. `docs/CHANGELOG.md` — what changed over time
6. `AGENTS.md` — working rules for AI/developers
7. `docs/superpowers/specs/2026-09-13-phase-14-btc-first-v3-challenger-design.md` — BTC-first V3 research design
8. `docs/evidence/phase-14-v3-preregistration-handoff-20260913.json` — historical V3 Gate A / Gate B v1 handoff
9. `docs/superpowers/specs/2026-09-14-phase-14-v3-gate-b-successor-preregistration.md` — approved Gate B successor design
10. `docs/evidence/phase-14-v3-gate-b-successor-preregistration-20260914.json` — durable successor preregistration decision evidence

## Current next step

Phase 14 Live Readiness engineering remains research-only and the Master live gate remains blocked. Phase 15 is **not permitted**. Safety remains `LIVE_TRADING_ENABLED=false`, real trade-size and daily-loss limits remain zero, and automatic promotion remains false.

The active research direction remains the separately versioned **BTC-first V3 challenger**:

```text
feature_version = core-v3-btc-native
label_version   = official-outcome-v1
horizon_seconds = 300
feature_offsets = 60, 120, 180, 240
```

V3 forecasting uses BTC-native Coinbase/Bybit state. Polymarket price/book data remains the downstream executable benchmark/price-to-beat and is not a V3 forecast predictor. Existing V1/V2 evidence remains immutable.

### Historical Gate A acceptance

V3 Gate A remains accepted **PASS**. Sanitized production evidence is frozen at `docs/evidence/phase-14-v3-gate-a-production-20260913.json`. It covers 17 markets / 68 immutable V3 rows beginning at `2026-09-13T13:45:00Z`, with coverage hash `32c283a7769681ebe5b2e0d1fe255ad6c38aa5b0301303f8fe86f4e7b2278ffb`, zero future-cutoff violations, zero Polymarket predictor keys, and complete current-state availability for Coinbase, Bybit spot, and Bybit linear. No model training or activation occurred as part of that acceptance.

The historical Gate B v1 preregistration design was approved at commit `c9e179c91ea990ca4a25a13f69fc5932811fb32a` and froze the original epoch ending `2026-09-16T13:45:00Z`. That historical evidence remains immutable even though v1 is now retired for policy-selection execution.

### Gate B v1 is retired for policy selection

The historical `v3-gate-b-preregister-v1` contract froze epoch `2026-09-13T13:45:00Z` through `2026-09-16T13:45:00Z` and required separate hash-bound `diagnosis` and `consumed_v2_final_holdout` exclusion manifests.

The exact 84-trade diagnosis identities were not durably frozen before that epoch began. Repository and host-side recovery found the diagnosis methodology but not a valid exact identity list, cohort hash, or replay-safe snapshot. Do not reconstruct the cohort from current database state, later settlements, outcomes, inferred timestamps, approximate date windows, or a synthetic/empty manifest.

Therefore `v3-gate-b-preregister-v1` is **non-executable for model/policy selection**. Do not run its readiness or planning path. Data from its `2026-09-13T13:45:00Z` to `2026-09-16T13:45:00Z` epoch may be retained only as immutable engineering, source-availability, leakage, and coverage evidence.

The complete consumed-V2 final-holdout contamination boundary has separately been recovered as 48 unique historical condition IDs with zero overlap across the two consumed plans. Those identities remain permanently non-reusable historical contamination evidence.

### Approved Gate B successor

The approved successor design is `docs/superpowers/specs/2026-09-14-phase-14-v3-gate-b-successor-preregistration.md`, with durable decision evidence at `docs/evidence/phase-14-v3-gate-b-successor-preregistration-20260914.json`.

Its exact identity is:

```text
research_plan_version = v3-gate-b-preregister-v2
dataset_version       = supervised-core-v3-btc-native-v1
feature_version       = core-v3-btc-native
label_version         = official-outcome-v1
epoch_start           = 2026-09-16T13:45:00Z
epoch_end             = 2026-09-19T13:45:00Z
```

The successor preserves the v1 predictor set, model candidates, calibration/economic search rules, 24h/6h/6h/6h train-validation-test-step geometry, exactly five ordinary folds, one-market embargo, and final 12h reserved holdout.

Its contamination boundary is structural: only markets with `market_start_at >= epoch_start AND market_start_at < epoch_end` may participate. Every pre-epoch market is ineligible for successor train/validation/test/final-holdout membership. Historical diagnosis and consumed-V2 identities remain evidence but are not successor runtime selection inputs.

Readiness remains outcome-blind and planning remains feature-only/read-only. The successor preregistration authorizes neither model fitting nor final-holdout access.

### Successor runtime implementation checkpoint

The repository implementation of `v3-gate-b-preregister-v2` is complete at runtime checkpoint `659d9524fe8bfeba182b7cf7c8d9b664280f7562`. Exact-head CI run `34841954823` passed the full suite, deployment-asset validation, research-mode health check, and dashboard checks. The runtime removes historical exclusion manifests from successor readiness/plan APIs and CLI, while preserving the structural half-open epoch boundary, outcome-blind readiness, feature-only planning, PostgreSQL read-only transactions, no-clobber plan output, and the frozen search/economic contract.

This checkpoint is repository-only. It did not run readiness or planning against production, inspect successor outcomes/labels, fit models, access/evaluate the final holdout, activate a model/paper policy, mutate production, enable live trading, or change money limits.

## Immediate next task

The isolated V4 forward collector is now **production PASS and active** in research mode.

Initial accepted production cycle:

```text
feature_version          = core-v4-regime-aware
prospective_epoch_start  = 2026-09-20T12:40:53Z
candidate_head           = 36b02d0687194173ab5d3862d3b88c6c90607574
eligible_markets         = 9
feature_rows_inserted    = 36
bull_markets             = 2
bear_markets             = 0
sideways_mixed_markets   = 7
unknown_markets          = 0
future_cutoff_violations = 0
Polymarket predictor keys= 0
training_run             = false
automatic_promotion      = false
```

The collector runs once per minute from the isolated V4 runtime under `/var/lib/bp/runtime`. The deployed `/opt/bp` checkout was preserved and the recorder was not restarted.

The current task is now **prospective collection only**. Do not fit, calibrate, tune thresholds, construct/access a V4 final holdout, activate a model, or trade money.

Wait until enough fresh markets exist across bull, bear, and sideways/mixed conditions to support a meaningful regime comparison. Before any V4 model fitting, freeze a new preregistered Gate B plan on that fresh prospective cohort.
