## Immediate next task

**Phase 15 ten-dollar canary — Master gate PASS, code not deployed:** the accelerated frozen-V3 statistical audit is PASS and the dedicated Johannesburg execution-host probe is also PASS (`blocked=false`, `ZA/GP`). The user's ordinary physical-network check remains independently unblocked (`NG/LA`). Durable host evidence is `docs/evidence/phase-15-v3-canary-execution-host-geoblock-20260923.json`.

The only authorized first-live contract is `phase15-v3-ten-dollar-canary-v1`: one live submission intent maximum, **$10 maximum total cost including the modeled fee**, $10 maximum concurrent exposure, $10 maximum daily loss, one consecutive loss, frozen `min_edge=0.075`, and worker exit immediately after the first remote submission intent. The existing BP host retains prediction identity, risk, duplicate protection, reconciliation, and ledger logic; authenticated Polymarket signing/submission is isolated to the directly unblocked Johannesburg execution host.

The repository remains non-spending by itself. Wallet/private-key material must not be committed, logged, pasted into chat, or embedded in repository deployment automation. Credential provisioning and the final real-money activation remain explicit operator boundaries. Frozen V3 paper observation and V4 Gate B collection continue unchanged.

**Historical checkpoints below:**

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
