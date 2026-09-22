# Phase 14 — V4 Gate B Prospective Preregistration v1

**Date:** 22 September 2026  
**Status:** frozen research-only preregistration; readiness/planning only  
**Mode:** RESEARCH only; live trading disabled; all real-money limits remain zero  
**Source main at decision:** `9e5c5cda7384740a4ffafd231694935da8ab1cc3`

## 1. Decision

Freeze the first comprehensive V4 Gate B contract before any V4 labels, outcomes,
P&L, calibration results, model fits, or policy-selection results are used.

The pre-epoch V4 rows already collected since `2026-09-20T12:40:53Z`, including
the 373-market / 1,492-row read-only observation recorded on 22 September, remain
engineering, source-availability, leakage, and regime-coverage evidence only.
They are structurally ineligible for V4 Gate B model or policy selection.

This preregistration authorizes only:

1. continued prospective `core-v4-regime-aware` feature collection;
2. outcome-blind readiness after the frozen epoch closes;
3. feature-only, read-only, exclusive-create/no-clobber planning if readiness
   passes.

It does not authorize labeled preparation, model fitting, calibration fitting,
economic-policy selection, final-holdout access, paper activation, automatic
promotion, Phase 15, live trading, geographic bypass, or nonzero money.

## 2. Frozen identity and future epoch

```text
research_plan_version = v4-gate-b-preregister-v1
dataset_version       = supervised-core-v4-regime-aware-v1
feature_version       = core-v4-regime-aware
label_version         = official-outcome-v1
horizon_seconds       = 300
feature_offsets       = 60, 120, 180, 240
```

The selection epoch is wholly future at decision time:

```text
2026-09-23T00:00:00Z <= market_start_at < 2026-09-30T00:00:00Z
```

Every market before `2026-09-23T00:00:00Z` is structurally excluded from V4
train, validation, ordinary test, and final-holdout membership.

The seven-day duration is a prospective design choice intended to provide more
opportunity for regime variation than V3's three-day successor epoch. It is not
selected from V4 outcome or P&L evidence.

## 3. Chronological geometry

The frozen walk-forward geometry is:

- training: 48h
- validation: 12h
- ordinary test: 12h
- step: 12h
- ordinary folds: exactly 7
- final untouched holdout: 24h
- embargo: one market on the earlier side of train/validation boundaries
- minimum markets: 480 / 120 / 120 / 240 for
  train / validation / ordinary test / final holdout

The minimum counts preserve the same approximate five-sixths coverage tolerance
used by the accepted V3 geometry while doubling each time window.

No random split is allowed. Markets are admitted only when their complete
five-minute interval is contained in the relevant chronological partition.

## 4. Feature-only readiness

Readiness may inspect only immutable `market_features` rows and source
provenance. It must not read labels, outcomes, prediction evaluations, paper
settlements, or P&L.

Readiness requires:

- the full epoch has closed;
- exactly the frozen 60/120/180/240-second offsets for every eligible market;
- zero future source-cutoff violations;
- zero Polymarket forecast-predictor keys;
- zero regime-invariant violations;
- current-state availability of at least 90% for Coinbase, Bybit spot, and
  Bybit linear;
- non-structural short-return availability of at least 90%;
- 5m/15m/60m regime-return availability of at least 75%;
- at least 120 distinct eligible markets represented in each known regime:
  bull, bear, and sideways/mixed.

The 75% long-lookback availability floor is frozen from pre-epoch feature-only
source-availability evidence, not from labels, outcomes, model metrics, or P&L.
Missing long-lookback inputs remain explicit; they are never zero-filled.

A market may appear in more than one regime coverage count if its deterministic
regime changes across the four feature offsets. The readiness count means that a
regime was genuinely observed in at least that many distinct markets, not that
regime memberships are mutually exclusive at market level.

If readiness fails, continue prospective collection only after a separately
versioned future preregistration. Do not weaken the frozen epoch, coverage,
regime-diversity, or chronology gates after seeing labels.

## 5. Frozen predictor set

V4 keeps the V3 short-horizon BTC-native family:

```text
coinbase_return_from_market_start
coinbase_return_30s
coinbase_return_60s
coinbase_return_120s
bybit_spot_return_from_market_start
bybit_spot_return_30s
bybit_spot_return_60s
bybit_spot_return_120s
bybit_linear_return_from_market_start
bybit_linear_return_30s
bybit_linear_return_60s
bybit_linear_return_120s
coinbase_bybit_spot_return_spread
coinbase_bybit_spot_direction_agree
spot_linear_direction_agree
bybit_linear_vs_spot_basis
bybit_linear_funding_rate
bybit_linear_open_interest
seconds_elapsed
seconds_remaining
fraction_elapsed
```

and adds the frozen V4 context:

```text
coinbase_return_5m
coinbase_return_15m
coinbase_return_60m
bybit_spot_return_5m
bybit_spot_return_15m
bybit_spot_return_60m
bybit_linear_return_5m
bybit_linear_return_15m
bybit_linear_return_60m
regime_5m_direction
regime_15m_direction
regime_60m_direction
regime_5m_venue_agreement
regime_15m_venue_agreement
regime_60m_venue_agreement
regime_trend_score
regime_bull
regime_bear
regime_sideways_mixed
```

Polymarket price/book data is not a forecast predictor. It remains only the
downstream executable price-to-beat after a forecast is produced.

## 6. Forecast candidate ladder

The frozen ordinary-selection model candidates are:

```text
training_prior
single_feature_btc_logistic
short_context_v4_logistic
full_v4_logistic
full_v4_xgboost
```

Interpretation:

- `training_prior`: no-predictor baseline;
- `single_feature_btc_logistic`: simple BTC-direction baseline using the
  market-start Coinbase return plus its explicit missingness;
- `short_context_v4_logistic`: multivariate logistic using only the frozen
  V3-style short-context predictors from V4 rows;
- `full_v4_logistic`: multivariate logistic using the complete frozen V4
  predictor set;
- `full_v4_xgboost`: nonlinear challenger using the same complete V4
  predictor set.

Primary model selection metric is mean ordinary-fold validation log loss.
Tie-breakers are mean validation Brier score, worst known-regime validation log
loss, then simpler model.

The nonlinear challenger may replace `full_v4_logistic` only if it is strictly
better on overall validation log loss and Brier score and is not worse on both
metrics in any evaluable known-regime slice.

No side-specific model, regime-specific model, side filter, or regime filter is
a candidate in v1.

## 7. Calibration

Calibration candidates are exactly:

```text
identity
platt
```

Platt must keep a nonnegative coefficient, improve overall validation log loss
and Brier score, and not be worse on both metrics in any evaluable known-regime
slice. Otherwise identity is retained.

Calibration is global. No side-specific or regime-specific calibrator may be
selected in v1.

## 8. Timing and economic policy

All feature offsets compete again:

```text
60, 120, 180, 240 seconds
```

The execution assumptions remain research assumptions:

```text
fee_rate        = 0.07
slippage_buffer = 0.01
max_book_age_s  = 10
```

The frozen minimum-edge candidates are:

```text
0, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15
```

plus explicit `no_trade`.

Economic eligibility requires:

- at least 16 validation trades in every ordinary fold;
- at least 6 of 7 validation folds with nonnegative after-cost P&L;
- positive aggregate validation after-cost P&L.

There is one global timing/calibration/edge policy. Regime-specific and
side-specific economic thresholds are forbidden in v1.

## 9. Mandatory reporting and insufficient slices

Every ordinary model/economic report must include:

- overall;
- bull;
- bear;
- sideways/mixed;
- unknown;
- Up;
- Down;
- regime-by-side.

A slice with fewer than 60 markets is explicitly
`insufficient_slice_evidence`. Sparse slices remain visible but may not be used
to invent a side/regime filter or threshold.

Probability reporting must include accuracy, balanced accuracy, log loss, Brier
score, and calibration error where defined.

Economic reporting must include trade count, coverage, gross and after-cost
P&L, average win/loss, profit factor, maximum drawdown, losing streak, spread,
and assumed costs. The full edge/coverage frontier must be retained.

Execution availability must be reported separately from forecast quality:
missing/stale selected books, eligible executable signals, simulated fill/expiry,
and other execution failures may not be reclassified as forecast errors.

## 10. Holdout boundary

Feature-only planning reserves the final 24 hours and hashes exact membership
without reading labels.

Ordinary labeled preparation/model selection is a later explicit boundary.
After ordinary selection is permanently frozen, final-holdout label access and
evaluation are a separate one-shot explicit authorization boundary.

The final holdout may never be reused to choose another model, timing offset,
calibrator, edge threshold, regime rule, side rule, or replacement holdout.

## 11. Runtime contract

Repository runtime exposes only:

```text
python -m bp_engine.v4_research.cli readiness --as-of <timestamp>
python -m bp_engine.v4_research.cli plan --as-of <timestamp> --output <plan.json>
```

Both database operations run read-only. Planning is exclusive-create/no-clobber.
The plan hash binds the complete preregistered search contract, readiness hash,
feature manifest, chronological folds, and final reserve.

No V4 `prepare`, training, selection, or holdout command is authorized by this
preregistration.
