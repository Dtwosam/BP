# Phase 14 — V3 Gate B Successor Preregistration

**Date:** 14 September 2026  
**Status:** approved research-only successor preregistration design; runtime implementation pending  
**Mode:** RESEARCH only; live trading disabled; all real-money limits remain zero  
**Source main at decision:** `563895f35a0437190ec91ba5ad46a618202443ab`

## 1. Decision

The frozen `v3-gate-b-preregister-v1` attempt is retired as non-executable for V3 policy selection.

The v1 contract required an exact hash-bound exclusion manifest for the 84-trade diagnosis cohort. Exhaustive repository and host-side recovery found the original diagnosis methodology but not the exact 84 `condition_id` identities, a cohort hash, or a replay-safe frozen snapshot. Those identities will not be reconstructed from later production state, current settlements, outcomes, inferred timestamps, approximate date windows, or an empty/synthetic replacement manifest.

The existing v1 epoch from `2026-09-13T13:45:00Z` through `2026-09-16T13:45:00Z` remains truthful immutable evidence for engineering, source availability, leakage, and coverage diagnostics only. It must not be used for V3 model or policy selection, validation/test selection, or final-holdout evaluation.

A wholly future successor contract is therefore frozen before its prospective epoch begins.

## 2. Successor identity and epoch

The successor research-plan identity is:

```text
research_plan_version = v3-gate-b-preregister-v2
dataset_version       = supervised-core-v3-btc-native-v1
feature_version       = core-v3-btc-native
label_version         = official-outcome-v1
horizon_seconds       = 300
feature_offsets       = 60, 120, 180, 240
```

The successor prospective epoch is the half-open interval:

```text
2026-09-16T13:45:00Z <= market_start_at < 2026-09-19T13:45:00Z
```

This is again a 72-hour epoch. The chronology geometry remains exactly:

- training: 24h
- validation: 6h
- ordinary test: 6h
- step: 6h
- exactly five ordinary folds
- final holdout: 12h
- embargo: one market
- minimum train/validation/test/final-holdout markets: 240 / 60 / 60 / 120

No change to these statistical geometry rules is authorized by the recovery decision.

## 3. Structural contamination boundary

The successor does not attempt to recover or recreate exact historical diagnosis membership at runtime. Instead, eligibility itself is prospective and structural:

```text
market_start_at >= epoch_start
AND market_start_at < epoch_end
```

Every market whose start precedes `2026-09-16T13:45:00Z` is structurally ineligible for successor training, validation, ordinary test, or final-holdout membership.

Therefore:

- the unrecoverable 84-trade diagnosis cohort cannot enter successor policy selection because it is pre-epoch;
- the exact diagnosis exclusion manifest is preserved as a historical v1 requirement but is not a runtime prerequisite for `v3-gate-b-preregister-v2`;
- the consumed-V2 final-holdout exclusion manifest is likewise historical evidence rather than a successor runtime selection prerequisite because all consumed V2 markets are pre-epoch;
- no historical exclusion identity may be used to widen, shrink, or cherry-pick the successor epoch membership.

The complete consumed-V2 contamination boundary has been recovered from two immutable historical plans as 48 unique condition IDs with zero overlap. Its canonical combined historical exclusion-manifest SHA-256 is:

```text
f581d33676a4f9acfab45a7b0ab5103b36b1d4021fca4a9dc373d927d55e5938
```

Those 48 identities remain permanently non-reusable and preserved as historical contamination evidence. Their recovery does not authorize final-holdout outcomes, predictions, P&L, or model evaluation to be inspected.

## 4. Forecast predictor contract — unchanged from v1

The successor uses exactly the existing `core-v3-btc-native` predictors:

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

Polymarket price/book values remain excluded from forecast predictors. They remain downstream executable benchmark/economic inputs only.

No V3 feature semantics or feature version changes are authorized during this successor preregistration.

## 5. Forecast candidate contract — unchanged from v1

The successor candidate ladder remains exactly:

```text
training_prior
coinbase_momentum_sign_diagnostic
single_feature_btc_logistic
full_v3_logistic
full_v3_xgboost
```

The selection rules remain unchanged:

- primary validation metric: log loss;
- tie-breakers: Brier score, calibration quality, then simpler model;
- XGBoost may replace full V3 logistic only if it is strictly better on both validation log loss and validation Brier score;
- calibration candidates: identity and Platt;
- Platt is eligible only if it improves both validation log loss and Brier score and its coefficient is nonnegative.

This successor decision does not add, remove, or retune any model candidate.

## 6. Economic decision contract — unchanged from v1

The frozen economic rules remain:

- fee rate: `0.07`;
- slippage buffer: `0.01`;
- minimum-edge grid: `0, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15`;
- explicit `no_trade` candidate: required;
- maximum selected-book age: 10 seconds;
- minimum validation trades per fold: 8;
- at least four validation folds must have nonnegative P&L;
- aggregate validation P&L must be positive.

The historical 30% gap observation and BTC-momentum interaction remain diagnosis only. They are not hard-coded as successor policy rules.

## 7. Outcome blindness and stage boundaries

Successor readiness remains outcome-blind. It may assess only feature coverage, timestamps, source availability, leakage/future-cutoff invariants, epoch membership, and frozen contract hashes.

Successor planning remains feature-only and read-only. It may freeze chronological fold membership, the final 12h reserved membership, and deterministic hashes, but it must not read labels or outcomes.

No model fitting is authorized by this preregistration.

No final-holdout access is authorized by this preregistration. Final-holdout label access and evaluation remain a separate one-shot explicit authorization boundary after all ordinary model-selection work is frozen.

No model activation, paper activation, production deployment, migration, restart, live trading, geographic bypass, automatic promotion, or money-limit change is authorized.

## 8. Implementation requirement before epoch start

The repository runtime contract must be updated to `v3-gate-b-preregister-v2` before `2026-09-16T13:45:00Z`.

The implementation must:

1. freeze the successor identity and epoch exactly as specified here;
2. enforce structural half-open epoch eligibility for every readiness/planning market query;
3. remove the historical diagnosis and consumed-V2 manifest arguments from the successor runtime interface rather than accepting synthetic or empty replacements;
4. bind the structural contamination policy into readiness/plan hashes;
5. preserve PostgreSQL read-only transactions and feature-only planning;
6. keep plan output exclusive-create/no-clobber;
7. add regression coverage proving no pre-epoch market can enter any successor fold or final reserve;
8. preserve all v1 predictor, candidate, calibration, economic, chronology, and safety rules not explicitly changed by this document.

If that implementation is not merged before `2026-09-16T13:45:00Z`, the successor epoch must be moved forward again and re-preregistered before the new epoch begins. The epoch must never be backdated around an implementation delay.

## 9. Successor execution order

After the successor runtime contract is merged before epoch start:

1. continue underlying Coinbase/Bybit compact-state collection unchanged;
2. collect/materialize `core-v3-btc-native` evidence for the successor epoch;
3. do not run successor readiness before `2026-09-19T13:45:00Z`;
4. after epoch close, run outcome-blind successor readiness;
5. only if readiness passes, run feature-only planning to freeze exactly five ordinary folds and the 12h final reserve;
6. stop before model fitting and final-holdout access unless separately authorized.

This document supersedes `v3-gate-b-preregister-v1` only for future Gate B policy-selection execution. It does not rewrite or invalidate historical v1 evidence.