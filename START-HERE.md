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

**23 September accelerated V3 audit result:** the production read-only audit on exact main `ceeec0bded4bb6ae60291ee8f5f60db214eceb98` passed every statistical V3 live-readiness row under the frozen rules. Durable evidence is `docs/evidence/phase-15-v3-accelerated-readiness-production-20260923.json`. Current paper evidence is 77 settled trades (48W/29L), +$687.612927164917 realized after-cost P&L, and a deterministic mean-P&L 95% interval of +$1.746626049710839 to +$18.521917124071315. The prospective calibration audit also passed its predeclared intercept/slope rule.

The only remaining Master blocker is execution-host geography. The user’s ordinary physical connection is unblocked (`NG/LA`), while the current US execution host remains blocked. The next authorized mutation is **only** `bash scripts/deploy/phase15_v3_canary_host_probe_cloudshell.sh`, which provisions a dedicated `e2-micro` in GCP `africa-south1-a` (Johannesburg), runs the official direct geoblock check, installs no trading software, reads no wallet material, and deletes itself automatically if blocked. It requires explicit billable-VM acknowledgement. Do not activate live trading yet.


**Same-day V3 canary readiness:** `phase15-v3-canary-readiness-v1` is now the only authorized statistical follow-up. Its acceptance rules are frozen before the new prospective calibration intercept/slope diagnostics are read. After the package merges green, run `bash scripts/deploy/phase15_v3_accelerated_readiness_cloudshell.sh` once from exact clean current `main`. The run is PostgreSQL read-only and cannot access wallet/signing material, construct an authenticated trading client, enable live trading, or change money limits.

A statistical PASS still cannot override geography. Before any live canary, independently require an unblocked official Polymarket geoblock result from the user's ordinary physical network with VPN/proxy disabled and an unblocked direct geoblock result from the eventual execution host. Do not use infrastructure to disguise a restricted physical location.


**23 September V3 live-gate reassessment:** the read-only production run is complete and preserved at `docs/evidence/phase-14-v3-live-gate-reassessment-production-20260923.json`. Frozen V3 now has 76 settled trades, 47 wins / 29 losses, +$682.252111761097 realized paper P&L, profit factor 6.262572772140266, max drawdown $27.974134608836, and a deterministic bootstrap 95% interval for mean realized P&L of +$1.6407501892525114 to +$18.945890261783955. `positive_after_cost_profitability=pass`, execution/reconciliation remains `pass`, and explicit user authorization is `pass`.

The Master live gate nevertheless remains **closed**. The direct official Polymarket geoblock request from the production VM returned `blocked=true`, `country=US`, `region=SC`, so `geographic_compliance_eligible=fail`. `sufficiently_large_live_paper_sample_with_uncertainty`, `calibration_acceptable`, and `walk_forward_results_stable_enough` remain `insufficient_evidence` under the canonical rules. Do not rerun the one-shot reassessment absent a separately versioned reason; do not bypass geographic restrictions; keep live trading disabled and real-money limits zero.


Frozen V3 paper activation remains a **historical production PASS**, and the current recorder/frozen-V3 runtime is **active after concurrent-partition-retirement rollout PASS** on deployed candidate `52b4355d6f077373b873f7a6f42bc37a20ddbc7b`. The maintenance timer is restored active.

The accepted frozen identity remains unchanged:

```text
model_sha256       = 124627e15cab3997b8abe54ec5237450d976ab5682953f45a1399a76b6dae0e7
prediction_version = v3-frozen-paper-v1
execution_version  = paper-execution-v3-frozen-v1
min_edge           = 0.075
real_money         = $0.00
```

The latest recovery PASS is `/var/lib/bp/evidence/phase14-recorder-v3-recovery-20260922T134508Z.json`. The subsequent rollout PASS is `/var/lib/bp/evidence/phase14-concurrent-partition-retirement-rollout-20260922T140747Z.json`: maintenance retired exactly one eligible hourly partition, recorder PID `4016465` stayed stable with zero restarts, frozen predictor PID `4016471` and frozen execution PID `4016476` stayed active, post-maintenance storage health was `ok` with retention lag `0.0h`, and no detached-retirement leftovers remained.

The immediate operational sequence is:

1. **Collect the frozen V4 Gate B v1 future cohort.** Continue frozen-V3 paper observation and **V4 regime-aware feature collection** with the existing collector unchanged. This remains **prospective observation only**. Only V4 markets with `market_start_at >= 2026-09-23T00:00:00Z` and `< 2026-09-30T00:00:00Z` may enter future selection; earlier V4 rows are coverage/engineering evidence only.
2. **Do not run V4 readiness or planning early.** After `2026-09-30T00:00:00Z`, run only outcome-blind read-only readiness. If and only if it passes, write the feature-only no-clobber plan and stop. Labeled preparation, training, policy selection, and final-holdout access remain blocked.
3. **Preserve observation/storage evidence.** Continue using `bash scripts/deploy/phase14_observation_cloudshell.sh` only for read-only evidence collection. **Do not rerun the storage rollout.** Preserve the rollout PASS and monitor normal hourly maintenance.
4. **Keep all promotion/live boundaries closed.** V3 refit/tuning, V4 label access/model fitting/policy selection/final-holdout access, automatic promotion, Phase 15, live trading, geographic bypass, and nonzero real-money limits remain unauthorized.

The frozen V4 Gate B contract is `docs/superpowers/specs/2026-09-22-phase-14-v4-gate-b-preregistration.md`, with durable preregistration evidence at `docs/evidence/phase-14-v4-gate-b-preregistration-20260922.json`.

The observation PASS recorded 308 frozen-V3 predictions, 60 trade signals, 45 settled orders, and virtual cash of `472.362970092036` from the frozen `100.00` starting balance. It also recorded 373 V4 markets / 1,492 rows with bull, bear, sideways/mixed, and unknown regimes represented, zero future-cutoff violations, zero Polymarket predictor keys, zero regime-invariant violations, no training, no policy selection, and no automatic promotion. These are observation facts only, not tuning inputs or a promotion decision.

Do not refit V3, recalibrate it, change `min_edge=0.075`, alter paper sizing, tune from paper results, perform Gate B actions, automatically promote anything, enable a live-order path, enter Phase 15, bypass geographic restrictions, or change real-money limits.
