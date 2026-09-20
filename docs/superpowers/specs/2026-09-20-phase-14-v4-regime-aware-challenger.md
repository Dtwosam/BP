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

## 5. Anti-overfit boundary

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

## 6. Mandatory evaluation

Every V4 probability evaluation must report overall metrics and separate metrics for bull, bear, sideways/mixed, and unknown regimes.

At minimum each regime report includes market count, Up/Down outcome counts, Up/Down prediction counts, accuracy, balanced accuracy, log loss, Brier score, and calibration error.

Any future economic evaluation must additionally report by regime and trade side: trade count and coverage, gross and after-cost P&L, average win/loss, maximum drawdown, losing streak, spread, and assumed execution costs.

Overall profitability is not sufficient if a regime-specific failure is being hidden by another market condition.

## 7. Current implementation boundary

The repository implementation may add immutable V4 feature models, BTC state source reads, 5m/15m/60m regime calculations, deterministic regime one-hot fields, leakage and immutability tests, and regime-sliced probability reporting.

This step does not authorize production database writes, production feature materialization, deployment or service restart, model training, threshold search, final-holdout construction or access, paper activation, automatic promotion, live trading, or nonzero money limits.

## 8. Next controlled step

After repository CI passes, the next controlled boundary is production V4 feature collection/materialization in RESEARCH mode.

That production mutation requires separate authorization. Once sufficient prospective V4 coverage exists, freeze a new V4 Gate B preregistration before reading selection outcomes.
