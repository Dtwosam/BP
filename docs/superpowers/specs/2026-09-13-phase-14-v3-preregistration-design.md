# Phase 14 — V3 Preregistration and Evaluation Design

**Date:** 13 September 2026  
**Status:** approved research design; no labeled-model training, policy selection, final-holdout access, model activation, paper activation, or live trading authorized by this document  
**Phase:** 14 — BTC-first V3 research correction  
**Mode:** RESEARCH only; live trading disabled; all real-money limits remain zero  
**Base main:** `8b2d983ec75692de7ed39043c4a0e19dcf691942`

## 1. Purpose

This document preregisters the first labeled research/evaluation contract for the BTC-first V3 challenger before labels are joined for model selection and before any V3 final holdout is identified through outcomes.

The objective is to answer two separate questions without contaminating them:

1. can BTC-native information predict the official 5-minute `official-outcome-v1` target out of sample?;
2. after calibration, does that independent probability create stable positive after-cost economic edge against the contemporaneous Polymarket executable book?

Gate A has already established the repository implementation for immutable `core-v3-btc-native` features. A separately authorized production Gate A materialization then produced 68 feature rows for 17 completed 5-minute markets from `2026-09-13T13:45:00Z` through `2026-09-13T15:10:00Z`, with zero future-cutoff violations and zero Polymarket predictor keys. The complete sanitized evidence is preserved at `docs/evidence/phase-14-v3-gate-a-production-20260913.json` on the V3 evidence branch, with coverage-input SHA-256 `32c283a7769681ebe5b2e0d1fe255ad6c38aa5b0301303f8fe86f4e7b2278ffb`.

Gate A acceptance is a data/provenance/causality acceptance only. It is not evidence of predictive performance or profitability.

## 2. Frozen research identity

The first V3 Gate B research identity is:

```text
research_plan_version = v3-gate-b-preregister-v1
dataset_version       = supervised-core-v3-btc-native-v1
feature_version       = core-v3-btc-native
label_version         = official-outcome-v1
horizon_seconds       = 300
feature_offsets       = 60, 120, 180, 240
```

The target remains the canonical official resolved Polymarket outcome. V1 and V2 features, predictions, paper ledgers, Gate B evidence, adaptive-readiness evidence, and consumed holdouts remain immutable.

## 3. Prospective evidence epoch

The first V3 prospective evidence epoch is frozen before labeled model research as:

```text
start = 2026-09-13T13:45:00Z
end   = 2026-09-16T13:45:00Z
```

This is exactly 72 hours beginning at the clean post-merge V3 main boundary. At uninterrupted five-minute cadence it can contain at most 864 markets and 3,456 V3 feature rows.

The first 17 Gate A production markets are ordinary members of this frozen prospective epoch. They are not a special tuning or acceptance subset and must not be used to choose predictors, model hyperparameters, calibration rules, timing, or economic thresholds.

The epoch end is fixed. It must not be extended or shortened after labels are inspected in order to improve model results.

## 4. Outcome-blind coverage acceptance

Before any official labels are joined for V3 research, the full prospective epoch must pass an outcome-blind coverage check.

Required conditions:

- zero future-cutoff violations;
- zero Polymarket forecast predictor keys;
- all four feature offsets are present for each eligible market;
- current-state availability is at least 90% independently for Coinbase spot, Bybit spot, and Bybit linear;
- non-structurally-missing BTC return fields have at least 90% availability;
- feature and input fingerprints remain immutable under identical reruns;
- no policy is selected;
- no model is trained;
- no automatic promotion occurs.

The `*_return_120s` feature at the 60-second offset is structurally unavailable because `T-120s` precedes market start. This is expected missingness, not a coverage defect, and must remain explicitly missing rather than borrowing data from the previous market.

If the full prospective epoch does not satisfy these requirements, Gate B is `insufficient_evidence`. Do not weaken the thresholds or change missingness semantics after labels are observed.

## 5. Contamination exclusions

### 5.1 Diagnosis cohort

The 84 settled paper trades used on 13 September 2026 to discover the old Polymarket-price/current-book anti-edge failure are contaminated for V3 policy selection.

Before labeled V3 dataset construction, store their sorted condition IDs and a canonical SHA-256 digest. Those conditions may be used only for historical regression/diagnosis of the old failure mechanism. They are forbidden from:

- validation;
- ordinary test;
- final holdout;
- model selection;
- calibration selection;
- timing selection;
- economic threshold selection;
- prospective-performance claims.

The first V3 prospective epoch begins after this diagnosis period, but the explicit exclusion remains mandatory to protect against future interval/config changes.

### 5.2 Consumed V2 final holdout

Every condition ID belonging to the consumed V2 final holdout must be stored as a separate sorted exclusion set with its own canonical SHA-256 digest.

Those conditions are permanently forbidden from V3 validation, ordinary test, final holdout, model/policy selection, or future V3 claims. The consumed V2 holdout must never be reused.

## 6. Frozen predictor contract

The first V3 forecast predictor set remains exactly the BTC-native contract already defined by Gate A:

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

No new technical indicators, realized-volatility family, order-flow family, acceleration/reversal family, or large feature search may be added during the first V3 Gate B run.

Polymarket price/book values are excluded from the forecast vector. They enter only the later economic evaluation layer as contemporaneous executable prices and liquidity/freshness checks.

## 7. Dataset and preprocessing contract

V3 must reuse the established supervised-dataset discipline unless an explicit incompatibility is demonstrated:

- join immutable `core-v3-btc-native` rows to immutable `official-outcome-v1` labels only after feature construction and prospective-epoch coverage acceptance;
- all rows for one `condition_id` stay in the same chronological market partition;
- random feature-row shuffle is forbidden;
- imputation/scaling statistics are fit from training partitions only;
- equal-market weighting remains the default so four offsets from one market do not count as four independent markets;
- missingness remains explicit through the existing dataset missing-flag mechanism;
- dataset and split identities are deterministically hashed;
- contamination exclusions are applied before any validation/test/final-holdout partition is created.

## 8. Walk-forward evaluation geometry

Reuse the existing Phase 8 duration-based chronological whole-market fold machinery with this new frozen geometry:

```text
train_duration         = 24 hours
validation_duration    =  6 hours
test_duration          =  6 hours
step_duration          =  6 hours
final_holdout_duration = 12 hours
embargo_markets        = 1
```

For a complete 72-hour prospective epoch, this should yield five ordinary walk-forward folds followed by a 12-hour untouched final holdout.

Minimum market counts are frozen as:

```text
min_train_markets      = 240
min_validation_markets =  60
min_test_markets       =  60
min_final_holdout      = 120
```

These are minimum usable counts, not targets to be relaxed. If chronology and exclusions cannot satisfy them, the Gate B result is `insufficient_evidence`.

Ordinary test partitions are evaluation-only. They must never feed back into model, calibration, feature-offset, or edge-threshold selection.

## 9. Frozen forecast candidates

Exactly five forecast candidates are permitted:

1. training prior — class-prior baseline only;
2. Coinbase momentum-sign diagnostic — direction of `coinbase_return_from_market_start`, reported as directional accuracy/coverage and never treated as a calibrated probability model;
3. single-feature BTC logistic — regularized logistic using only `coinbase_return_from_market_start` plus required missingness handling;
4. full V3 logistic — regularized logistic over the frozen V3 predictor contract;
5. full V3 XGBoost challenger.

No neural network, feature-search pipeline, retrospective gap veto, diagnosis-derived momentum threshold, or ad-hoc model is permitted in this first preregistered run.

## 10. Forecast model selection

Primary validation metric:

```text
log loss
```

Tie-break sequence:

1. Brier score;
2. calibration quality;
3. simpler model.

Directional accuracy is reported but is not the optimization metric.

The XGBoost challenger may replace the full logistic only when its aggregated validation log loss and aggregated validation Brier score are both strictly better than the full logistic under the same folds and coverage rules. Otherwise the simpler full logistic remains preferred.

The training-prior and Coinbase-momentum baselines remain reportable regardless of champion selection.

## 11. Calibration contract

Calibration candidates are frozen to:

```text
identity
Platt
```

Calibration is fit without ordinary-test or final-holdout information. Preserve the existing project rule: Platt is eligible only when it improves both validation log loss and validation Brier score relative to identity. Otherwise identity remains selected.

Calibration must never reverse probability ordering through an invalid negative Platt coefficient; preserve the existing fail-closed calibration constraints.

## 12. Feature-offset selection

All four V3 decision offsets remain candidates:

```text
60
120
180
240
```

Offset selection uses validation evidence only after the forecast/calibration contract is applied. Ordinary-test and final-holdout performance may not alter the selected offset.

The 84-trade diagnosis results may not be used to prefer later/earlier offsets.

## 13. Economic evaluation contract

Economic evaluation occurs only after V3 produces an out-of-sample calibrated `P(Up)`.

For Up:

```text
side_probability = P(Up)
raw_edge          = side_probability - up_best_ask
```

For Down:

```text
side_probability = 1 - P(Up)
raw_edge          = side_probability - down_best_ask
```

Then:

```text
cost_adjusted_edge = raw_edge - fee - slippage_buffer
```

The current selected-side executable book must be timestamp-coherent with the decision and no more than 10 seconds stale. Missing, stale, malformed, non-finite, or crossed-invalid selected-side book evidence fails closed to no-trade.

The economic cost/grid contract is frozen to the already-established research values:

```text
fee_rate        = 0.07
slippage_buffer = 0.01

min_edge_grid =
0.000
0.010
0.020
0.030
0.050
0.075
0.100
0.150
+ explicit no_trade
```

No 30% gap cutoff or momentum veto discovered from the diagnosis cohort may be introduced.

## 14. Economic policy eligibility

A non-`no_trade` economic candidate is eligible for selection only when all of the following hold on validation evidence:

- at least 8 validation trades in every ordinary fold in which the candidate is evaluated;
- aggregate validation after-cost P&L is strictly positive;
- at least 4 of 5 validation folds have non-negative after-cost P&L.

If no candidate satisfies these conditions, select `no_trade`.

Every candidate report must include at least:

```text
trade_count
coverage
realized_after_cost_pnl
mean_pnl_per_trade
fees
edge_band_breakdown
claimed_edge_vs_realized_result_relationship
```

The edge-band report is mandatory so recurrence of the old anti-edge pattern is visible rather than hidden by an aggregate result.

## 15. Final holdout contract

The final 12 hours of the frozen 72-hour V3 epoch form a new V3 final holdout after outcome-blind feature-only planning and contamination exclusions.

Feature-only condition IDs and a plan hash may be frozen without reading holdout labels. Until a separate one-shot explicit authorization is granted:

- final-holdout labels must not be read;
- final-holdout execution/economic outcomes must not be evaluated;
- final-holdout model metrics must not be computed;
- no summary containing final-holdout results may exist.

Access to the V3 final holdout requires a new explicit one-shot authorization after ordinary-fold model, calibration, offset, and economic policy selection are frozen.

Once final-holdout access begins, the holdout is permanently consumed regardless of success, failure, missing input, or interrupted evaluation. It may not be reused, repaired into a retest, cherry-picked, or redefined.

## 16. Gate B execution sequence

The approved sequence is:

```text
Gate A accepted
    -> commit this frozen preregistration
    -> continue prospective V3 feature collection through 2026-09-16T13:45:00Z
    -> run outcome-blind coverage/readiness check
    -> freeze feature-only fold/holdout plan and hashes
    -> join labels for non-final-holdout partitions only
    -> run five ordinary walk-forward development folds
    -> select/freeze forecast model, calibration, offset, and economic policy
    -> request separate explicit final-holdout authorization
    -> evaluate the V3 final holdout exactly once
```

No step implicitly authorizes the next authorization boundary.

## 17. Acceptance semantics

Possible Gate B outcomes are:

- `PASS` — a preregistered candidate demonstrates acceptable forecast and economic behavior under the frozen ordinary folds and then survives the one-shot final holdout;
- `NO_TRADE` — the statistically preferred safe result when forecast/economic evidence does not support a tradeable policy;
- `INSUFFICIENT_EVIDENCE` — chronology, coverage, or market counts cannot satisfy the frozen contract;
- `FAIL` — integrity, contamination, leakage, hash, or other fail-closed contract violation.

A high directional accuracy alone is insufficient for PASS. Positive probability quality, calibration discipline, walk-forward stability, and after-cost economics remain separate required evidence.

## 18. Safety and authorization boundaries

This preregistration does not authorize:

- current V2 adaptive training;
- V3 labeled model training before the prospective epoch/coverage gate is complete;
- production migration or service restart;
- live-prediction activation;
- V3 paper activation;
- final-holdout access;
- automatic promotion;
- Phase 15 progression;
- geographic/compliance bypass;
- live trading;
- nonzero money limits.

The system remains in RESEARCH mode with live trading disabled and zero real-money limits.

## 19. Implementation acceptance criteria

The next repository task is implementation of the preregistered Gate B planning/readiness path only. It must provide tests that prove:

- the exact research identity and 72-hour epoch are immutable;
- diagnosis and consumed-V2-holdout exclusions are hash-bound and cannot enter validation/test/final holdout;
- full-epoch coverage/readiness is outcome-blind;
- walk-forward geometry and minimum market counts are exact;
- ordinary tests cannot affect selection;
- model/calibration/offset/economic candidate sets are exact;
- Polymarket values cannot enter the V3 forecast vector;
- final-holdout labels cannot be read during planning or ordinary-fold research;
- final-holdout evaluation requires a separate one-shot authorization;
- `no_trade` and `insufficient_evidence` remain valid outcomes;
- V2 adaptive training remains paused;
- no model activation, paper activation, live trading, or money-limit change is introduced.

The implementation task must stop before actual V3 labeled training/evaluation unless a later authorization explicitly permits it.
