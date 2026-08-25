# Phase 8 Walk-Forward Backtester Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, leakage-safe `walk-forward-v1` backtester for the accepted Phase 7 `market_price` champion, with validation-only timing selection, a final untouched holdout, observed-ask execution diagnostics, regime breakdowns, immutable run storage, and one-command reproducibility.

**Architecture:** Add a focused `bp_engine.backtesting` package that reuses the accepted Phase 7 dataset/metric primitives but owns walk-forward fold construction, model-spec provenance, validation-only offset selection, execution eligibility, regimes, report hashing, persistence, and CLI orchestration. V1 accepts only Phase 7 training runs whose validation champion is `market_price`; each fold refits only the training prior fallback and never reuses future-trained artifact weights.

**Tech Stack:** Python 3.12+, SQLAlchemy 2, PostgreSQL 16, existing scikit-learn-independent Phase 7 baseline/metric primitives, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-25-phase-8-walk-forward-backtester-design.md`

## Global Constraints

- Backtest version is exactly `walk-forward-v1`.
- Model-spec version is exactly `phase7-market-price-v1`.
- Dataset version remains `supervised-core-v1`; feature version and label version come from the immutable source training run.
- Whole `condition_id` markets are the partition unit; random row shuffles are forbidden.
- Production defaults: train 8h, validation 2h, test 2h, step 2h, final holdout 2h, one-market embargo, minimum market-price coverage 0.80.
- V1 rejects `step_duration < test_duration` to prevent ordinary OOS market reuse.
- V1 requires at least 24 train markets, 6 validation markets, 6 test/holdout markets, both classes in every evaluated partition, at least three eligible ordinary folds, and at least 90% test/holdout prediction coverage.
- Offset selection is validation-only: lowest log loss, then Brier score, then smaller feature offset.
- Execution uses only the exact selected-side observed non-stale best ask; no midpoint, token-history price, inferred bid transform, or synthetic historical book.
- Execution P&L is `gross_execution_pnl_before_costs`, never net profitability.
- Final holdout is reported separately and never influences timing/model configuration.
- CLI has no market API/network dependency and must not change live-trading configuration.
- Live prediction, paper trading, and live trading remain blocked; `LIVE_TRADING_ENABLED=false` and trade/loss limits remain zero.

---

### Task 1: Backtesting Types and Fold Timeline

**Files:**
- Create: `src/bp_engine/backtesting/__init__.py`
- Create: `src/bp_engine/backtesting/models.py`
- Create: `src/bp_engine/backtesting/folds.py`
- Create: `tests/backtesting/test_fold_builder.py`

**Interfaces:**
- Consumes: `bp_engine.modeling.models.DatasetSnapshot`, `SupervisedRow`.
- Produces:
  - `BACKTEST_VERSION = "walk-forward-v1"`
  - `MODEL_SPEC_VERSION = "phase7-market-price-v1"`
  - `WalkForwardConfig`
  - `MarketRecord`
  - `FoldPartition`
  - `WalkForwardFold`
  - `WalkForwardPlan`
  - `build_market_timeline(dataset: DatasetSnapshot) -> tuple[MarketRecord, ...]`
  - `build_walk_forward_plan(dataset: DatasetSnapshot, config: WalkForwardConfig) -> WalkForwardPlan`

- [ ] **Step 1: Write failing tests for whole-market timeline and duration validation**

Create tests that construct a `DatasetSnapshot` with repeated feature offsets per condition and assert:

```python
config = WalkForwardConfig(
    train_duration=timedelta(hours=8),
    validation_duration=timedelta(hours=2),
    test_duration=timedelta(hours=2),
    step_duration=timedelta(hours=2),
    final_holdout_duration=timedelta(hours=2),
    embargo_markets=1,
    min_train_markets=24,
    min_validation_markets=6,
    min_test_markets=6,
    min_market_price_coverage=0.80,
    min_prediction_coverage=0.90,
)
assert build_market_timeline(dataset)[0].condition_id == "condition-000"
```

Also assert duplicate rows for one condition with inconsistent target/window raise `ValueError`, every duration must be positive, and `step_duration < test_duration` raises `ValueError("step_duration must be at least test_duration")`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
pytest tests/backtesting/test_fold_builder.py -v
```

Expected: import/module failures because `bp_engine.backtesting` does not exist.

- [ ] **Step 3: Implement frozen dataclasses and canonical market timeline**

Define in `models.py`:

```python
BACKTEST_VERSION = "walk-forward-v1"
MODEL_SPEC_VERSION = "phase7-market-price-v1"

@dataclass(frozen=True)
class WalkForwardConfig:
    train_duration: timedelta
    validation_duration: timedelta
    test_duration: timedelta
    step_duration: timedelta
    final_holdout_duration: timedelta
    embargo_markets: int = 1
    min_train_markets: int = 24
    min_validation_markets: int = 6
    min_test_markets: int = 6
    min_market_price_coverage: float = 0.80
    min_prediction_coverage: float = 0.90

@dataclass(frozen=True)
class MarketRecord:
    condition_id: str
    slug: str
    horizon_seconds: int
    market_start_at: datetime
    market_end_at: datetime
    target: int

@dataclass(frozen=True)
class FoldPartition:
    name: str
    start: datetime
    end: datetime
    condition_ids: tuple[str, ...]

@dataclass(frozen=True)
class WalkForwardFold:
    index: int
    train: FoldPartition
    validation: FoldPartition
    test: FoldPartition
    purged_condition_ids: tuple[str, ...]
    embargo_condition_ids: tuple[str, ...]
    membership_sha256: str

@dataclass(frozen=True)
class WalkForwardPlan:
    folds: tuple[WalkForwardFold, ...]
    final_train: FoldPartition
    final_validation: FoldPartition
    final_holdout: FoldPartition
    final_purged_condition_ids: tuple[str, ...]
    final_embargo_condition_ids: tuple[str, ...]
    plan_sha256: str
```

`build_market_timeline()` groups by condition, validates static equality, and sorts `(market_start_at, condition_id)`.

- [ ] **Step 4: Add failing tests for purge/embargo, holdout isolation, class/count rules, and non-overlapping ordinary tests**

Use a synthetic 24h regular 15m timeline and assert:

```python
plan = build_walk_forward_plan(dataset, config)
assert len(plan.folds) >= 3
assert plan.final_holdout.end == dataset.end
assert plan.final_holdout.start == dataset.end - timedelta(hours=2)
for fold in plan.folds:
    assert set(fold.train.condition_ids).isdisjoint(fold.validation.condition_ids)
    assert set(fold.validation.condition_ids).isdisjoint(fold.test.condition_ids)
    assert set(fold.train.condition_ids).isdisjoint(fold.test.condition_ids)
```

Add a market whose interval crosses a nominal boundary and assert it is purged, plus a test that a one-class partition is marked invalid/raises the documented fold eligibility error rather than lowering thresholds.

- [ ] **Step 5: Implement duration windows, full-interval containment, purge and one-market embargo**

Use canonical UTC boundaries. Include a market only when `market_start_at >= start and market_end_at <= end`. Purge boundary-crossing markets; then remove the configured number of nearest earlier-partition markets at train→validation and validation→test/holdout boundaries. Hash ordered condition memberships and boundaries with existing `canonical_hash`.

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
pytest tests/backtesting/test_fold_builder.py -v
ruff check src/bp_engine/backtesting tests/backtesting/test_fold_builder.py
```

Expected: PASS.

Commit:

```bash
git add src/bp_engine/backtesting tests/backtesting/test_fold_builder.py
git commit -m "feat: add walk-forward fold planner"
```

---

### Task 2: Source Training-Run Provenance and Market-Price Predictor

**Files:**
- Create: `src/bp_engine/backtesting/predictor.py`
- Create: `tests/backtesting/test_predictor.py`
- Modify: `src/bp_engine/modeling/repository.py`
- Test: `tests/modeling/test_model_run_repository.py`

**Interfaces:**
- Consumes: immutable `model_training_runs` rows, `PriorBaseline`, `MarketPriceBaseline`, `SupervisedRow`.
- Produces:
  - `ModelSpec`
  - `MarketPriceFoldPredictor`
  - `load_model_spec(connection: Connection, run_id: str) -> ModelSpec`
  - `MarketPriceFoldPredictor.fit(rows: tuple[SupervisedRow, ...]) -> None`
  - `MarketPriceFoldPredictor.predict(rows: tuple[SupervisedRow, ...]) -> tuple[float, ...]`
  - `MarketPriceFoldPredictor.observed_price_coverage(rows: tuple[SupervisedRow, ...]) -> float`

- [ ] **Step 1: Write RED tests for immutable training-run lookup**

Add a repository read test requiring an exact run id and assert the returned row preserves `validation_champion`, `horizon_seconds`, versions, model configs and semantic SHA. Missing run id must raise a typed `SourceTrainingRunNotFound` from the backtesting adapter.

- [ ] **Step 2: Write RED predictor provenance tests**

Assert `load_model_spec()` accepts only:

```python
validation_champion == "market_price"
model_configs["market_price"] == {
    "predictor": "pm_up_price",
    "missing_fallback": "training_prior",
    "clip_epsilon": 1e-6,
}
```

Wrong champion/config/version must fail closed.

- [ ] **Step 3: Implement repository `get()` and `ModelSpec` validation**

Add a read-only method:

```python
def get(self, connection: Connection, run_id: str) -> Mapping[str, Any] | None:
    ...
```

`ModelSpec` stores run id, semantic hash, horizon, dataset/feature/label/split versions, champion, and market-price config.

- [ ] **Step 4: Write RED tests proving training-only prior fallback**

Construct train rows with known class prior and validation/test rows where `pm_up_price=None`. Assert the fallback equals the training prior. Change validation/test targets and assert probabilities do not change.

- [ ] **Step 5: Implement `MarketPriceFoldPredictor` by composing Phase 7 baselines**

Fit `PriorBaseline` with `equal_market_weights(train_rows)`, then instantiate `MarketPriceBaseline(prior.probability)`. Prediction must remain clipped by the accepted baseline implementation; observed coverage is the fraction with non-NULL `pm_up_price`.

- [ ] **Step 6: Verify and commit**

Run:

```bash
pytest tests/backtesting/test_predictor.py tests/modeling/test_model_run_repository.py -v
ruff check src/bp_engine/backtesting/predictor.py src/bp_engine/modeling/repository.py tests/backtesting/test_predictor.py
```

Commit message:

```text
feat: anchor backtests to Phase 7 model specs
```

---

### Task 3: Validation-Only Offset Selection

**Files:**
- Create: `src/bp_engine/backtesting/selection.py`
- Create: `tests/backtesting/test_offset_selection.py`

**Interfaces:**
- Consumes: one fold’s train/validation rows and `MarketPriceFoldPredictor`.
- Produces:
  - `OffsetCandidateReport`
  - `OffsetSelection`
  - `select_validation_offset(train_rows, validation_rows, *, min_market_price_coverage: float, min_validation_markets: int) -> OffsetSelection`
  - `rows_at_offset(rows, offset_seconds) -> tuple[SupervisedRow, ...]`

- [ ] **Step 1: Write RED candidate/coverage tests**

Create validation rows at offsets 60/120/180 with different `pm_up_price` missingness. Assert an offset below 80% observed coverage is excluded even if its fallback predictions happen to score well.

- [ ] **Step 2: Write RED selection-order tests**

Use probabilities/targets where offset A has lower log loss than B, B has higher accuracy, and assert A wins. Add exact log-loss tie then Brier tie and assert smaller `feature_offset_seconds` wins.

- [ ] **Step 3: Write RED leakage perturbation test**

Select an offset from train+validation, mutate every test/holdout target and predictor, rerun selection with unchanged train+validation, and assert the selection object is byte-for-byte semantically equal.

- [ ] **Step 4: Implement offset grouping and selection**

For each validation offset, require at most one row per condition and `market_count >= min_validation_markets`. Fit one fold predictor from train rows, predict validation rows, record `evaluate_probabilities()` plus observed/fallback counts, then choose by `(log_loss, brier_score, offset_seconds)`.

- [ ] **Step 5: Verify and commit**

Run:

```bash
pytest tests/backtesting/test_offset_selection.py -v
ruff check src/bp_engine/backtesting/selection.py tests/backtesting/test_offset_selection.py
```

Commit:

```text
feat: select prediction timing from validation only
```

---

### Task 4: Execution Diagnostics, Regimes, and Accuracy Uncertainty

**Files:**
- Create: `src/bp_engine/backtesting/execution.py`
- Create: `src/bp_engine/backtesting/regimes.py`
- Create: `src/bp_engine/backtesting/uncertainty.py`
- Create: `tests/backtesting/test_execution.py`
- Create: `tests/backtesting/test_regimes.py`
- Create: `tests/backtesting/test_uncertainty.py`

**Interfaces:**
- Produces:
  - `execution_diagnostic(rows, probabilities) -> dict[str, float | int]`
  - `utc_session_regime(row: SupervisedRow) -> str`
  - `training_volatility_threshold(train_rows, offset_seconds) -> float | None`
  - `volatility_regime(row, threshold) -> str`
  - `regime_metrics(rows, probabilities, *, volatility_threshold) -> dict[str, Any]`
  - `wilson_accuracy_interval(correct: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]`

- [ ] **Step 1: Write RED execution tests**

Assert predicted Up uses only `pm_up_best_ask`, predicted Down only `pm_down_best_ask`. `missing__pm_up_book_missing`, `missing__pm_up_book_stale`, corresponding Down flags, NULL ask, or invalid ask make the row unavailable. Assert midpoint/`pm_up_price` are never fallback execution prices.

- [ ] **Step 2: Implement observed-ask execution arithmetic**

For each executable row:

```python
predicted = 1 if p_up >= 0.5 else 0
payout = 1.0 if row.target == predicted else 0.0
gross = payout - observed_selected_side_ask
```

Return counts, coverage, average ask, correct count, `gross_execution_pnl_before_costs`, and mean gross P&L per executed share.

- [ ] **Step 3: Write RED regime tests**

Assert UTC buckets are exactly `00-06`, `06-12`, `12-18`, `18-24`. For volatility, construct train/test rows and prove test-value perturbation cannot change the train median threshold. NULL volatility must produce `unknown`.

- [ ] **Step 4: Implement regime helpers and count parity**

Use `coinbase_realized_vol_15m` at the selected offset. Regime reports include market counts and Phase 7 probability metrics for groups with enough rows; undefined balanced accuracy remains `None` when a group contains one class.

- [ ] **Step 5: Write/implement Wilson interval tests**

Test known boundaries: `total=0` raises; `correct=0,total=10` stays in `[0,1]`; `correct=10,total=10` stays in `[0,1]`; `correct=8,total=10` contains `0.8`.

- [ ] **Step 6: Verify and commit**

Run all three test files and Ruff. Commit:

```text
feat: add executable-price and regime diagnostics
```

---

### Task 5: Immutable Backtest Registry

**Files:**
- Create: `migrations/0008_backtest_runs.sql`
- Modify: `src/bp_engine/storage/schema.py`
- Create: `src/bp_engine/backtesting/repository.py`
- Create: `tests/backtesting/test_backtest_repository.py`
- Create: `tests/backtesting/test_postgres_backtests.py`

**Interfaces:**
- Produces:
  - SQLAlchemy `backtest_runs` table
  - `BacktestRunRepository.store(connection, report: BacktestReport) -> BacktestStoreResult`
  - `BacktestRunConflict`

- [ ] **Step 1: Write RED schema/repository tests**

Require columns for run/version/source-run/dataset/config/fold/report hashes, horizon/window, report JSON, and `created_at`. First store creates, identical semantic rerun returns existing, changed semantic content under same run id raises `BacktestRunConflict`, and original `created_at` is preserved.

- [ ] **Step 2: Implement additive migration/schema**

Use `run_id` primary/unique identity, positive horizon/window checks, 64-char SHA fields where appropriate, JSONB config/report on PostgreSQL, and an index on `(horizon_seconds, created_at)`.

- [ ] **Step 3: Implement semantic normalization/store**

Normalize aware datetimes to UTC/Z and tuple/list representations exactly as the Phase 7 model registry does. Exclude only creation metadata from semantic comparison.

- [ ] **Step 4: Add PostgreSQL integration test**

Under `BP_TEST_DATABASE_URL`, apply/create schema and prove create/existing/conflict semantics against PostgreSQL 16.

- [ ] **Step 5: Verify and commit**

Run repository/unit/integration tests plus Ruff. Commit:

```text
feat: persist immutable walk-forward runs
```

---

### Task 6: Walk-Forward Service and Frozen Report Hashing

**Files:**
- Create: `src/bp_engine/backtesting/service.py`
- Expand: `src/bp_engine/backtesting/models.py`
- Create: `tests/backtesting/test_backtest_service.py`

**Interfaces:**
- Produces:
  - `FoldEvaluationReport`
  - `FinalHoldoutReport`
  - `BacktestReport`
  - `run_walk_forward_backtest(connection, *, source_training_run_id: str, start: datetime, end: datetime, config: WalkForwardConfig, created_at: datetime) -> BacktestReport`

- [ ] **Step 1: Write RED ordinary-fold orchestration test**

Use an in-memory deterministic dataset fixture and monkeypatch only the dataset loader/source-run reader boundary. Assert at least three fold reports, every selected test condition is in exactly one ordinary test report, and each fold records validation candidates + selected offset + membership hash.

- [ ] **Step 2: Write RED final-holdout isolation test**

Run once, mutate final-holdout predictors/targets, rerun, and assert all ordinary fold boundaries, validation candidates, selected offsets, and ordinary aggregate membership hashes are unchanged. Final holdout metrics may change.

- [ ] **Step 3: Write RED test-prediction coverage and no-offset-substitution tests**

Delete selected-offset rows from >10% of a fold’s test markets and assert the fold fails with prediction coverage below 90%. Delete a selected offset while another offset exists and assert the other offset is not substituted.

- [ ] **Step 4: Implement fold evaluation orchestration**

For each fold: subset rows by condition IDs; select offset from training/validation only; predict test rows at that exact offset; enforce coverage; compute Phase 7 metrics, Wilson interval, execution diagnostic and regimes. Aggregate ordinary OOS rows only after asserting condition IDs are unique across ordinary test outputs.

- [ ] **Step 5: Implement final pre-holdout context and holdout evaluation**

Fit training prior and select offset from final validation context; then evaluate final holdout separately. Never include final holdout in aggregate OOS metrics.

- [ ] **Step 6: Implement canonical semantic hashes/run id**

Hash config, source training semantic SHA, dataset SHA, plan/fold membership, selected offsets, metrics, regimes and execution results. Create deterministic run id:

```python
run_id = f"phase8-{horizon_seconds}-{semantic_sha256[:32]}"
```

Exclude only `created_at` from semantic identity.

- [ ] **Step 7: Verify deterministic rerun and commit**

Run service tests twice and assert equal semantic SHA/run id. Commit:

```text
feat: orchestrate deterministic walk-forward evaluation
```

---

### Task 7: Offline CLI and One-Command Reproduction

**Files:**
- Create: `src/bp_engine/backtesting/cli.py`
- Create: `scripts/run_walk_forward_backtest.py`
- Create: `tests/backtesting/test_backtest_cli.py`

**Interfaces:**
- CLI accepts repeated `--source-training-run-id` and outputs one report per source run sorted by horizon.

- [ ] **Step 1: Write RED parser/offline tests**

Assert timezone-aware `--start/--end`, repeated run IDs, documented defaults, and source scan showing no `httpx`/`websockets` imports in backtesting CLI/service/predictor modules.

- [ ] **Step 2: Write RED atomic output + registry-rerun test**

Invoke `_run()` twice against fixture DB: first creates registry rows/report file; second leaves registry count unchanged and returns identical semantic fields.

- [ ] **Step 3: Implement parser/config conversion**

Arguments:

```text
--start --end
--source-training-run-id (repeatable, required)
--train-hours 8
--validation-hours 2
--test-hours 2
--step-hours 2
--final-holdout-hours 2
--embargo-markets 1
--min-train-markets 24
--min-validation-markets 6
--min-test-markets 6
--min-market-price-coverage 0.80
--min-prediction-coverage 0.90
--env-file
--database-url
--output-dir
```

- [ ] **Step 4: Implement transaction and atomic JSON output**

Create metadata/migration-compatible schema, run each source id in one DB transaction, store immutable report, sort reports by horizon, write temp JSON then `os.replace()` to final path. Include `created_at` clearly separate from semantic fields.

- [ ] **Step 5: Add thin script entry point and verify**

`scripts/run_walk_forward_backtest.py` imports `main` and exits with its return code. Run CLI tests, all backtesting tests, Ruff, and `python -m compileall src scripts`.

- [ ] **Step 6: Commit**

```text
feat: add reproducible walk-forward backtest CLI
```

---

### Task 8: Deployment/Production Acceptance Assets

**Files:**
- Create: `scripts/deploy/phase8_host_acceptance.sh`
- Create: `scripts/deploy/phase8_cloudshell_accept.sh`
- Create: `docs/PHASE-8-DEPLOYMENT.md`
- Create: `tests/backtesting/test_phase8_deployment_assets.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Reuse the Phase 7 verified root-owned Git worktree → `git archive` → `bp`-owned non-Git candidate source pattern.
- Host acceptance uses the two accepted Phase 7 source run IDs and fixed one-day window.

- [ ] **Step 1: Write RED deployment-contract tests**

Require exact-head provenance, exported candidate source, isolated candidate venv, lightweight disk preflight, migration `0008_backtest_runs.sql`, two identical backtest passes, registry-second-run delta zero, final safety fields, and no global `safe.directory` exception.

- [ ] **Step 2: Implement host gate**

Preflight:

```text
HEAD exact
LIVE_TRADING_ENABLED=false
MAX_TRADE_SIZE_USD=0
MAX_DAILY_LOSS_USD=0
RECORDER_BEFORE=active
DISK_STATUS_BEFORE=ok
```

Run migration and the one-command backtest twice using source run IDs:

```text
phase7-300-0a822e17ceced11742bf6d3bc8214f44
phase7-900-e36d978aecc29816c5b9e2b67b30d6e2
```

Validate at least three ordinary folds/horizon, final holdout present, no test reuse/partition overlap, all class/coverage requirements, deterministic hashes/offsets/metrics, observed-ask-only execution semantics, registry no-op second pass, recorder/disk/trading safety after.

- [ ] **Step 3: Implement Cloud Shell wrapper using proven export architecture**

Root verifies exact Git SHA in a detached worktree, archives the verified tree, extracts into `bp`-owned non-Git candidate source, passes `BP_VERIFIED_HEAD`, and cleans both worktree/source on exit. Do not chown Git worktree and do not add `safe.directory`.

- [ ] **Step 4: Document pass fields and incident-safe operator command**

Runbook must name report/evidence paths, accepted source training runs, expected `VERDICT=PASS`, and explicitly state negative P&L/low execution coverage are valid research results if integrity gates pass.

- [ ] **Step 5: Add CI shell syntax checks and verify**

Add `bash -n` for both Phase 8 helpers. Run deployment tests, full `pytest`, Ruff and deployment syntax validation.

- [ ] **Step 6: Commit**

```text
feat: add Phase 8 production acceptance gate
```

---

### Task 9: Pre-Host Verification and Production Gate

**Files:**
- No code changes unless a real defect is found test-first.

- [ ] **Step 1: Freeze the candidate HEAD**

Confirm PR head SHA and no uncommitted/branch drift.

- [ ] **Step 2: Require all four exact-head workflows**

Verify on the exact candidate:

```text
CI
Historical Backfill Smoke
Live Recorder Smoke
Recorder Short Soak
```

All must be completed/success.

- [ ] **Step 3: Run pinned Cloud Shell host acceptance**

Use the frozen SHA version of `phase8_cloudshell_accept.sh`. The user executes this GCP-host command because GCP account access is not available through the project connector.

- [ ] **Step 4: Parse the complete host evidence**

Require the Phase 8 integrity/determinism/safety fields from the design. Record actual selected offsets, aggregate OOS metrics, final holdout metrics, regime counts and observed execution coverage/P&L without cherry-picking.

- [ ] **Step 5: If the host finds a defect, use systematic debugging + TDD**

Do not lower thresholds, reuse test data for timing selection, synthesize asks, route around Bybit restrictions, or enable trading to force PASS.

---

### Task 10: Phase 8 Closeout and Integration

**Files:**
- Create after host PASS: `docs/evidence/phase-8-closeout-20260825.json`
- Modify: `PROJECT_STATE.json`
- Modify: `START-HERE.md`
- Modify: `docs/DECISION-LOG.md`
- Modify: `docs/CHANGELOG.md`

- [ ] **Step 1: Record sanitized host evidence**

Include accepted operational SHA, exact source training runs, dataset/config/plan/report hashes, fold counts, validation-selected offsets by fold, aggregate OOS vs final holdout metrics, execution coverage/gross before-costs P&L, regimes, deterministic rerun proof, disk/recorder/trading safety, and any production incidents.

- [ ] **Step 2: Advance source-of-truth only to Phase 9-ready**

Set:

```text
source_of_truth_version = 0.8.0
current_phase = 9
current_phase_name = probability calibration + edge engine
status = PHASE_8_COMPLETE_PHASE_9_READY
live_trading_enabled = false
```

- [ ] **Step 3: Add decisions for walk-forward windows/timing selection/execution availability**

Decision log must preserve that test/holdout cannot select offsets and missing historical executable prices remain no-fill evidence.

- [ ] **Step 4: Run fresh exact-head closeout gates**

All four workflows must pass on the final closeout HEAD.

- [ ] **Step 5: Mark PR ready and merge with expected-head guard**

Use GitHub expected-head merge protection. Verify `main` contains Phase 9-ready state and live trading remains disabled.
