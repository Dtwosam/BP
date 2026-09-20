# Phase 14 — V4 Regime-Aware BTC Challenger

**Date:** 20 September 2026  
**Status:** repository research implementation; no production rollout, training, paper activation, or live trading authorized  
**Phase:** 14 — live readiness research  
**Mode:** RESEARCH only

## 1. Purpose

V3 produced positive one-shot holdout economics, but its holdout trades were asymmetric by side: UP trades were materially stronger than DOWN trades. The holdout interval may also have reflected a bullish BTC regime.

V4 tests whether BTC-native forecasting can remain useful across different market conditions rather than depending on one directional environment.

V4 is a separate immutable challenger. It does not modify, refit, or reinterpret the frozen V3 plan, selection, model, or consumed final holdout.

## 2. Immutable identity

```text
feature_version  = core-v4-regime-aware
dataset_version  = supervised-core-v4-regime-aware-v1
label_version    = official-outcome-v1
horizon_seconds  = 300
feature_offsets  = 60, 120, 180, 240
```

The future V4 Gate B planning epoch must be prospective and frozen only after V4 feature collection is accepted. No market from before that future frozen boundary may enter final V4 model/policy selection.

## 3. Forecast inputs

V4 preserves the BTC-native V3 short-horizon feature family and adds longer market context from the same timestamp-coherent compact BTC state.

For Coinbase spot, Bybit spot, and Bybit linear, add:

```text
return_5m
return_15m
return_60m
```

These are measured from exact as-of observations no later than feature time. The existing ten-second source freshness rule applies independently at each requested anchor time.

Polymarket price/book data remains excluded from the forecast feature vector. It may be used only later as the executable price-to-beat.

## 4. Regime definition

Regime classification is deterministic and contains no threshold learned from the consumed V3 holdout.

For each of 5m, 15m, and 60m:

1. take the sign of each available Coinbase spot, Bybit spot, and Bybit linear return;
2. require at least two non-zero venue votes;
3. use the majority sign as the horizon direction.

Then:

```text
bull           = all three horizon directions are positive
bear           = all three horizon directions are negative
sideways_mixed = all horizon directions are available but do not all agree
unknown        = one or more horizon directions cannot be established
```

V4 stores numeric one-hot regime flags plus per-horizon direction, venue agreement, and a trend score. This regime is context for modeling and mandatory reporting; it is not itself a trade instruction.

## 5. V3 weakness-remediation objectives

V4 is the full successor research program for the documented weaknesses exposed by V3. Regime robustness is one objective, not the entire V4 purpose.

The following weaknesses must remain explicitly in scope for the future V4 Gate B preregistration and ordinary selection:

1. **Regime dependence.** Test whether forecast quality and economics remain useful across bull, bear, and sideways/mixed conditions instead of depending on one directional environment.
2. **Trade-side asymmetry.** V3 final-holdout trades were materially stronger on UP than DOWN. V4 must report and validate probability quality and economics separately for UP and DOWN.
3. **Selected-model simplicity / feature underuse.** V3 ultimately selected `single_feature_btc_logistic` even though richer BTC-native predictors existed. V4 must compare a simple baseline with multivariate BTC-native models and at least one preregistered nonlinear challenger using only the frozen V4 predictor set.
4. **Calibration robustness.** Calibration must be evaluated overall, by regime, and by predicted/traded side. A model with acceptable aggregate calibration but materially poor regime/side calibration cannot hide that weakness in the overall number.
5. **Timing dependence.** V3 selected the 240-second offset. V4 must allow the frozen 60/120/180/240 decision offsets to compete again on fresh prospective data rather than assuming 240 seconds remains best.
6. **Trade-quality versus coverage.** V3's frozen holdout produced relatively few trades. V4 must report the coverage/economic-quality frontier so a higher trade count is never treated as an improvement unless probability quality and after-cost economics remain acceptable.
7. **Loss/drawdown robustness.** V4 economic reporting must include losses, average win/loss, drawdown, losing streak, and profit factor overall and by regime/side. Positive aggregate P&L alone is insufficient.
8. **Execution availability.** Freshness/missing-book conditions and simulated fill/expiry behavior must be reported separately from forecast quality so execution problems are not mistaken for model problems.

These objectives are hypotheses motivated by V3 evidence only. The consumed V3 final holdout must not supply V4 numeric thresholds, side filters, model hyperparameters, regime-specific rules, calibration parameters, or acceptance cutoffs.

The future V4 Gate B preregistration must freeze the candidate model ladder, calibration ladder, timing candidates, economic-policy candidates, side/regime reporting contract, chronology, selection rules, and final untouched holdout before any prospective V4 labels are used for selection.

## 6. Anti-overfit boundary

The V3 final holdout is permanently consumed.

Its results may support the research hypothesis that market regime and side asymmetry deserve investigation. They must not be used to:

- select a V4 edge threshold;
- choose a V4 confidence cutoff;
- disable DOWN trades;
- choose a regime-specific trade rule;
- fit or calibrate a V4 model;
- select V4 hyperparameters;
- claim V4 out-of-sample performance.

Any future V4 selection and final holdout must use a new prospective cohort frozen after this design and feature implementation.

## 7. Mandatory evaluation

Every V4 probability evaluation must report overall metrics and separate metrics for bull, bear, sideways/mixed, and unknown regimes.

At minimum each regime report includes market count, Up/Down outcome counts, Up/Down prediction counts, accuracy, balanced accuracy, log loss, Brier score, and calibration error.

Any future economic evaluation must additionally report by regime and trade side: trade count and coverage, gross and after-cost P&L, average win/loss, maximum drawdown, losing streak, spread, and assumed execution costs.

Overall profitability is not sufficient if a regime-specific failure is being hidden by another market condition.

## 8. Current implementation boundary

The immutable V4 feature implementation and isolated prospective production collector are active in RESEARCH mode. The collector materializes the frozen V4 predictor set and regime context only; it does not fit models, read a V4 final holdout, select a policy, activate a V4 model, or trade money.

The current collector must remain unchanged while the prospective cohort accumulates unless a separately versioned successor feature contract is explicitly designed before collecting a new cohort. Do not silently add predictors to the active V4 feature version.

## 9. Next controlled step

Continue prospective V4 collection until the cohort has enough regime and side diversity for meaningful ordinary model selection.

Before reading V4 labels for selection, freeze a new comprehensive V4 Gate B preregistration that covers every weakness-remediation objective in Section 5. That preregistration must define the model/calibration/timing/policy candidate ladders, chronological folds, coverage and risk reporting, side/regime slices, and an untouched final holdout.

Model fitting, threshold selection, final-holdout access, V4 paper activation, automatic promotion, live trading, and nonzero money remain separate controlled boundaries.
