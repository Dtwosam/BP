# Adaptive Bitcoin Learning Loop Design

**Date:** 12 September 2026  
**Status:** Implemented research-only milestone; no production mutation and no model activation

## Purpose

Preserve the project’s original machine-learning objective after the Phase 14 V2 Gate B experiment: BP is intended to learn statistical relationships between Bitcoin/market conditions observable before resolution and the eventual official market outcome. It is not intended to learn only from whether an executed trade won or lost.

This document clarifies how future adaptive learning must work while preserving the Master Source of Truth leakage, chronology, calibration, economic-evaluation, and safety rules.

## Source-of-truth interpretation

The Master Source of Truth already defines the supervised learning problem as:

- inputs: information knowable at prediction time, including Bitcoin market/microstructure state, Polymarket state, time remaining, reference/opening distance, volatility/regime, derivatives/cross-market information where available, and missing-data flags;
- label: the official resolved Up/Down market outcome;
- objective: learn statistical relationships between those pre-resolution conditions and the resolved outcome;
- evaluation: chronological out-of-sample accuracy/calibration plus executable after-cost economics.

Therefore:

1. **Resolved market examples are the primary learning examples.** A market can teach the model even when the current policy chose `NO_TRADE`.
2. **Trade outcomes are economic evaluation evidence.** They measure whether the probability model plus execution policy produced usable edge after spread, fees, slippage, fills, and abstention.
3. **Executed trades must not be the sole retraining dataset or sole retraining trigger.** Doing so would prevent a cautious/no-trade policy from learning from the many resolved markets it observed.
4. **No unresolved outcome may enter training.** Features and predictions remain immutable; the official outcome is appended only after resolution.

## Phase 14 V2 Gate B conclusion

The 12 September 2026 timestamp-coherent V2 Gate B run is complete evidence but is **not accepted as a tradable V2 policy**.

Canonical operator evidence is summarized in:

`docs/evidence/phase-14-v2-gate-b-20260912.json`

The ordinary folds were mixed:

- fold 0: validation selected `no_trade`;
- fold 1: validation selected a trade policy and its ordinary test was profitable after costs;
- fold 2: validation selected `no_trade`;
- fold 3: validation selected a profitable trade policy but its ordinary test lost money after costs.

The final train/validation selection was:

```text
policy = no_trade
reason = no_validation_edge_candidate_profitable
```

The separately authorized one-shot final holdout was evaluated under that frozen selection, made zero trades, and is now permanently consumed. It cannot be reused for feature choice, model choice, hyperparameters, calibration, threshold selection, timing/freshness selection, or any later claim of unseen performance.

This result does **not** show that Bitcoin behavior is unlearnable. It shows that the deliberately simple timestamp-coherent V2 last-trade baseline did not establish stable tradable edge.

## Learning-loop design

### 1. Continuous observation

The production research recorder continues collecting immutable market and Bitcoin observations while real-money trading remains disabled.

For each prediction timestamp the system preserves only information knowable at that time. Candidate families remain those already authorized by the Master Source of Truth, including:

- BTC returns/momentum;
- spot/perpetual state;
- order-book spread/depth/imbalance;
- aggressive buy/sell flow and trade velocity;
- realised volatility/regime;
- basis/funding/open-interest/liquidation features where reliably available;
- cross-exchange relationships;
- Polymarket executable prices, spread, depth, last trade, implied probability and time geometry;
- explicit stale/missing-data flags.

No feature may use post-prediction information.

### 2. Outcome completion

When a market resolves, the official outcome becomes the supervised label for the already-frozen prediction-time feature rows.

A resolved market is a learning example whether the active policy traded or abstained.

### 3. Retraining trigger

The initial adaptive trigger is **50 newly resolved eligible markets** since the most recent completed training cycle.

This is intentionally not “50 executed trades.” `NO_TRADE` markets still contain valid supervised information about how Bitcoin and the related market state evolved into the official outcome.

The trigger is a minimum evidence threshold, not a guarantee that a challenger must be promoted. If fewer than 50 new eligible resolved markets exist, no new training cycle starts.

Changing this threshold later is a research-policy change and must be documented before the affected evaluation period is inspected.

### 4. Training data

A challenger training cycle uses the eligible historical supervised dataset available before the cycle cutoff, subject to:

- immutable feature versions;
- chronological ordering;
- explicit source timestamps/cutoffs;
- no final-holdout reuse;
- purging/embargo where needed;
- reproducible dataset/model hashes.

The 50 new resolved markets trigger the cycle; they are **not** the only training rows. Older eligible historical examples remain available according to the frozen training policy.

### 5. Model ladder

The adaptive loop follows the existing Master model ladder rather than inventing unlimited new versions:

1. naive/market baseline;
2. logistic regression;
3. deterministic LightGBM/XGBoost challenger;
4. calibrated boosted-tree ensemble only if simpler models justify it;
5. sequence/deep models only if simpler models plateau and evidence supports the added complexity.

A more complex model must beat the simpler alternatives out-of-sample.

### 6. Challenger evaluation

Each training cycle produces a **challenger**, not an automatic replacement.

Model and calibration selection use only chronological train/validation evidence. Ordinary out-of-sample test periods evaluate the frozen selection and cannot alter it afterward.

Evaluation must include both statistical and economic evidence:

- log loss / Brier / calibration;
- directional accuracy and balanced accuracy;
- coverage / abstention;
- executable trade count;
- traded accuracy;
- gross and after-cost P&L;
- fee, spread, slippage and no-fill effects;
- stability across chronological periods.

High directional accuracy alone is insufficient. The V2 Gate B fold-3 result is the concrete example: good prediction accuracy can coexist with negative executable P&L.

### 7. Promotion boundary

The initial adaptive loop does **not** automatically promote a challenger.

`automatic_promotion=false` remains authoritative.

A challenger may become the next money-disabled paper candidate only after its evidence package satisfies the applicable research gate and the model/policy change is explicitly accepted. Real-money activation remains a completely separate authorization boundary under the Master live gate.

The learning loop may automatically **train, score, and report** challengers once the evidence threshold is reached, but it must not silently switch the production paper or live model.

### 8. Fresh final evidence

Recurring training cycles must not repeatedly consume a “final holdout.” Rolling train/validation/ordinary-test evidence is used to decide whether a challenger is promising enough to justify a new final evaluation.

When a final holdout is warranted:

- it must come from a fresh future epoch not previously inspected for that selection decision;
- membership is frozen before labels are used for final evaluation;
- the model/calibration/policy is frozen before holdout access;
- evaluation is one-shot;
- the holdout becomes permanently non-reusable immediately upon access/attempt according to the fail-closed evidence rules.

## Failure and stop rules

The adaptive loop stops or keeps the incumbent when any of the following applies:

- insufficient newly resolved eligible markets;
- data/timestamp integrity failure;
- missing required labels/features;
- challenger does not beat simpler baselines out-of-sample;
- economics are unstable across ordinary chronological tests;
- after-cost profitability is not positive enough to satisfy the frozen gate;
- calibration degrades materially;
- evidence would require reusing or tuning on a consumed holdout.

There is no rule to create V4, V5, V6, and so on until something happens to look profitable. Added complexity must be evidence-driven and preregistered before the relevant unseen evaluation.

## Safety invariants

This design changes no production safety setting:

```text
MODE=research
LIVE_TRADING_ENABLED=false
MAX_TRADE_SIZE_USD=0
MAX_DAILY_LOSS_USD=0
automatic_promotion=false
```

The existing money-disabled research/paper infrastructure may continue collecting evidence. This design does not authorize V2 activation, any new paper-model activation, geographic bypass, Phase 15, nonzero money limits, or live trading.

## Implementation shape after approval

Implementation should be incremental and test-driven:

1. add a machine-readable learning-cycle state/checkpoint;
2. count newly resolved eligible markets since the last completed cycle;
3. add a read-only readiness command that reports whether the 50-market trigger has been reached;
4. add deterministic challenger-training artifacts with exact dataset/model/config hashes;
5. evaluate challengers using chronological validation and ordinary tests without final-holdout access;
6. emit an immutable comparison report against the incumbent/baselines;
7. keep promotion disabled until a separate acceptance path is explicitly designed and approved;
8. only then design a fresh-final-holdout path for challengers that clear the ordinary-test gate.

The first implementation milestone is therefore **adaptive research training/readiness**, not live trading and not automatic model replacement.
