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

Phase 15 still authorizes **exactly one additional frozen-V3 live canary through the private Telegram approval path**. The first canary remains officially reconciled at zero fill and its submission attempt remains consumed.

The private Telegram transport is now **production-active**. Exact activation from main `469049a9f6361f9c656e3d176029b10db7359689` with repaired helper blob `83157c6c04b9a996bab51012b4bda4dd31062320` passed after listener, stage, Pub/Sub, and activation-readiness checks all returned PASS. The existing topic/subscription were reused; publisher and all four Johannesburg transport services are active; global and Phase-15 `LIVE_TRADING_ENABLED` remain false; the executor is safe-idle; and no real order was submitted. The activation authorization is consumed, so activation must not be rerun without fresh explicit authorization.

There is **no pending second-canary intent**. Prepare-only watcher run `phase15-prepare-watch-20260926T144337Z-c2034bee` is no longer active: it stopped safely before preparing a candidate because the generic canary preparation layer still counted the reconciled first canary against the original lifetime one-attempt limit. The stop did not consume the second-canary network attempt and did not perform Telegram `APPROVE`, executor arm/invocation, or order submission. A repository fix now keeps legacy/default limits at one while letting only the source-truth-gated second-canary watcher recognize the reconciled first canary plus one authorized second slot. The corrected prepare-only watcher restart is now complete. Exact main `f3de8c008d6a50584dc71a172ca4dcb4275f73d8` and all four authorized corrected blobs matched, and run `phase15-prepare-watch-20260926T155010Z-f3de8c00` is active on `bp-recorder`. The watcher remains research/zero-money with no arm or submission automation. The restart authorization is consumed; Telegram `APPROVE`, executor arm/invocation, and order submission remain separate boundaries.

The second canary preserves the frozen V3 **$5 target notional** under the existing hard **$10 per-market ceiling**. Hard limits remain $10 max trade, $10 total exposure, $10 daily loss, one consecutive loss, one accepted order, one network submission attempt for the authorization, and a 2-second order TTL/cancel attempt. No stake growth, V3 tuning, V4 mutation, or broad autonomous live rollout is authorized.

The dedicated execution host remains `bp-v3-canary-exec` in GCP `africa-south1-a` (Johannesburg). Wallet/signing material is permitted only on that host, never on the existing US BP host, in Git, or in chat.

The active path is now: fresh NEW frozen-V3 candidate → prepare-only watcher → private Telegram notification → exact Telegram APPROVE → signed source-truth/dispatch verification → authenticated Pub/Sub transport → one-shot Johannesburg claim → exact executor handoff verification → one executor invocation → stop and officially reconcile before any third live action. Global `LIVE_TRADING_ENABLED` remains false so unrelated execution paths stay closed.

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

1. Keep corrected run `phase15-prepare-watch-20260926T155010Z-f3de8c00` active; do not restart it again.
2. Use only the existing read-only prepare-watch status/follow helpers to observe that exact run until one fresh NEW frozen-V3 $5 candidate is prepared or the run terminates.
3. If a fresh candidate appears, review the exact intent ID, prediction ID, paper order ID, side, target, limit price, requested shares, remaining market window, and expiry.
4. Stop at the Telegram boundary. Do not send Telegram `APPROVE`, arm/invoke the executor, or submit an order based on this watcher-start authorization.
5. If the watcher terminates without a candidate, record that terminal result before any later restart decision.
6. Continue frozen-V3 paper observation and V4 Gate B collection unchanged.


**Preserved Phase 14 historical context:** frozen V3 paper activation remains a **historical production PASS** and the recorder/frozen-V3 runtime remains **active after concurrent-partition-retirement rollout PASS**. The frozen identities remain model `124627e15cab3997b8abe54ec5237450d976ab5682953f45a1399a76b6dae0e7`, prediction `v3-frozen-paper-v1`, execution `paper-execution-v3-frozen-v1`, and `min_edge=0.075`. That paper program used `real_money         = $0.00` and remains **prospective observation only** while the separately bounded Phase 15 canary is evaluated.

The **frozen V4 Gate B v1 future cohort** and **V4 regime-aware** feature collection continue unchanged. **Do not rerun the storage rollout.** The historical Phase 14 instruction to **keep all promotion/live boundaries closed** remains the governing boundary for every path except the separately authorized one-attempt Phase 15 canary. The historical read-only observation helper remains `bash scripts/deploy/phase14_observation_cloudshell.sh`; using it does not authorize any live-order action.

