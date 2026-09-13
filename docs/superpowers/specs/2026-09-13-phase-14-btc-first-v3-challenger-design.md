# Phase 14 — BTC-First V3 Challenger Design

**Date:** 13 September 2026  
**Status:** research design; no training, deployment, model activation, paper activation, final-holdout access, or live trading authorized  
**Phase:** 14 — live readiness research correction  
**Mode:** RESEARCH only; live trading disabled; all real-money limits remain zero  
**Base:** `main` at `60e7e1fa48355fcc7781fb379b0846353eb0a456`  
**Branch:** `research/btc-first-v3-design`

## 1. Purpose

The current paper strategy is not an independent BTC-direction forecaster. Its live probability is derived from a Polymarket Up-token historical price observation and is compared against a fresher executable selected-side order-book ask. Read-only production investigation on 13 September 2026 showed that this construction can turn ordinary market repricing into very large apparent `edge`, especially when the strategy trades against contemporaneous BTC movement.

This design introduces a new research-only challenger whose forecast is produced from BTC-native information and whose economic decision is evaluated separately against the current Polymarket executable ask.

The first V3 feature identity is:

```text
feature_version = core-v3-btc-native
label_version   = official-outcome-v1
horizon_seconds = 300
feature_offsets = 60, 120, 180, 240
```

V1 and V2 evidence remain immutable. V3 does not rewrite `core-v1`, `core-v2-last-trade`, historical predictions, paper orders, settlements, P&L, Gate B evidence, or the consumed V2 final holdout.

## 2. Production diagnosis that motivates V3

The investigation used only read-only production SQL/API evidence. No production mutation, migration, training, restart, activation, live trade, or money-limit change occurred.

### 2.1 Paper outcome

At the investigated snapshot:

```text
settled paper trades = 84
open positions       = 0
fills                = 98
realized P&L         ~= -100 USD
```

Directional correctness against the official outcome was:

```text
correct = 26 / 84 = 30.95%
wrong   = 58 / 84 = 69.05%
```

Correct trades produced approximately `+103.85 USD`; wrong trades produced approximately `-203.85 USD` before netting to about `-100 USD` after recorded fees/costs.

The wrong trades carried substantially larger claimed cost-adjusted edge than the correct trades:

```text
correct avg claimed edge ~= 0.1328
wrong avg claimed edge   ~= 0.4015
```

This is the opposite of the relationship required from a useful edge score.

### 2.2 Calibration and token/book wiring were not the cause

All 84 settled trades were aligned with the raw probability side. There were no calibration-induced side flips.

The production plumbing checks also showed:

```text
probability request not using Up token = 0 / 84
Up selected-ask mismatch              = 0 / 84
Down selected-ask mismatch            = 0 / 84
```

The defect is therefore not explained by an Up/Down token swap, selected-book mismatch, or calibration-side inversion.

### 2.3 BTC-native signal was materially stronger

The historical `btc_candles` table was correctly rejected for this investigation because its Coinbase 1-minute backfill stopped on 24 August 2026 and was stale for the August 28 through September 11 paper-trade window.

The live compact state was then used instead. Coinbase `BTC-USD` `market_state_1s` contained 579,873 rows inside the 84-trade window and covered the full period.

Using fresh state observations no more than ten seconds old:

```text
fresh trades                          = 84
flat BTC windows                      = 1
official outcome matches Coinbase BTC = 64 / 84 = 76.19%
current machine matches Coinbase BTC  = 31 / 84 = 36.90%
BTC pre-decision direction matches final Coinbase BTC = 64 / 84 = 76.19%
```

Coinbase is an independent directional proxy rather than the exact settlement oracle, so `76.19%` is not an official-label accuracy claim for Coinbase. It is sufficient evidence that BTC-native movement contained useful information that the deployed paper decision rule was not exploiting.

### 2.4 Apparent edge was largely a stale-market-price/current-book gap

Across the 84 settled trades, the selected-side probability/current-ask gap averaged approximately `0.3403`, and 54/84 trades exceeded a 20 percentage-point gap.

Performance by gap band was:

| selected-side probability minus current ask | trades | correct | accuracy | realized P&L |
| --- | ---: | ---: | ---: | ---: |
| `<10%` | 10 | 8 | 80.00% | +12.98 |
| `10-20%` | 20 | 11 | 55.00% | -0.15 |
| `20-30%` | 9 | 5 | 55.56% | +7.40 |
| `30-40%` | 9 | 1 | 11.11% | -26.77 |
| `40%+` | 36 | 1 | 2.78% | -93.46 |

Combined:

```text
gap < 30%  : 24 / 39 correct = 61.54%, P&L ~= +20.24 USD
gap >= 30% :  2 / 45 correct =  4.44%, P&L ~= -120.24 USD
```

The probability observation age was roughly 45 seconds across all bands, so age alone did not separate winners from losers. The harmful behavior appeared when the fresh executable market had moved far away from the older price-derived probability.

### 2.5 High-gap trades were predominantly against BTC movement

Momentum alignment made the mechanism clearer:

```text
with BTC pre-decision direction:
    29 trades
    19 correct = 65.52%
    P&L ~= +42.42 USD

against BTC pre-decision direction:
    54 trades
     7 correct = 12.96%
    P&L ~= -137.79 USD
```

For `gap >= 30%` specifically:

```text
against BTC momentum: 38 trades, 0 correct, P&L ~= -118.23 USD
with BTC momentum:      7 trades, 2 correct, P&L ~=  -2.01 USD
```

The 0/38 observation is diagnostic evidence from this finite sample, not a rule to hard-code into future trading policy.

## 3. Root-cause conclusion

The live V1 probability path requests Polymarket `/prices-history` for the Up token and selects the latest point at or before prediction time. The current executable ask comes from a separate, fresher order-book state.

Conceptually the deployed paper signal can therefore behave like:

```text
older Polymarket-implied side probability
    minus
newer Polymarket executable ask
    minus
costs
```

When BTC and Polymarket move materially during the timestamp gap, that difference can become large because the older price has not caught up with the current book. The paper system then interprets market movement as forecast edge and can take a contrarian position against newly incorporated information.

The investigation therefore rejects the current apparent-edge construction as a basis for adaptive learning or promotion.

It does **not** prove that every contrarian trade is wrong, that a 30% gap must always be rejected, or that simple BTC momentum will remain 76% accurate prospectively. Those would be overfit conclusions from the diagnosis cohort.

## 4. Canonical research decision

### 4.1 Pause the current adaptive training cycle

The existing adaptive-readiness evidence remains valid. The first-cycle bootstrap boundary remains immutable, and the observed eligible-market count is not reset.

However, `adaptive-train` for the current `core-v2-last-trade` learning stream is **paused**. No adaptive cycle ledger row should be written and no model artifact should be trained from that stream until the BTC-first challenger question is resolved.

This pause is a research-direction decision, not evidence corruption. Existing readiness counts, V2 features, V2 Gate B results, and the consumed final holdout remain truthful immutable evidence.

### 4.2 Do not tune a retrospective 30% gap cutoff

The 84-trade diagnosis cohort may establish that the current edge semantics failed. It must not be used to select a new `min_edge`, momentum threshold, gap cutoff, model confidence cutoff, or promotion rule.

The exact 84-trade diagnosis cohort must be treated as contaminated for V3 policy selection. It may be used for regression/diagnostic tests of the old failure mode but not for choosing V3 economic hyperparameters or claiming prospective performance.

### 4.3 Separate forecasting from pricing

V3 uses two distinct stages:

```text
BTC-native state -> forecast P(final outcome = Up)

forecast probability + fresh Polymarket book -> executable economic decision
```

Polymarket price is the benchmark/executable price to beat. It is not the source of the first V3 probability forecast.

## 5. Approach selection

Three approaches were considered.

### A. Patch V2 with a gap cap or momentum veto

This is the smallest change but is rejected as the primary research direction. The observed 30% failure boundary and momentum interaction were discovered from settled outcomes, so directly encoding them would overfit the diagnosis cohort and retain the same market-price-derived forecast semantics.

### B. Hybrid BTC + Polymarket model from the start

This could be powerful, but it makes it harder to determine whether any gain comes from BTC-native forecasting or simply a different transformation of Polymarket state. It also weakens independence between forecast and price-to-beat.

A hybrid model may be tested later as a separately versioned challenger after a BTC-native baseline is established.

### C. BTC-native forecast plus separate Polymarket execution layer — selected

This is the V3 design. It creates the cleanest falsifiable experiment:

- can BTC-native information predict the official 5-minute outcome out of sample?
- once calibrated, is that independent probability economically superior to the current executable ask after costs?

This approach is selected.

## 6. V3 feature contract

### 6.1 Scope

The first V3 feature family is 5-minute only:

```text
feature_version  = core-v3-btc-native
horizon_seconds  = 300
feature offsets  = 60, 120, 180, 240 seconds
```

`official-outcome-v1` remains the label authority.

V3 does not modify the existing V1 or V2 feature constants.

### 6.2 Forecast predictors

The first V3 forecast intentionally stays small and interpretable. It uses timestamp-coherent BTC state already recorded by BP.

At each feature time `T`, use as-of observations at or before the requested timestamp and never use a state whose effective availability is after that timestamp.

Initial candidate predictors:

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

Each price-derived return must be computed from exact as-of source observations whose cutoffs are recorded in the feature provenance.

The first V3 contract does **not** require realized volatility, raw order flow, acceleration, or large technical-indicator families. Those are later challengers only if the small BTC-native baseline leaves justified headroom.

### 6.3 Missingness and freshness

Every source has explicit missing/stale flags. Missing values remain missing and are represented through the existing dataset missing-flag mechanism.

No forecast input may silently fall back to a Polymarket price, a training prior, a later BTC observation, or a value from another venue.

The V3 source reader must enforce no-future semantics for every as-of observation.

### 6.4 Polymarket fields are excluded from the first forecast feature payload

The first V3 forecast model must not include:

```text
pm_up_price
pm_down_price
pm_up_last_trade_price
pm_down_last_trade_price
pm_up_best_ask
pm_down_best_ask
pm_up_best_bid
pm_down_best_bid
```

or derived Polymarket price/book transforms as model predictors.

This exclusion is deliberate. It preserves a clean separation between an independent BTC forecast and the market price used later to decide whether a trade has positive expected value.

Polymarket book state is still required for economic evaluation, but it belongs to the execution/evaluation layer rather than the forecast feature vector.

## 7. V3 source implementation

The preferred source for V3 forward research is `market_state_1s`, because production already records dense live Coinbase and Bybit BTC state and the investigation proved that this table covered the paper-trade window.

The existing `btc_candles` historical backfill remains useful historical evidence but must not be assumed current. The 13 September investigation found its Coinbase one-minute data ended on 24 August 2026.

A V3 as-of state reader should expose a small explicit interface for nearest-known state at or before a requested time. The first implementation should reuse existing `FeatureSourceReader.latest_state(...)` semantics where possible rather than add an unrelated data path.

For each feature time, the generator requires BTC state at:

- market start;
- `T - 120s` when inside available history;
- `T - 60s`;
- `T - 30s`;
- `T`.

Each selected source cutoff and row identity participates in the input fingerprint.

## 8. Baselines and models

Every V3 model candidate must beat simple baselines out of sample.

Required forecast baselines:

1. **training prior** — class prior only;
2. **Coinbase momentum sign diagnostic** — direction of `coinbase_return_from_market_start`; reported as directional accuracy/coverage, not treated as a calibrated probability by itself;
3. **single-feature BTC logistic** — logistic regression using only `coinbase_return_from_market_start` plus the minimum required missingness handling;
4. **full V3 logistic** — regularized logistic model over the frozen V3 predictor contract;
5. **XGBoost challenger** — only eligible if it improves probability metrics over the simpler models under the existing complexity/promotion discipline.

The existing generic dataset loader, chronological split, equal-market weighting, logistic trainer, XGBoost trainer, and probability metrics should be reused rather than forked unless a concrete incompatibility is demonstrated.

The legacy `MarketPriceBaseline` remains reportable as an external market benchmark when a timestamp-coherent current market probability is available, but it is not a V3 forecast model and does not supply the V3 probability used for edge.

## 9. Research partitions and contamination controls

### 9.1 Never reuse the consumed V2 final holdout

The V2 Gate B final holdout has already been consumed and is permanently non-reusable. V3 receives a new dataset identity and a new independently frozen evaluation design.

### 9.2 Diagnosis cohort is not a V3 policy-selection set

The 84 settled paper trades inspected on 13 September 2026 were used to discover the failure mode. Their outcomes and economic results therefore cannot select V3 timing, feature thresholds, calibration, edge thresholds, or promotion criteria.

A future implementation may either exclude them entirely from the first V3 modeling run or admit them only to the training portion after the V3 feature/model/economic contract is frozen. They may never enter V3 validation, test, or final holdout selection.

### 9.3 New V3 preregistration boundary

Before any labeled V3 policy selection or final evaluation, commit a V3 research configuration that freezes at least:

- dataset/feature/label versions;
- eligible market interval;
- diagnostic-cohort exclusion IDs/hash;
- fixed feature offsets;
- predictor names;
- split/walk-forward rules;
- calibration candidates;
- execution-cost assumptions;
- edge threshold candidate grid including `no_trade`;
- untouched final-holdout identity/rule.

The final holdout must not be inspected during feature or policy iteration.

## 10. Forecast evaluation

Forecast quality and economic quality are separate gates.

Forecast evaluation reports at minimum:

```text
log loss
Brier score
calibration diagnostics
directional accuracy
offset-level metrics
market-level weighting
coverage / missingness
```

A V3 candidate does not pass merely because directional accuracy is high on one sample. Probability quality, chronology, calibration, stability, and missingness remain required.

The observed 76.19% Coinbase momentum result is a research clue and baseline target, not a promised future accuracy level.

## 11. Economic evaluation

After a V3 model produces an out-of-sample calibrated `P(Up)`, the execution layer reads the fresh current Polymarket book at the same decision time.

For an Up decision:

```text
side_probability = P(Up)
raw_edge          = side_probability - up_best_ask
```

For a Down decision:

```text
side_probability = 1 - P(Up)
raw_edge          = side_probability - down_best_ask
```

Then apply the same explicit fee and slippage assumptions used by the accepted research configuration:

```text
cost_adjusted_edge = raw_edge - fee - slippage_buffer
```

The current selected-book freshness and numeric-integrity rules remain fail-closed.

The edge threshold is selected only from permitted validation evidence. `no_trade` remains a mandatory candidate.

V3 must explicitly report the relationship between claimed edge and realized results by edge band so that a recurrence of the V1 anti-edge pattern is visible immediately.

## 12. Prospective architecture

V3 is implemented in gated subprojects.

### Gate A — BTC-native source + immutable feature capture

Deliver:

- new V3 model/feature constants without changing V1/V2 constants;
- BTC as-of source selection with leakage tests;
- `core-v3-btc-native` feature generation at 60/120/180/240 seconds;
- provenance, source cutoff, fingerprint, and immutable repository tests;
- coverage-only reporting;
- no labeled model training as part of Gate A.

### Gate B — preregistered V3 model/economic research

Only after Gate A has enough coverage:

- freeze the V3 research plan before final evaluation;
- build the V3 dataset against `official-outcome-v1`;
- evaluate prior, BTC momentum diagnostic, single-feature logistic, full logistic, and XGBoost challenger;
- calibrate using permitted training/validation partitions only;
- select timing and edge policy on validation only;
- evaluate untouched test/final holdout exactly once under the frozen plan;
- accept `no_trade` if no stable positive economic edge survives costs.

### Gate C — shadow V3 prospective prediction

Only after an independently accepted Gate B policy:

- add a new prediction version such as `live-prediction-v3-btc-native`;
- produce immutable shadow predictions;
- do not allow the paper worker to consume the new prediction version yet;
- keep V1/V2/V3 evidence epochs separate.

### Gate D — V3 paper epoch

Only after separate explicit authorization:

- enable V3 money-disabled paper execution;
- begin a new V3 paper evidence epoch;
- do not blend V1 paper P&L with V3 paper P&L;
- require prospective sample, calibration, walk-forward stability, reconciliation, and P&L evidence before any later gate discussion.

No gate implicitly authorizes the next one.

## 13. Adaptive-learning interaction

The current adaptive subsystem remains implemented but paused for `core-v2-last-trade` training.

Rules while V3 is being developed:

- `adaptive-readiness` may remain read-only and continue reporting evidence counts;
- do not reset the 12 September bootstrap boundary;
- do not create an adaptive cycle merely because readiness is true;
- do not consume the current 200+ eligible-market readiness as authorization to train V2;
- do not point the existing adaptive trainer at V3 until V3 has its own explicit dataset/feature contract and source-of-truth update;
- future adaptive V3 learning, if adopted, receives a separately documented versioned learning stream rather than silently changing the meaning of the existing V2 stream.

## 14. TDD requirements

Implementation is test-first.

### V3 BTC source tests

- as-of state at `T` never selects a row whose bucket/event availability is after `T`;
- later state insertion does not change an already-derived feature at `T`;
- Coinbase, Bybit spot, and Bybit linear state identities remain isolated;
- stale/missing state is explicit;
- start/30s/60s/120s anchors select deterministic rows.

### V3 calculator tests

- positive and negative returns are computed correctly;
- return-from-market-start uses only the frozen market-start anchor;
- trailing 30/60/120-second returns use the correct anchors;
- cross-venue direction-agreement flags are deterministic;
- basis/funding/open-interest missingness remains explicit;
- no Polymarket predictor keys appear in the V3 forecast feature payload.

### V3 feature-service tests

- exactly four rows are planned for a valid 300-second market;
- feature version is exactly `core-v3-btc-native`;
- source cutoffs never exceed feature time;
- input fingerprints change when a legitimate source observation changes;
- future perturbations do not change an as-of feature;
- `core-v1` and `core-v2-last-trade` outputs/hashes remain unchanged.

### V3 research tests

- diagnosis-cohort IDs cannot enter validation/test/final holdout;
- consumed V2 final-holdout IDs cannot be reused;
- random market shuffles are forbidden;
- momentum diagnostic is reported separately from probability models;
- XGBoost cannot be preferred unless it improves the frozen probability criteria over simpler baselines;
- economic edge uses V3 calibrated probability and current selected-side ask, never a Polymarket historical-price probability;
- `no_trade` remains a valid selected policy;
- edge-band reporting is mandatory.

### Safety/source-of-truth tests

- mode remains RESEARCH;
- live trading remains disabled;
- trade and daily-loss money limits remain zero;
- automatic promotion remains false;
- V2 adaptive training is documented as paused;
- V3 work does not authorize deployment, migration, model activation, holdout access, paper activation, Phase 15, geographic bypass, live trading, or nonzero money.

## 15. Acceptance criteria for the next implementation task

The immediate implementation target after this design is approved is **Gate A only**: repository-local V3 BTC-native source/calculator/feature generation with tests and documentation.

Gate A is complete only when:

- `core-v3-btc-native` is a separate immutable feature version;
- four 5-minute feature offsets are deterministic;
- BTC inputs are timestamp-coherent and leakage-safe;
- Polymarket price/book predictors are absent from the forecast feature vector;
- provenance and future-perturbation tests pass;
- existing V1/V2 tests remain green;
- no training or production action occurs.

## 16. Safety boundary

This design authorizes repository research/design work only.

It does **not** authorize:

- production deployment;
- database migration on production;
- service restart;
- recorder restart;
- adaptive training;
- V3 training;
- model artifact activation;
- access to a future frozen final holdout;
- V2 or V3 paper activation;
- real-money trading;
- geographic bypass;
- nonzero trade-size or daily-loss limits;
- Phase 15.

Explicit authorization remains required at each controlled boundary.

## 17. Exact next action

After this design is reviewed and accepted, create a test-first implementation plan for **V3 Gate A — BTC-native source + immutable feature capture** only.

Do not run the current `adaptive-train` cycle while this design is under implementation or review.
