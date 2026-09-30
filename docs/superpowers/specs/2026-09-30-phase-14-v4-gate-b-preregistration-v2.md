# Phase 14 — V4 Gate B Prospective Preregistration v2

**Date:** 30 September 2026  
**Status:** frozen outcome-blind research-only preregistration; readiness/planning only  
**Mode:** RESEARCH only; live trading disabled; all real-money limits remain zero  
**Source main at decision:** `0743841bbc93c72b024b5f853a721d762eac60c9`

## 1. Why v2 exists

V1 remains immutable historical source truth. After its epoch closed, the authorized
feature-only readiness check passed, but feature-only plan construction failed before
any label, outcome, P&L, model fit, calibration fit, or policy-selection result was read.

The failure was structural: the 12-hour interval
`2026-09-25T12:00:00Z <= market_start_at < 2026-09-26T00:00:00Z`
contained only 78 complete markets. V1 fold 0 therefore had 78 test markets versus
the frozen minimum 120, and fold 1 had 77 validation markets after the one-market
embargo versus the frozen minimum 120. No v1 plan artifact was created.

Because this diagnosis used only immutable feature identity, timestamps, offsets, and
counts, an outcome-blind separately versioned preregistration remains valid.

## 2. V2 geometry

V2 uses the unchanged collected V4 feature rows and preserves the final holdout:

```text
research_plan_version = v4-gate-b-preregister-v2
dataset_version       = supervised-core-v4-regime-aware-v1
feature_version       = core-v4-regime-aware
label_version         = official-outcome-v1
horizon_seconds       = 300
feature_offsets       = 60, 120, 180, 240
selection_epoch       = [2026-09-24T00:00:00Z, 2026-09-30T00:00:00Z)
training              = 48h
validation            = 12h
ordinary test         = 12h
step                   = 12h
ordinary folds         = 5
final holdout          = final 24h, unchanged
embargo                = one market on earlier side of train/validation boundaries
minimum markets        = 480 / 120 / 120 / 240
```

The discarded v1-only early period is excluded for structural feature availability,
not for any observed outcome or economic result.

## 3. Preserved search contract

V2 preserves the complete v1 candidate ladder and assumptions:

- `training_prior`
- `single_feature_btc_logistic`
- `short_context_v4_logistic`
- `full_v4_logistic`
- `full_v4_xgboost`
- calibration candidates `identity` and `platt`
- timing candidates 60, 120, 180, and 240 seconds
- fee rate 0.07 and slippage buffer 0.01
- minimum-edge grid 0, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15
- explicit `no_trade`
- the same overall/regime/side/regime-by-side reporting requirements
- the same execution-availability, drawdown, losing-streak, profit-factor, and
  coverage-frontier requirements

Side-specific and regime-specific policies remain forbidden.

## 4. Economic consistency rule

V1 required at least 6 of 7 ordinary validation folds to have non-negative
after-cost P&L. With five v2 folds, v2 requires **5 of 5** non-negative validation
folds. This is deliberately not a relaxation: it increases the required fraction
from 85.7% to 100%.

Positive aggregate validation after-cost P&L and at least 16 validation trades in
every ordinary fold remain required.

## 5. Readiness hardening

V2 readiness must verify the exact chronological partition counts that the plan
builder will require. A readiness result may not be `ready=true` if any ordinary
train/validation/test partition or the final train/validation/holdout partition is
below its frozen minimum.

This check remains feature-only and outcome-blind.

## 6. Authorization boundary

This preregistration authorizes only outcome-blind readiness and feature-only,
exclusive-create/no-clobber plan construction after readiness passes.

It does **not** authorize labeled preparation, model fitting, calibration fitting,
economic-policy selection, final-holdout access, paper activation, automatic
promotion, Phase 15 changes, live trading changes, or nonzero-money changes.
