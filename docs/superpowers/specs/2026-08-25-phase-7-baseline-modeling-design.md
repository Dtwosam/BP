# Phase 7 Baseline Modeling — Design

**Date:** 25 August 2026  
**Phase:** 7 — baselines before fancy ML  
**Status:** implementation design  
**Trading mode:** RESEARCH; live trading remains disabled

## Goal

Build a deterministic, leakage-safe supervised-learning pipeline that joins frozen Phase 6 `core-v1` features to Phase 5 `official-outcome-v1` labels only after feature generation, trains separate 5-minute and 15-minute model families, evaluates simple baselines before boosted trees, and emits immutable evidence showing exactly which data, split, features, model configuration, and metrics produced a result.

Phase 7 is not a trading strategy, backtester, calibration/edge engine, or live predictor. It may compute a limited gross executable-price diagnostic where a real observed ask exists, but it must not treat that diagnostic as realistic net P&L or use it to promote a model.

## Source-of-truth constraints

The implementation must preserve these existing project rules:

- official resolved Polymarket outcome is the target;
- labels never enter feature payloads;
- feature rows are immutable and versioned;
- 5m and 15m models are trained separately;
- chronological evaluation is mandatory;
- random row shuffles are forbidden;
- overlapping/correlated market rows cannot cross split boundaries;
- final test data cannot be used to tune thresholds, select features, select models, or fit preprocessing;
- complex models must not be preferred merely because they are complex;
- calibration, coverage, accuracy, log loss, Brier score, and execution realism matter alongside headline accuracy;
- live trading remains disabled.

## Key modeling choice: split by market, never by feature row

A single market has several `core-v1` feature timestamps but one eventual outcome. Splitting those rows independently would leak the same label and market path across train and test.

Phase 7 therefore treats `condition_id` as the indivisible grouping key. Unique markets are ordered by `(market_start_at, condition_id)` and assigned chronologically to train, validation, embargo, and test. Every feature row for one condition follows its market into exactly one split.

Initial split contract is `chronological-market-v1`:

- 60% of eligible markets: train;
- 20%: validation;
- 20%: final test;
- one whole market is embargoed at each train/validation and validation/test boundary when the split is large enough;
- split ratios operate on unique markets, not feature-row count;
- all split membership is deterministic and stored in the report.

The builder fails closed if any condition appears in more than one split or if the training split contains only one class. Production acceptance additionally requires both classes in validation and test.

## Dataset contract

Dataset version is `supervised-core-v1`.

The dataset builder reads only:

From `market_features`:

- condition id;
- slug;
- horizon;
- market start/end;
- feature timestamp and offset;
- feature version;
- `features`;
- `missing_flags`;
- feature hash/input fingerprint.

From `market_labels`:

- condition id;
- horizon;
- market start/end;
- official outcome;
- label version;
- source-observed timestamp.

Label resolution metadata, rule text, reference prices, label source hash, or other post-resolution fields are not model features.

For every joined condition, horizon and market window must match exactly across feature and label records. `source_observed_at` must be at or after market end. Duplicate natural keys or mismatched static metadata fail closed.

The target is binary:

- Up = 1;
- Down = 0.

Every `features` value must be numeric or NULL. Every `missing_flags` value becomes a stable binary predictor with a `missing__` prefix. Metadata identifiers, timestamps, hashes, source cutoffs, generated timestamps, and the label are never predictor columns.

The feature and missing-flag key sets must be identical across all rows for one feature version. A missing key is a schema error, not silently imputed.

Each market contributes equal total sample weight. Row weight is `1 / rows_for_condition` so a market with more feature timestamps cannot dominate another market if cadence changes in a future feature version.

A canonical dataset SHA-256 covers sorted row identities, feature hashes, label targets, versions, and split-independent static metadata. The raw label is included in the dataset manifest hash but never in predictor data.

## Missing-data and preprocessing contract

Missingness is signal and must remain explicit.

For learned models:

- preprocessing is fitted only on the training split;
- numeric NULLs are median-imputed using training data only;
- predictors that are entirely NULL in training are dropped and recorded in the manifest;
- missing flags remain explicit binary columns;
- logistic regression standardization is fitted only on training data;
- no validation/test statistic may affect imputation, scaling, or feature selection.

The fixed market-price baseline does not use the learned preprocessing pipeline.

## Initial model ladder

### 1. Naive prior baseline

Fit only the weighted Up-class prior on training markets and predict that constant probability for validation/test rows. Accuracy uses the corresponding majority side. This is the minimum sanity baseline.

### 2. Polymarket price baseline

Use `pm_up_price` as the market-implied Up probability, clipped only for numerical metric stability. If the field is unavailable on a row, fall back to the training prior and record baseline fallback coverage.

This baseline is predictive, not an execution price. It is never presented as a tradable ask/bid.

### 3. Logistic regression

Use scikit-learn logistic regression with deterministic settings, training-only median imputation and scaling, explicit sample weights, and no hyperparameter search in V1.

### 4. XGBoost challenger

Use a small deterministic XGBoost classifier with fixed conservative parameters, one CPU thread, and no test-set tuning. V1 intentionally avoids a hyperparameter sweep. Complexity is justified only if evidence beats the simpler baselines.

Sequence models, deep learning, ensembles, and automated hyperparameter search are out of scope for Phase 7.

## Model selection and untouched test policy

The test set is opened only after model selection is frozen.

For each horizon:

1. fit learned models on train only;
2. evaluate naive, market-price, logistic, and XGBoost on validation;
3. select the `validation_champion` using lowest validation log loss, with Brier score as first tie-break and balanced accuracy as second tie-break;
4. freeze the champion identity and any fixed decision threshold before reading final test metrics;
5. evaluate every already-frozen candidate on test for comparison, but do not change the champion based on test results.

The report must distinguish `validation_champion` from `best_test_result` so a favorable test result cannot silently rewrite the selected model.

XGBoost receives `boosted_promotion_eligible=true` only when it beats the strongest simple baseline on validation log loss and Brier score and confirms lower test log loss without a worse test Brier score. If it does not, the boosted model remains a recorded challenger and no more complex model work is justified.

Phase 7 may still close with a simple champion; the build-order rule means complexity stops, not that a weaker boosted model is promoted.

## Metrics

For validation and final test, record:

- weighted accuracy;
- weighted balanced accuracy;
- weighted log loss;
- weighted Brier score;
- Up/Down class counts by unique market and row;
- calibration buckets using the source-of-truth ranges from 50–55% through 90%+;
- expected calibration error (ECE) as a summary only;
- confidence coverage and accuracy at 55%, 60%, 65%, 70%, 75%, 80%, 85%, and 90% confidence;
- metrics by feature offset so later work can study prediction timing without regenerating features;
- market-price baseline fallback coverage;
- executable-ask coverage for the limited gross execution diagnostic.

Balanced accuracy is computed from class recall and remains explicitly undefined rather than fabricated when a split has only one class.

## Limited Phase 7 execution diagnostic

Phase 7 does not implement Phase 8's realistic backtester.

Where both the selected side's fresh observed best ask and eventual outcome are available, the report may compute a **gross settlement P&L before fees/slippage/latency**:

- buy one $1-settlement share at the observed ask;
- gross P&L = settlement value minus ask;
- no midpoint substitution;
- no synthetic ask when book data are absent;
- missing execution rows remain excluded with explicit coverage.

This number is diagnostic only, is named `gross_execution_pnl_before_costs`, and is never used for model selection or promotion.

## Artifact and registry contract

Phase 7 adds an immutable `model_training_runs` registry. Each row stores:

- deterministic run id;
- dataset/split versions and SHA-256;
- feature/label versions;
- horizon;
- requested data window;
- train/validation/test market and row counts;
- selected predictor names and dropped-all-missing names;
- model-family configurations;
- validation champion;
- metrics/report JSON;
- artifact manifest and SHA-256 values;
- created timestamp.

An identical run id with identical semantics is existing/no-op. Changed semantics at the same run id fail closed.

Model binaries are written to an operator-specified artifact directory, not committed to Git. Each binary receives a SHA-256 recorded in the registry/report. Training evidence must therefore identify both the immutable database report and the exact external model artifact.

Run identity is derived from dataset hash, split version/configuration, horizon, feature/label versions, predictor schema, and model configurations. Wall-clock creation time is excluded from semantic identity.

## CLI

Add `scripts/train_baselines.py` with explicit UTC window arguments and no network access during training.

Example:

```bash
python scripts/train_baselines.py \
  --start 2026-08-24T00:00:00Z \
  --end 2026-08-25T00:00:00Z \
  --feature-version core-v1 \
  --label-version official-outcome-v1 \
  --output-dir /var/lib/bp/models \
  --env-file /etc/bp/bp.env
```

Default horizons are 300 and 900 seconds. A horizon can be selected explicitly for focused research.

CLI output is deterministic semantic JSON except for clearly separated creation timestamps/artifact paths. Failures exit non-zero and do not partially register a successful run.

## Production data window and minimum evidence

The Phase 6 acceptance hour contains only 16 markets and is not enough to support a meaningful Phase 7 model comparison, especially for the 15-minute horizon.

Phase 7 production acceptance therefore expands the historical research dataset using the already-accepted Phase 4 → Phase 5 → Phase 6 pipelines over the fixed full UTC day:

`2026-08-24T00:00:00Z <= market_start_at < 2026-08-25T00:00:00Z`

The acceptance helper may re-run those immutable/idempotent pipelines. It must never rewrite previously accepted rows. Bybit's documented host HTTP 403 remains an audited optional-source limitation; Polymarket and Coinbase remain mandatory core historical inputs.

Minimum eligible unique-market counts for model acceptance are deliberately conservative rather than equal to expected market counts:

- 5m: at least 100 unique labeled markets;
- 15m: at least 30 unique labeled markets.

If the source window cannot meet those counts, Phase 7 acceptance stops with insufficient-data evidence rather than training on an obviously tiny sample.

## Production acceptance gates

A valid Phase 7 host acceptance must prove:

1. exact candidate HEAD and all normal GitHub gates pass before host acceptance;
2. live trading is false and trade/loss limits remain zero;
3. recorder is active before and after the research run;
4. disk status is `ok` before expensive data expansion/training begins;
5. Phase 4 historical expansion completes or returns only the already-defined audited Bybit unavailable state;
6. Phase 5 labels are deterministic/idempotent for the fixed window;
7. Phase 6 features are deterministic/idempotent for the fixed window;
8. minimum unique-market counts are met for both 5m and 15m;
9. feature/label static metadata matches for every training condition;
10. no condition crosses train/validation/test/embargo boundaries;
11. train/validation/test each contain both classes for both horizons;
12. training rerun produces the same dataset hash, split membership, model configurations, validation champion, and semantic metric report;
13. every metric is finite where defined and no undefined metric is coerced to a favorable number;
14. model artifact hashes exist for learned models;
15. model-training registry rerun is existing/no-op for identical semantic runs;
16. report identifies whether XGBoost earned promotion eligibility rather than assuming it did;
17. final project evidence records the champion selected on validation and untouched test performance separately;
18. live trading remains disabled throughout.

## Failure policy

Fail closed on:

- random or row-level split leakage;
- one market appearing in multiple splits;
- label/static metadata mismatch;
- post-resolution fields entering predictor columns;
- validation/test statistics entering preprocessing;
- single-class training data;
- insufficient unique-market coverage;
- non-finite model probabilities or metrics;
- changed immutable source rows;
- training registry semantic conflicts;
- missing artifact hashes;
- test-set-based model selection;
- storage warning/critical status;
- recorder regression;
- safety-setting regression.

Do not delete data, lower storage thresholds, synthesize missing market depth, route around source restrictions, or loosen leakage checks to force acceptance.

## Dependencies

Add:

- scikit-learn for logistic regression, preprocessing, and reference metrics;
- XGBoost for the first boosted-tree challenger;
- joblib for explicit model artifact serialization.

Keep versions bounded in `pyproject.toml`. Deterministic tests must not depend on network access.

## Testing strategy

Use TDD for every implementation slice.

Required test groups:

- dataset join/static-contract failures;
- predictor-schema and forbidden-key tests;
- market-group chronological split tests;
- embargo and no-condition-crossing tests;
- training-only preprocessing tests;
- equal-market sample-weight tests;
- dataset/run hash determinism tests;
- naive and market-price baseline tests;
- logistic model smoke/determinism tests;
- XGBoost smoke/determinism tests;
- metric/calibration/coverage tests;
- validation-champion/test-isolation tests;
- boosted promotion rule tests;
- gross executable-ask diagnostic coverage tests;
- immutable training-run registry tests;
- PostgreSQL integration tests;
- CLI tests;
- deployment/acceptance helper syntax and contract tests.

## Phase boundary

Phase 8 walk-forward backtesting remains blocked until Phase 7 production acceptance is recorded, the closeout HEAD passes the full exact-head GitHub gate set, the Phase 7 PR is merged with an expected-head guard, and `main` is verified after merge.

Live prediction, paper trading, and live trading remain blocked by later phases. Live trading remains disabled.
