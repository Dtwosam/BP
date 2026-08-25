# Phase 8 Walk-Forward Backtester — Design

**Date:** 25 August 2026  
**Phase:** 8 — walk-forward backtester  
**Status:** implementation design  
**Base:** `main` after Phase 7 merge `ae3eb5e8108580cd58b8479cc7562007183a7d6d`  
**Trading mode:** RESEARCH; live prediction, paper trading, and live trading remain disabled

## Objective

Build a deterministic walk-forward backtester that evaluates the Phase 7 accepted model specification on strictly chronological data, chooses prediction timing only from information available before each test window, preserves an untouched final holdout, and reports both probabilistic quality and execution diagnostics using genuinely observed executable prices.

Phase 8 must answer a more useful question than “which final-test offset looked best?”: **if this exact evaluation procedure had been run through time, what prediction timing and out-of-sample performance would it have produced without seeing the future?**

The Phase 7 production result is the starting point, not a tuning target:

- 5m validation champion: `market_price`;
- 15m validation champion: `market_price`;
- XGBoost promotion eligibility: false for both horizons;
- accepted dataset version: `supervised-core-v1`;
- accepted feature version: `core-v1`;
- accepted label version: `official-outcome-v1`.

The attractive Phase 7 late-window 15m slices, including 0.80 accuracy at 780 seconds and 0.85 at 840 seconds on 20-market slices, are hypotheses only. Phase 8 must not hard-code either offset because those values came from the already-opened Phase 7 final test report.

## Source-of-truth constraints

The implementation must preserve the project rules already in force:

- chronological data only;
- whole markets, not feature rows, are the indivisible partition unit;
- purging/embargo where overlap could contaminate adjacent partitions;
- no random split leakage;
- no future prices or labels in predictors;
- no validation/test statistics in training preprocessing;
- no test-set threshold or timing selection;
- no repeated tuning on the same “unseen” final holdout;
- no cherry-picking only the best day/fold;
- no midpoint substitution for executable entry price;
- no synthetic historical order book when it was not observed;
- rejected/unfilled/unavailable execution opportunities must remain unavailable rather than being counted as fills;
- live trading remains disabled and trade/loss limits remain zero.

Phase 8 does **not** implement Phase 9 probability calibration or the full edge engine. It therefore does not optimize minimum trade edge, fees, slippage penalties, uncertainty penalties, or staleness penalties. It may report gross settlement P&L at a genuinely observed ask as an execution diagnostic, but it must name that result as before fees/slippage and must not present it as realistic net profitability.

## Approaches considered

### A. Rolling duration-based walk-forward with final holdout — selected

Use configurable wall-clock durations for train, validation, test, step, and final holdout windows. Markets are assigned by their actual UTC market intervals, and every feature row for a condition follows that market.

Advantages:

- directly matches the source-of-truth requirement for configurable windows;
- remains meaningful if market cadence changes or another verified horizon is added;
- supports regime change analysis because the training window can age out stale history;
- naturally models periodic retraining/re-evaluation;
- permits earlier OOS test outcomes to become legitimate historical information for later folds, as they would in real time;
- cleanly reserves a final holdout that never influences fold configuration or timing selection.

Trade-off: a short research period may contain fewer markets in some folds than a market-count design, so minimum market/class checks are required.

### B. Expanding-window walk-forward

Train on all history before each validation/test window.

Advantage: maximizes sample size.  
Rejected as the V1 default because stale regimes can dominate a short-horizon market and the project explicitly wants regime breakdown. It can be added later as a separate version if evidence justifies it rather than changing `walk-forward-v1` semantics.

### C. Fixed market-count folds

Use N train markets, M validation markets, and K test markets regardless of elapsed time.

Advantage: predictable sample counts.  
Rejected for V1 because the configuration becomes horizon/cadence-specific and is less portable when gaps, missing markets, or new horizons appear. Duration windows with explicit minimum market checks better represent deployment time.

## Version contract

Initial backtest version:

```text
walk-forward-v1
```

Initial model-spec version:

```text
phase7-market-price-v1
```

`walk-forward-v1` freezes:

- window boundary semantics;
- whole-market assignment;
- purge/embargo rules;
- candidate prediction-offset selection;
- validation objective/tie-breaks;
- final holdout handling;
- market-price baseline fallback behavior;
- execution eligibility rules;
- regime definitions;
- aggregation and uncertainty calculations;
- report/hash semantics.

Changing any of those semantics requires a new backtest version rather than silently changing an old result.

`phase7-market-price-v1` is anchored to a specific immutable Phase 7 `model_training_runs` row. The source training run supplies horizon, dataset/feature/label versions, model configuration, validation champion, and semantic provenance. V1 requires that the source run’s validation champion is `market_price`; another family fails closed instead of silently switching behavior. This is intentional because Phase 7 did not justify more complex models.

The trained Phase 7 artifact weights are **not** reused across earlier historical folds. For `market_price` no learned artifact is required: every fold fits only the training-market Up prior used as the documented fallback when `pm_up_price` is missing. This prevents future-trained weights from leaking backward.

## Data input

Reuse the existing `load_dataset` contract from Phase 7.

For each source training run, Phase 8 loads a `supervised-core-v1` snapshot for the requested `[start, end)` window using exactly the source run’s:

- horizon seconds;
- feature version;
- label version.

The dataset loader already enforces:

- static feature/label market metadata equality;
- post-market label observation;
- no label/provenance predictor keys;
- numeric-or-NULL predictor payloads;
- explicit missing flags;
- deterministic dataset SHA-256.

Phase 8 must additionally verify that the source training run exists exactly once and that its stored model configuration contains the expected `market_price` contract:

```text
predictor = pm_up_price
missing_fallback = training_prior
clip_epsilon = 1e-6
```

Any source-run/model-spec mismatch fails closed.

## Whole-market timeline

Create one canonical market record per `condition_id` from the dataset rows. All rows for a condition must agree on:

- slug;
- horizon;
- market start;
- market end;
- target.

Unique markets are ordered by:

```text
(market_start_at, condition_id)
```

No condition may appear in multiple simultaneous partitions for a fold.

A market belongs to a duration window only when its full interval is contained in that window:

```text
market_start_at >= window_start
market_end_at <= window_end
```

A boundary-crossing market is not partially admitted.

## Walk-forward windows

V1 configuration fields:

- requested dataset `start` / `end`;
- `train_duration`;
- `validation_duration`;
- `test_duration`;
- `step_duration`;
- `final_holdout_duration`;
- `embargo_markets`;
- minimum unique markets per train/validation/test partition;
- minimum class count policy;
- minimum observed market-price coverage for offset selection.

Production/research defaults for the currently accepted one-day dataset are:

```text
train_duration = 8 hours
validation_duration = 2 hours
test_duration = 2 hours
step_duration = 2 hours
final_holdout_duration = 2 hours
embargo_markets = 1
minimum_market_price_coverage = 0.80
```

These defaults are configuration, not universal truth. The report stores the exact values and a canonical config hash.

### Ordinary folds

Reserve the final holdout first. Ordinary folds are built only before the holdout.

For fold `i`:

```text
train_start_i      = requested_start + i * step
train_end_i        = train_start_i + train_duration
validation_start_i = train_end_i
validation_end_i   = validation_start_i + validation_duration
test_start_i       = validation_end_i
test_end_i         = test_start_i + test_duration
```

A fold is included only when `test_end_i <= final_holdout_start`.

Later folds may use earlier test outcomes in their training/validation history once those outcomes would have been known in real time. This is normal walk-forward behavior, not leakage.

### Final holdout

The final holdout is:

```text
[requested_end - final_holdout_duration, requested_end)
```

It is never used to:

- choose candidate offsets;
- choose model family;
- choose predictor schema;
- choose probability threshold;
- fit the fallback prior;
- define volatility regime thresholds.

For the final holdout evaluation, build a final pre-holdout train/validation context ending exactly at `final_holdout_start`. The validation window immediately before the holdout chooses the offset; the preceding rolling training window fits the fallback prior. Only after both are frozen is the holdout evaluated.

## Purging and embargo

The dataset is grouped by non-overlapping markets for one horizon, but V1 still implements explicit protection rather than assuming future datasets always share that property.

For every train→validation and validation→test/holdout boundary:

1. markets are admitted only if their complete market interval is contained in the nominal partition;
2. any market whose interval overlaps the next partition boundary is purged;
3. after purging, remove `embargo_markets` whole conditions nearest each boundary from the earlier partition;
4. record purged and embargoed condition IDs in the fold manifest;
5. assert train, validation, test/holdout, purged, and embargo sets are pairwise consistent.

V1 defaults to one whole-market embargo at each boundary, matching the conservative Phase 7 grouping policy.

## Fold validity

Each fold must satisfy configurable minimum market counts. Production defaults should be conservative enough for the one-day accepted data while still producing several folds:

- train: at least 24 unique markets;
- validation: at least 6;
- test: at least 6.

Every train, validation, and test/holdout partition must contain both target classes. A fold that does not meet the count/class contract is recorded as ineligible with its reason and is excluded from aggregate performance. The command fails if fewer than three ordinary eligible folds remain for a horizon or if the final holdout context is ineligible.

The backtester never lowers these thresholds automatically to force a report.

## Model behavior per fold

V1 supports the accepted Phase 7 `market_price` champion only.

For each fold:

1. fit `PriorBaseline` on **training markets only** with equal market weighting;
2. instantiate `MarketPriceBaseline(training_prior)`;
3. generate validation probabilities from `pm_up_price`, using the training prior only when that predictor is NULL;
4. calculate validation metrics independently at each candidate feature offset;
5. freeze one offset using the validation-only rule below;
6. evaluate the already-frozen offset on the test partition.

Because one selected offset yields at most one prediction row per market, final fold metrics are market-level rather than overweighting markets with more feature timestamps.

No model family selection occurs in Phase 8 V1. The family is fixed by the source Phase 7 training run.

## Candidate prediction offsets

Candidate offsets are derived from the feature version’s observed rows, not from final-test performance.

For a fold, an offset is validation-eligible when:

- it is strictly inside the market horizon;
- at most one row exists per condition at that offset;
- it has prediction rows for at least the configured validation market minimum;
- the fraction of validation rows with an observed (non-fallback) `pm_up_price` is at least `minimum_market_price_coverage`.

For each eligible offset, record:

- prediction market count;
- observed market-price count;
- fallback count/coverage;
- accuracy;
- balanced accuracy;
- log loss;
- Brier score;
- calibration/ECE;
- confidence coverage.

### Validation-only offset selection

Choose the fold offset by:

1. lowest validation log loss;
2. lower validation Brier score;
3. **smaller feature offset** as the deterministic final tie-break, preserving more lead time when probability quality is effectively tied.

Accuracy does not override probability-quality selection. Test or holdout metrics never participate in offset choice.

The selected offset and all validation candidate metrics are stored in the fold report so the decision is auditable.

## Test and holdout prediction coverage

After an offset is frozen, a test/holdout market may still lack the corresponding feature row. V1 does not silently substitute another offset.

Record:

- expected markets;
- predicted markets;
- missing-offset markets;
- prediction coverage.

If prediction coverage falls below 90% for a test or final holdout partition, that fold is invalid for acceptance rather than being quietly evaluated on a favorable subset.

A NULL `pm_up_price` at an existing feature row uses the source model’s documented training-prior fallback and is counted separately from a missing feature row.

## Probabilistic evaluation

Reuse the Phase 7 metric implementation on the selected one-row-per-market predictions:

- accuracy;
- balanced accuracy;
- log loss;
- Brier score;
- ECE;
- calibration buckets;
- confidence coverage.

Add a 95% Wilson interval for classification accuracy on each fold, aggregate OOS predictions, and final holdout. This is descriptive uncertainty, not a promotion threshold.

Aggregate OOS metrics are calculated by concatenating the selected predictions from all **ordinary test folds** in chronological order. Because `step_duration == test_duration` in the production default, ordinary test windows do not overlap. If a user configures overlapping test windows (`step_duration < test_duration`), V1 fails closed rather than double-counting the same market in aggregate OOS statistics.

The final holdout is reported separately and is never blended into the ordinary walk-forward aggregate headline.

## Execution diagnostic using realistic executable prices

Phase 8 satisfies the executable-price requirement without inventing Phase 9’s edge engine.

For each selected test/holdout prediction:

1. predicted side is Up when `p_up >= 0.5`, otherwise Down;
2. entry price must be the corresponding observed `pm_up_best_ask` or `pm_down_best_ask` from that exact feature row;
3. the corresponding book must not be marked missing or stale;
4. ask must be finite and satisfy `0 < ask <= 1`;
5. if any requirement fails, the market is **not executable** and is counted as no-fill/unavailable;
6. no midpoint, token-history price, opposite-side bid transformation, or synthetic ask may substitute;
7. settlement value is 1 for a correct predicted side and 0 otherwise;
8. gross P&L per hypothetical one-share purchase is `settlement - observed_ask`.

Report:

- prediction markets;
- executable markets;
- unavailable/no-fill markets;
- execution coverage;
- average observed ask;
- correct executed trades;
- gross settlement P&L before fees/slippage;
- mean gross P&L per executable share.

The field name must include `before_costs` or equivalent. Phase 8 must explicitly state that the result is **not net profitability**. Fees, slippage, latency penalties, uncertainty penalties, and minimum-edge trade filtering belong to Phase 9 and later execution phases.

A lack of historical observed asks is evidence, not a reason to create synthetic fills.

## Regime breakdown

V1 reports ordinary OOS and final-holdout metrics by deterministic regimes without selecting a different model or timing from test results.

### UTC session regime

Based only on market start time:

- `00-06`;
- `06-12`;
- `12-18`;
- `18-24` UTC.

### BTC volatility regime

Use `coinbase_realized_vol_15m` from the selected feature row.

For each fold:

- compute the volatility threshold as the median of non-NULL **training** rows at the already-selected feature offset;
- classify test/holdout rows as `low`, `high`, or `unknown`;
- never derive the threshold from validation/test/holdout values.

If the training offset has insufficient non-NULL volatility observations, the fold records volatility regime as unavailable rather than borrowing a future threshold.

### Execution-availability breakdown

Report metrics separately for:

- executable observed-book rows;
- book unavailable/stale rows.

This is a coverage breakdown, not permission to treat unavailable rows as zero-cost fills.

Regime tables must include market counts so tiny slices cannot be mistaken for stable evidence.

## Frozen report and hashing

Add immutable `backtest_runs` storage.

Natural identity is a deterministic `run_id` derived from canonical semantics rather than wall-clock time. Each stored run includes:

- run id;
- `walk-forward-v1` version;
- source training run id and semantic SHA-256;
- dataset version/SHA-256;
- feature/label versions;
- horizon;
- requested start/end;
- complete backtest configuration;
- config SHA-256;
- fold manifest hashes;
- ordinary fold reports;
- aggregate OOS metrics;
- final holdout report;
- regime breakdown;
- execution diagnostic;
- report semantic SHA-256;
- creation timestamp.

Repository semantics mirror accepted historical/feature/model registries:

- first insert creates the row;
- an identical semantic rerun with the same deterministic `run_id` returns existing and preserves original `created_at`;
- changed semantics under the same `run_id` fail closed.

The semantic hash excludes only wall-clock creation metadata and operator output-file path.

## Backtest output file

The CLI writes a canonical JSON report atomically to an operator-specified file or directory. The JSON contains a list sorted by horizon when multiple source training runs are supplied.

An immediate rerun against unchanged PostgreSQL data and the same arguments must produce:

- identical dataset SHA-256;
- identical config SHA-256;
- identical fold membership hashes;
- identical selected validation offsets;
- identical probabilistic metrics;
- identical regime metrics;
- identical execution eligibility/P&L;
- identical semantic report SHA-256;
- no new `backtest_runs` row.

Creation timestamps may differ in CLI output but are excluded from semantic equality.

## CLI

Add:

```text
scripts/run_walk_forward_backtest.py
```

Primary command shape:

```bash
python scripts/run_walk_forward_backtest.py \
  --start 2026-08-24T00:00:00Z \
  --end 2026-08-25T00:00:00Z \
  --source-training-run-id phase7-300-0a822e17ceced11742bf6d3bc8214f44 \
  --source-training-run-id phase7-900-e36d978aecc29816c5b9e2b67b30d6e2 \
  --train-hours 8 \
  --validation-hours 2 \
  --test-hours 2 \
  --step-hours 2 \
  --final-holdout-hours 2 \
  --embargo-markets 1 \
  --min-market-price-coverage 0.80 \
  --env-file /etc/bp/bp.env \
  --output-dir /var/lib/bp/artifacts/phase8-backtests
```

Defaults may match the documented production values, but every resolved value is written into the report.

The command is offline with respect to market APIs. It may read PostgreSQL and write the report/immutable registry, but it must not fetch current market data, place orders, enable prediction services, or mutate live-trading configuration.

## Package structure

Create a focused package rather than overloading Phase 7 modeling files:

```text
src/bp_engine/backtesting/
  __init__.py
  models.py       # immutable config/fold/report dataclasses
  folds.py        # market timeline + duration windows + purge/embargo
  predictor.py    # Phase 7 market-price spec adapter/fallback prior
  selection.py    # validation-only offset selection
  execution.py    # observed-ask eligibility + gross before-costs P&L
  regimes.py      # UTC/volatility/availability breakdown
  repository.py   # immutable backtest_runs store
  service.py      # orchestration + hashes + aggregate/holdout report
  cli.py          # argument parsing, DB transaction, atomic JSON output
scripts/run_walk_forward_backtest.py
migrations/0008_backtest_runs.sql
```

Reuse Phase 7 dataset and metric primitives where their semantics already match. Do not duplicate metric formulas.

## Error handling

Fail closed for:

- timezone-naive requested boundaries;
- non-positive durations;
- `step_duration < test_duration` in V1;
- final holdout too large for one valid train/validation context;
- fewer than three eligible ordinary folds;
- source training run missing or duplicated;
- source run horizon/version mismatch;
- source run validation champion not `market_price` in V1;
- changed expected market-price model config;
- static market metadata disagreement within a condition;
- condition overlap across simultaneous partitions;
- overlapping ordinary test windows;
- train/validation/test/holdout single-class partitions;
- insufficient market counts;
- no validation-eligible prediction offset;
- test/holdout prediction coverage below 90%;
- non-finite probabilities or metrics;
- executable ask outside `(0, 1]`;
- semantic conflict in immutable backtest registry.

Missing observed executable prices are **not** an exception; they are explicit no-fill/unavailable outcomes.

## Testing strategy

Use TDD for every implementation slice.

### Fold construction

- duration window boundaries;
- whole-market containment;
- deterministic ordering;
- purge overlapping market intervals;
- one-market embargo;
- pairwise partition disjointness;
- rolling step advancement;
- rejection of overlapping ordinary test windows;
- final holdout isolation;
- minimum market/class eligibility.

### Offset selection

- candidates derived from validation only;
- fallback coverage measured correctly;
- coverage threshold enforced;
- log-loss then Brier then earlier-offset tie-break;
- perturbing test probabilities cannot change selected offset;
- perturbing final holdout cannot change any ordinary fold configuration.

### Prediction/model provenance

- source `model_training_runs` row required;
- horizon/feature/label/model config must match;
- accepted `market_price` champion works;
- unsupported champion fails closed;
- prior fallback fitted from training only;
- test target perturbation does not alter probabilities.

### Execution

- uses selected-side observed best ask only;
- stale/missing book means unavailable/no fill;
- midpoint is never substituted;
- price-history token price is never substituted;
- invalid ask fails closed;
- gross payout/P&L arithmetic;
- coverage and no-fill counts.

### Regimes

- UTC session assignment;
- volatility median fitted on training only;
- test volatility perturbation does not change threshold;
- unknown volatility remains explicit;
- regime count parity with evaluated markets.

### Hashing and persistence

- config hash determinism;
- fold membership hash determinism;
- semantic report hash determinism;
- identical registry rerun returns existing without rewriting `created_at`;
- semantic conflict fails closed;
- PostgreSQL migration/integration.

### CLI

- timezone validation;
- repeated source run IDs;
- offline/no HTTP or WebSocket imports;
- deterministic JSON semantics;
- atomic report write;
- one command can evaluate both accepted 5m and 15m source runs.

## Production acceptance

Run Phase 8 acceptance on `bp-recorder` using the already accepted Phase 7 one-day research data first. Do not automatically widen the historical window merely to make metrics look better.

The host gate must verify before execution:

- exact candidate HEAD;
- all normal exact-head GitHub gates passed;
- `LIVE_TRADING_ENABLED=false`;
- `MAX_TRADE_SIZE_USD=0`;
- `MAX_DAILY_LOSS_USD=0`;
- recorder active;
- disk status `ok`;
- both Phase 7 source training run IDs exist and match their accepted semantic hashes.

Run the backtest command twice with the same fixed one-day window and production defaults.

Acceptance requires for both horizons:

- at least three eligible ordinary walk-forward folds;
- one valid final holdout;
- zero simultaneous condition overlap;
- zero overlapping ordinary test-market reuse;
- both classes in every evaluated partition;
- validation-selected offsets determined without test/holdout input;
- test/holdout prediction coverage at least 90%;
- finite probability metrics;
- execution rows use only observed non-stale selected-side asks;
- missing executable asks remain no-fill/unavailable;
- deterministic second run: identical dataset/config/fold/offset/report hashes;
- immutable registry second run creates zero rows;
- output identifies aggregate OOS and final holdout separately;
- output includes UTC, volatility, and execution-availability regime counts;
- disk remains `ok` and recorder active afterward;
- live trading and trade/loss limits remain unchanged.

The acceptance result may show poor accuracy, poor execution coverage, or negative gross before-costs P&L. Those are valid research outcomes and must not be hidden or patched away. Phase 8 closes when the backtester is reproducible and leakage-safe, not when it produces a profitable-looking number.

## Phase boundary

Phase 9 — probability calibration + edge engine — remains blocked until:

- Phase 8 production acceptance passes;
- durable Phase 8 closeout evidence is committed;
- the Phase 8 closeout HEAD passes the complete exact-head GitHub gate set;
- the Phase 8 PR is merged with an expected-head guard;
- `main` is verified after merge.

Phase 8 must not add live prediction services, paper-order execution, live execution, wallet credentials, or real-money permissions. Live trading remains disabled.
