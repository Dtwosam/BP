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

**5 October 2026 superseding status:** the latest continuous fast-live v2 authorization `phase15-v3-fast-live-auto-continuous-v2-12h-9824a0b1-20261001T201044Z` was activated in production, expired at `2026-10-02T08:10:44Z`, and is now inactive/fail-closed. Read-only reconciliation found zero publications, zero network submission attempts, and zero real orders bound to that authorization; `bp-phase15-fast-live-source.service` and the Johannesburg receiver are inactive, the executor kill switch is engaged, and there are no unresolved result or settlement records. The one preserved historical fast-live attempt/fill belongs to the earlier 30 September authorization and is already recorded by `docs/evidence/phase15_v3_fast_live_completed_session_cleanup_20261001.json`. Durable reconciliation for the expired October session is `docs/evidence/phase15-v3-fast-live-expired-session-readonly-reconciliation-20261005.json`.

**5 October cleanup PASS:** the exact expired-zero-attempt cleanup ran from main `d0b1412023849ede8054beb9650d713037573730` using helper blob `0046d6a3636bcd3ffb917813cfc29de38404ee74`. It reconfirmed zero publications, zero network attempts, zero execution results, zero real orders, a clean ZA account with zero open orders, preserved the Johannesburg kill switch, removed the session Pub/Sub/runtime material, and verified that material absent afterward. Both fast-live services remain inactive. The one-shot cleanup authorization is consumed. Durable reviewed evidence is `docs/evidence/phase15-v3-fast-live-expired-zero-attempt-cleanup-20261005.json`.

**No new live session is currently authorized, and V3 live is not the active objective.** The expired cleanup must not be rerun. The active strategy-development path is now the already-collected V4 Gate B v2 cohort: 1,662 frozen-plan markets, a frozen `full_v4_xgboost` / 240-second / identity / `min_edge=0.05` global policy, and an untouched 288-market final holdout. Repository code now provides a hash-bound one-shot holdout evaluator plus a guarded Cloud Shell runner, but the holdout has not been accessed or evaluated. After exact-head validation/merge, holdout access remains a fresh explicit one-shot authorization boundary. V4 paper activation is a later separate zero-money boundary; live trading remains disabled.


**Historical context below is superseded by the 5 October status above.** Phase 15 previously authorized an additional frozen-V3 live canary through the private Telegram approval path; those earlier one-shot/session authorizations are consumed or expired and do not authorize a new live session now. The first canary remains officially reconciled at zero fill and its submission attempt remains consumed.

The private Telegram transport is now **production-active**. Exact activation from main `469049a9f6361f9c656e3d176029b10db7359689` with repaired helper blob `83157c6c04b9a996bab51012b4bda4dd31062320` passed after listener, stage, Pub/Sub, and activation-readiness checks all returned PASS. The existing topic/subscription were reused; publisher and all four Johannesburg transport services are active; global and Phase-15 `LIVE_TRADING_ENABLED` remain false; the executor is safe-idle; and no real order was submitted. The activation authorization is consumed, so activation must not be rerun without fresh explicit authorization.

There is **no pending second-canary intent**. Prepare-only watcher run `phase15-prepare-watch-20260926T144337Z-c2034bee` is no longer active: it stopped safely before preparing a candidate because the generic canary preparation layer still counted the reconciled first canary against the original lifetime one-attempt limit. The stop did not consume the second-canary network attempt and did not perform Telegram `APPROVE`, executor arm/invocation, or order submission. A repository fix now keeps legacy/default limits at one while letting only the source-truth-gated second-canary watcher recognize the reconciled first canary plus one authorized second slot. The corrected prepare-only watcher restart completed from exact main `f3de8c008d6a50584dc71a172ca4dcb4275f73d8` with all four authorized corrected blobs matching. Read-only status later showed run `phase15-prepare-watch-20260926T155010Z-f3de8c00` expired safely at `2026-09-26T17:50:48.084941Z` after its 7200-second window with reason `no_eligible_v3_trade_within_wait_window`. The service is inactive, no arm was attempted, no real order was submitted, and the second-canary submission attempt remains unconsumed. That one-shot authorization has now been exercised successfully. New run `phase15-prepare-watch-20260926T195409Z-271b35ff` started PASS from exact main `271b35fffdb3ccff1490e213672dfa98b7583d75`; the service is active on `bp-recorder`, `NO_REAL_ORDER_SUBMITTED=true`, `ARM_AUTOMATED=false`, and `SUBMISSION_AUTOMATED=false`. The restart authorization is consumed.

A later read-only audit found **three fresh frozen-V3 paper trades** during that watcher window. All three were `trade=true` and `executable=true`, but each live-canary risk decision failed only with `reconciliation_blocked`. The missing first-canary post-submission ledger row has now been repaired in production from exact main `38b6081621eab59aecafcdb0628e1d68e9497827` using helper blob `b21456d222f8256b381d43d70c1373bbbee6d78d`. The helper freshly re-verified official zero fill, then persisted reconciliation `live-reconciliation-81489372163985723f74f2003c7ef1d7` with `unresolved_count=0` and `critical_count=0`. No Telegram `APPROVE`, executor arm/invocation, or order submission occurred, and the repair authorization is consumed.

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

1. Keep the October V3 fast-live session **expired, inactive, and fully cleaned**. Do not restart it, rerun its cleanup, or generate a replacement live authorization while V4 paper validation is the active objective.
2. Treat V4 Gate B collection and ordinary selection as complete: the frozen v2 plan contains 1,662 markets; the selected global policy is `full_v4_xgboost` at 240 seconds, identity calibration, and `min_edge=0.05`; the final 288-market holdout remains untouched.
3. Merge and exact-head validate the repository-only V4 final-holdout evaluator and guarded runner. This build step must not read holdout labels, refit/tune/reselect, activate paper, mutate production, enable live trading, or use real money.
4. After merge, any access to the 288 holdout labels requires a **fresh explicit one-shot authorization** bound to the then-current main and exact frozen plan/selection/model hashes. The guarded runner must write the durable attempt marker before label access, so a failed attempt cannot be silently retried.
5. After the one-shot holdout result is durably recorded and reviewed, V4 paper activation is a separate decision. Paper mode must remain zero real money with live trading disabled.
6. Continue the V4 feature collector only as background future observation. No additional seven-day collection is required for the already-frozen Gate B v2 run.
7. Preserve the Phase 14 historical V3 paper and storage evidence unchanged; do not use V3 or consumed holdouts to retune the frozen V4 policy.
