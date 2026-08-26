# Phase 9 — Probability Calibration + Edge Engine Design

**Date:** 25 August 2026  
**Status:** Approved for implementation under the project’s standing autonomous-build instruction  
**Base:** `main` at `76aef668a9be972ffd2f22b7c571974f1f36d450`  
**Mode:** RESEARCH only; live trading remains disabled

## 1. Purpose

Phase 8 proved that directional accuracy is not itself a trading rule. The accepted 5m walk-forward report exceeded 80% accuracy while losing money at observed asks, and the 15m ordinary-OOS headline did not survive the untouched final holdout. Phase 9 therefore adds a deterministic decision layer that answers a narrower question:

> After calibration and explicit executable-price costs, is the estimated probability far enough from the observed market ask to justify a hypothetical trade, or should the engine abstain?

A valid Phase 9 result may be **NO TRADE** for every market. The phase is successful when calibration, edge calculation, abstention, provenance, and leakage controls are correct and reproducible; it does not require positive P&L.

## 2. Scope

Phase 9 will implement:

- per-horizon post-hoc probability calibration for the accepted Phase 8 `market_price` source;
- identity calibration as the mandatory baseline/fallback;
- monotone Platt/logit calibration as the only V1 challenger;
- validation-only calibration-method selection;
- side-aware executable edge against an observed fresh selected-side best ask;
- current documented Polymarket taker-fee curve support through an explicit fee-rate configuration;
- an explicit slippage buffer;
- spread-aware execution semantics using the observed ask rather than midpoint, plus spread reporting/gating without double-charging the spread;
- staleness/missing-book no-fill semantics;
- a validation-selected minimum-edge uncertainty margin;
- first-class abstention, including a `no_trade` policy candidate;
- immutable Phase 9 run registry rows and deterministic semantic hashes;
- one-command offline evaluation for 5m and 15m;
- production-host acceptance with trading disabled.

Phase 9 will not add:

- live prediction;
- order placement or wallet code;
- paper execution infrastructure;
- new predictive features;
- a more complex predictive model;
- isotonic calibration in V1;
- holdout-driven calibration, offset selection, fee assumptions, slippage assumptions, or edge thresholds.

## 3. Approaches considered

### A. Identity calibration + fixed edge threshold

This is the smallest implementation. It would compare the accepted market probability directly with the executable ask and abstain below a fixed margin.

**Advantage:** minimal variance and very easy to audit.  
**Problem:** it cannot correct systematic over/under-confidence, so it does not fully satisfy the calibration objective.

### B. Identity vs monotone Platt calibration + validation-selected edge policy — selected

Fit a one-dimensional logistic calibration map from the source probability’s logit to the official outcome using training markets only. Compare it with identity calibration on validation log loss and Brier score. Then select a minimum executable-edge threshold on validation only, with `no_trade` always available.

**Advantages:** deterministic, interpretable, low parameter count, stable enough for the current sample sizes, and directly compatible with the Phase 8 fold structure.  
**Risk control:** Platt is rejected unless it is monotone in the expected direction and improves both validation log loss and Brier score versus identity.

### C. Isotonic calibration

A non-parametric monotone mapping could fit richer reliability curves.

**Advantage:** flexible calibration shape.  
**Problem:** current per-fold validation/training samples are too small to justify the extra degrees of freedom. It would make threshold-selection overfitting easier and is deferred unless later data proves Platt/identity inadequate.

## 4. Frozen source contract

Phase 9 consumes immutable Phase 8 backtest runs, not arbitrary dataset partitions.

For each horizon, a `BacktestSourceSpec` loader will validate:

- `backtest_version == "walk-forward-v1"`;
- source training run is the accepted Phase 7 `market_price` contract;
- dataset/feature/label versions match the stored Phase 8 run;
- requested horizon/window and dataset SHA-256 match reconstructed data;
- fold memberships and selected offsets exist and are internally consistent;
- ordinary test markets do not repeat;
- final holdout is disjoint from ordinary OOS markets.

The Phase 9 source-spec object intentionally exposes only membership, selected offsets, hashes, and source identity. It does **not** expose Phase 8 test/holdout metrics to the calibration/selection code. This prevents accidental use of prior test results as tuning input.

Accepted production source IDs for the fixed-day host gate remain:

- 5m: `phase8-300-efdf493067e9d56419afc4d88452bec6`;
- 15m: `phase8-900-64aaf2b1774ee7af37bd110b84b37ec1`.

## 5. Probability rows and eligibility

The source predictor remains the Phase 7/8 market-price model. At the stored Phase 8 selected offset:

- `pm_up_price` is the raw Up probability when present;
- rows without an observed `pm_up_price` are not eligible for Phase 9 trade-edge selection;
- training-prior fallback may still exist in the inherited predictor implementation for Phase 8 reproducibility, but Phase 9 must not convert a fallback probability into an executable trade signal;
- every condition may contribute at most one row at the selected offset.

This separates prediction-coverage reporting from trade eligibility and prevents a missing market price from masquerading as edge.

## 6. Calibration contract

### 6.1 Identity calibrator

`p_cal = clip(p_raw, 1e-6, 1 - 1e-6)`.

Identity is always available and is the default if the challenger cannot be safely promoted.

### 6.2 Platt/logit calibrator

For training rows at the selected offset with observed market probability:

1. clip `p_raw` to `[1e-6, 1 - 1e-6]`;
2. transform to `x = log(p_raw / (1 - p_raw))`;
3. fit deterministic one-feature logistic regression against the official Up/Down target using equal-market weights;
4. use fixed solver/seed/max-iteration settings;
5. reject the fitted challenger if the logit coefficient is not finite or is `<= 0`.

A non-positive slope would reverse the source ranking and is not considered calibration in V1.

### 6.3 Calibration selection

The calibrators are fit on **training markets only**. Validation rows choose the calibrator using:

1. lower validation log loss;
2. lower validation Brier score as tie-break;
3. identity as final tie-break.

Platt is promotion-eligible only when it improves **both** validation log loss and validation Brier score versus identity. Otherwise identity is selected.

The ordinary test partition and final holdout cannot alter calibration choice or parameters.

For the final holdout evaluation, the chosen calibrator is still fit only on final-train markets; final-validation chooses method and edge threshold, and final-holdout remains untouched until evaluation.

## 7. Side and executable-price contract

Given calibrated `p_up`:

- if `p_up >= 0.5`, side is Up and `p_side = p_up`;
- otherwise side is Down and `p_side = 1 - p_up`.

The executable price is the observed fresh best ask for that selected side, using the existing Phase 8 contract:

- Up → `pm_up_best_ask`;
- Down → `pm_down_best_ask`;
- selected-side book missing → unavailable/no trade;
- selected-side book stale → unavailable/no trade;
- invalid/missing ask → unavailable/no trade.

The selected-side best bid is used only to report the observed spread and to apply an optional `max_spread` gate. The engine does not use midpoint as a fill price.

Comparing fair probability to the **ask** already pays the crossing spread relative to midpoint; Phase 9 therefore does not subtract the spread a second time. Spread is accounted for through executable-ask pricing and explicit spread reporting/gating.

## 8. Fee, slippage, and uncertainty contract

### 8.1 Fee curve

The engine supports the documented taker fee form for one share:

`fee = fee_rate * ask * (1 - ask)`

`fee_rate` is explicit run configuration and must be stored in the immutable report. It is not inferred from the outcome label or hidden global state.

For the Phase 9 production research gate, the pinned BTC-crypto taker fee-rate assumption will be `0.07`, matching the current Polymarket crypto fee documentation as of 25 August 2026. It is an explicit research assumption, not proof that every historical market had identical fee parameters. Later live-prediction work must retrieve and preserve per-market CLOB fee parameters rather than rely on this research constant.

### 8.2 Slippage

Exact historical depth is not available for all research rows, so Phase 9 does not fabricate depth or partial fills. Instead it subtracts a fixed, explicit per-share `slippage_buffer` from expected edge and realized diagnostic P&L.

The production research gate will pin `slippage_buffer = 0.01` USDC/share as a conservative one-cent sensitivity assumption. Reports must label this as an assumption rather than observed slippage.

### 8.3 Uncertainty margin

The validation-selected `min_edge` is the V1 uncertainty margin. No trade is eligible unless the cost-adjusted expected edge exceeds this additional margin. This avoids inventing a second opaque confidence penalty while still forcing the estimated edge to clear a validation-chosen safety buffer.

## 9. Edge calculation

For an executable row:

```text
raw_edge = p_side - ask
fee = fee_rate * ask * (1 - ask)
cost_adjusted_edge = raw_edge - fee - slippage_buffer
trade = cost_adjusted_edge >= min_edge
```

For a realized one-share diagnostic:

```text
payout = 1 if selected side resolves true else 0
realized_pnl_after_assumed_costs = payout - ask - fee - slippage_buffer
```

The report will keep raw edge, fee, slippage assumption, cost-adjusted edge, and realized diagnostic P&L separate. It must never label this as proven live net P&L because latency, depth/partial fills, cancellations, and historical fee provenance are not fully modeled.

## 10. Minimum-edge policy selection

The default deterministic threshold grid is:

`0.00, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15`

and an explicit `no_trade` candidate.

Threshold selection uses **validation only** after calibration selection.

A threshold candidate is eligible only when:

- at least `min_validation_trades = 3` validation trades execute under the Phase 9 eligibility contract;
- total realized P&L after the configured fee/slippage assumptions is strictly positive;
- mean realized P&L after assumed costs is strictly positive.

Among eligible thresholds choose, in order:

1. highest total validation realized P&L after assumed costs;
2. highest mean validation realized P&L after assumed costs;
3. higher `min_edge` as the conservative tie-break.

If no threshold is eligible, select `no_trade`. `no_trade` is therefore a normal deterministic policy result, not an error.

Neither ordinary test nor final holdout can change the threshold.

## 11. Fold data flow

For each stored Phase 8 ordinary fold:

1. reconstruct train/validation/test membership from immutable Phase 8 source metadata;
2. use the stored Phase 8 selected offset; do not reselect timing;
3. fit the raw market-price fallback prior on train for source-contract consistency;
4. restrict Phase 9 calibration/edge rows to observed market price at the selected offset;
5. fit identity and Platt candidates on train;
6. choose calibrator on validation only;
7. choose edge threshold or `no_trade` on validation only;
8. freeze both choices;
9. evaluate the ordinary test partition once;
10. append its immutable report without reusing the test market elsewhere.

For final holdout:

1. use stored final-train/final-validation/final-holdout membership and stored selected offset;
2. fit calibration candidates on final-train only;
3. choose calibrator and edge threshold on final-validation only;
4. freeze policy;
5. evaluate final-holdout once.

## 12. Reports and metrics

Each fold and final holdout report will include:

### Calibration

- selected calibration method;
- identity and Platt validation log loss/Brier/ECE;
- selected calibrator parameters;
- raw and calibrated test metrics;
- calibration coverage and observed-market-price coverage.

### Edge/execution

- predicted markets;
- observed-price eligible markets;
- executable markets;
- trade count;
- abstention/no-fill counts by reason;
- trade coverage;
- selected `min_edge` or `no_trade`;
- average ask;
- average observed spread when measurable;
- raw expected edge sum/mean;
- fee assumption sum;
- slippage assumption sum;
- cost-adjusted expected edge sum/mean;
- correct traded outcomes;
- traded-subset accuracy;
- realized gross P&L before fee/slippage assumptions;
- realized P&L after the configured fee/slippage assumptions.

Aggregate ordinary-OOS reporting will concatenate only the already-frozen fold test decisions; it will not re-optimize one global threshold over OOS results.

## 13. Versions and immutable registry

New constants:

- `CALIBRATION_VERSION = "platt-or-identity-v1"`
- `EDGE_POLICY_VERSION = "selected-ask-edge-v1"`

Migration `0009_calibration_edge_runs.sql` will create immutable `calibration_edge_runs` rows containing at least:

- `run_id`;
- calibration/edge versions;
- source Phase 8 run id and semantic SHA-256;
- source training run id and semantic SHA-256;
- dataset/feature/label/horizon/window identity;
- dataset SHA-256;
- config + config SHA-256;
- source fold/plan hashes;
- complete semantic report JSON;
- semantic SHA-256;
- created timestamp.

The run id is deterministic:

`phase9-<horizon_seconds>-<semantic_sha256[:32]>`

Identical reruns are existing/no-op. Any attempt to store a different semantic report under the same run id fails closed.

Calibration coefficients are small deterministic JSON values and do not require an external binary artifact in V1.

## 14. CLI

Add `scripts/run_calibration_edge.py` backed by a Phase 9 CLI/service.

Required inputs:

- `--source-backtest-run-id` (repeatable);
- `--start` / `--end`;
- `--fee-rate`;
- `--slippage-buffer`;
- optional threshold grid / minimum validation trades / maximum spread;
- `--env-file`;
- optional output directory for immutable JSON evidence.

The core service is PostgreSQL/offline only. It does not call Polymarket, Coinbase, Bybit, or any wallet/order endpoint.

## 15. Error handling / fail-closed rules

Fail the run when:

- source backtest identity/version/hash is missing or inconsistent;
- dataset SHA/version differs from Phase 8;
- stored fold memberships/offsets are inconsistent;
- a condition appears in multiple evaluation partitions;
- calibration train data has invalid probabilities/targets;
- Platt parameters are non-finite (challenger rejected; identity remains available);
- a stored run id conflicts semantically;
- final holdout overlaps any ordinary OOS condition;
- trade logic uses missing/stale/non-finite executable price;
- trading safety is not RESEARCH / disabled during host acceptance.

Do **not** fail merely because:

- Platt does not beat identity;
- no edge threshold is profitable on validation;
- the selected policy is `no_trade`;
- test/holdout P&L is negative.

Those are valid research outcomes.

## 16. Tests

TDD coverage must include:

- deterministic identity and Platt calibration;
- probability clipping and monotone-positive-slope guard;
- Platt cannot promote unless validation log loss and Brier both improve;
- calibration fit excludes validation/test/holdout labels;
- Phase 8 selected offset is reused, never reselected from OOS data;
- raw-price fallback cannot become a trade-eligible row;
- side-aware Up/Down ask selection;
- stale/missing book => no fill/no trade;
- ask-based pricing, no midpoint/synthetic fill;
- fee-curve calculation;
- slippage-buffer calculation;
- spread reporting/gating without double subtraction;
- validation-only threshold selection;
- `no_trade` wins when no threshold has positive validation economics;
- minimum validation trade gate prevents one-trade cherry-picks;
- test/holdout results cannot rewrite calibrator/threshold;
- ordinary OOS markets never repeat;
- final holdout disjointness;
- deterministic report/hash/run id;
- immutable registry identical rerun and conflict behavior;
- PostgreSQL integration test;
- deployment asset syntax and safety.

## 17. Production acceptance

The Phase 9 host gate will remain pinned to the accepted fixed day:

`2026-08-24T00:00:00Z <= market_start_at < 2026-08-25T00:00:00Z`

and accepted Phase 8 run ids for 5m/15m.

Before host execution, the exact candidate SHA must pass fresh:

- CI;
- Historical Backfill Smoke;
- Live Recorder Smoke;
- Recorder Short Soak.

Host acceptance must verify:

- exact candidate SHA provenance via exported non-Git source architecture;
- source Phase 8 run ids/semantic hashes exactly match accepted evidence;
- migration applies idempotently;
- semantic rerun matches;
- second run creates zero registry rows;
- no partition/test/holdout overlap;
- stored Phase 8 offsets are reused exactly;
- calibrator selection uses training fit + validation choice only;
- edge threshold uses validation only;
- no trade is produced from missing/stale book or fallback market probability;
- fee/slippage assumptions are explicit in the report;
- `no_trade` is accepted as valid output;
- report contains both 5m and 15m calibration/edge metrics;
- disk status is `ok` before and after using bounded `disk-health` checks;
- recorder remains active;
- `LIVE_TRADING_ENABLED=false`;
- `MAX_TRADE_SIZE_USD=0`;
- `MAX_DAILY_LOSS_USD=0`.

Phase 10 live prediction remains blocked until Phase 9 host acceptance, closeout, exact-head gates, and merge.

## 18. Research interpretation

Phase 9 optimizes **decision quality**, not headline accuracy. A probability is useful only if its executable edge survives costs and uncertainty. The design therefore prefers abstention over forced volume and treats small-sample spectacular accuracy as insufficient evidence.

Any future promotion claim must distinguish:

- calibration improvement;
- trade-selection coverage;
- gross ask-based P&L;
- assumed fee/slippage-adjusted diagnostics;
- later real paper/live fill evidence.

Live trading remains disabled and requires a later dedicated gate plus explicit user authorization.
