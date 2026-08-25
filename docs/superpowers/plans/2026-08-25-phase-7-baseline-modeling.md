# Phase 7 Baseline Modeling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build deterministic leakage-safe supervised datasets, chronological market-group splits, naive/market/logistic/XGBoost baselines, immutable model-run evidence, and a production acceptance gate for separate 5m/15m research models.

**Architecture:** Join immutable `core-v1` feature rows to `official-outcome-v1` targets after feature generation, keep each `condition_id` wholly inside one chronological split, fit all preprocessing on training data only, choose a validation champion before opening test metrics, and persist a reproducible run manifest plus artifact hashes. Production acceptance expands one fixed UTC day through the already-accepted Phase 4→5→6 pipelines, then trains/evaluates both horizons offline.

**Tech Stack:** Python 3.12, SQLAlchemy Core, PostgreSQL 16, scikit-learn, XGBoost, joblib, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-25-phase-7-baseline-modeling-design.md`

## Global Constraints

- Dataset version is exactly `supervised-core-v1`.
- Split version is exactly `chronological-market-v1`.
- Feature version is `core-v1`; label version is `official-outcome-v1` for production acceptance.
- 5m and 15m models are trained/evaluated separately.
- No `condition_id` may appear in more than one train/validation/test/embargo partition.
- Train/validation/test assignment is chronological by `(market_start_at, condition_id)`.
- Numeric imputation/scaling/column dropping are fitted using train only.
- Official outcome, label references, resolution metadata, label provenance, timestamps, identifiers, hashes, and source cutoffs are never predictor columns.
- The validation champion is frozen before final test metrics are read.
- XGBoost is not promoted unless it beats the strongest simple baseline under the spec rule.
- Model binaries are external artifacts; Git stores code/docs only.
- Live trading remains disabled and Phase 8 remains blocked until Phase 7 acceptance/closeout.

---

### Task 1: Add ML dependencies and modeling data types

**Files:**
- Modify: `pyproject.toml`
- Create: `src/bp_engine/modeling/__init__.py`
- Create: `src/bp_engine/modeling/models.py`
- Test: `tests/modeling/test_modeling_models.py`

**Interfaces:**
- Produces constants `DATASET_VERSION = "supervised-core-v1"` and `SPLIT_VERSION = "chronological-market-v1"`.
- Produces frozen dataclasses `SupervisedRow`, `DatasetSnapshot`, `MarketPartition`, `DatasetSplit`, `MetricSummary`, `ModelEvaluation`, `TrainingRunReport`.

- [ ] **Step 1: Write RED tests for constants and immutable row/report types**

```python
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

from bp_engine.modeling.models import DATASET_VERSION, SupervisedRow


def test_supervised_row_is_frozen_and_versions_are_fixed() -> None:
    assert DATASET_VERSION == "supervised-core-v1"
    row = SupervisedRow(
        condition_id="c1",
        slug="btc-updown-5m-1",
        horizon_seconds=300,
        market_start_at=datetime(2026, 8, 24, tzinfo=UTC),
        market_end_at=datetime(2026, 8, 24, 0, 5, tzinfo=UTC),
        feature_at=datetime(2026, 8, 24, 0, 1, tzinfo=UTC),
        feature_offset_seconds=60,
        predictors={"pm_up_price": 0.6, "missing__x": 0.0},
        target=1,
        feature_hash="f" * 64,
        input_fingerprint="i" * 64,
    )
    try:
        row.target = 0
    except FrozenInstanceError:
        pass
    else:
        raise AssertionError("SupervisedRow must be frozen")
```

- [ ] **Step 2: Run RED**

Run: `pytest tests/modeling/test_modeling_models.py -v`  
Expected: FAIL because `bp_engine.modeling` does not exist.

- [ ] **Step 3: Add bounded dependencies**

Add project dependencies:

```toml
"scikit-learn>=1.7,<2",
"xgboost>=3,<4",
"joblib>=1.4,<2",
```

- [ ] **Step 4: Implement the frozen types minimally**

Use `@dataclass(frozen=True)` and timezone-aware datetimes. `SupervisedRow.predictors` is `dict[str, float | None]`; `target` is validated by construction helpers later.

- [ ] **Step 5: Run GREEN and lint**

Run: `pytest tests/modeling/test_modeling_models.py -v && ruff check src/bp_engine/modeling tests/modeling`  
Expected: PASS.

- [ ] **Step 6: Commit**

Commit message: `feat: add Phase 7 modeling types`

---

### Task 2: Build the immutable supervised dataset loader

**Files:**
- Create: `src/bp_engine/modeling/dataset.py`
- Test: `tests/modeling/test_dataset.py`
- Test: `tests/modeling/test_postgres_dataset.py`

**Interfaces:**
- Consumes `market_features`, `market_labels`, and `canonical_hash`.
- Produces:

```python
def load_dataset(
    connection: Connection,
    *,
    start: datetime,
    end: datetime,
    horizon_seconds: int,
    feature_version: str,
    label_version: str,
) -> DatasetSnapshot: ...
```

- [ ] **Step 1: Write RED for a valid join and forbidden predictor exclusion**

Insert one label plus two feature rows. Assert Up becomes target `1`, feature JSON numeric keys are present, missing flags become `missing__<name>` numeric predictors, and none of `official_outcome`, `source_observed_at`, `feature_at`, `condition_id`, or hashes appear in predictors.

- [ ] **Step 2: Write RED for static metadata mismatch and pre-resolution label provenance**

```python
with pytest.raises(DatasetIntegrityError, match="market window"):
    load_dataset(...)
```

Also fail if label `source_observed_at < market_end_at`, feature/missing key sets differ across rows, a feature value is non-numeric/non-null, or an official/label key appears inside `features`/`missing_flags`.

- [ ] **Step 3: Run RED**

Run: `pytest tests/modeling/test_dataset.py -v`  
Expected: FAIL because loader is absent.

- [ ] **Step 4: Implement deterministic projection and dataset hash**

Use one SQL join projecting only required columns. Build predictors as:

```python
predictors = {name: numeric_or_none(value) for name, value in sorted(features.items())}
predictors.update({f"missing__{name}": float(bool(value)) for name, value in sorted(missing.items())})
```

Reject any predictor key containing forbidden label/resolution/reference-provenance names. Compute `dataset_sha256` from sorted row descriptors containing row identity, target, versions, feature hash, and input fingerprint; do not include `created_at`.

- [ ] **Step 5: Add PostgreSQL integration coverage**

Use `BP_TEST_DATABASE_URL`; create schema, insert realistic rows, load twice, assert identical dataset SHA and ordered rows.

- [ ] **Step 6: Run GREEN**

Run: `pytest tests/modeling/test_dataset.py tests/modeling/test_postgres_dataset.py -v`  
Expected: PASS.

- [ ] **Step 7: Commit**

Commit message: `feat: build leakage-safe supervised datasets`

---

### Task 3: Implement market-group chronological splits and weights

**Files:**
- Create: `src/bp_engine/modeling/split.py`
- Test: `tests/modeling/test_split.py`

**Interfaces:**

```python
def chronological_market_split(
    dataset: DatasetSnapshot,
    *,
    train_fraction: float = 0.60,
    validation_fraction: float = 0.20,
    embargo_markets: int = 1,
    min_markets: int = 6,
) -> DatasetSplit: ...

def equal_market_weights(rows: tuple[SupervisedRow, ...]) -> tuple[float, ...]: ...
```

- [ ] **Step 1: Write RED proving rows from one condition never cross splits**

Create 20 markets with four rows each. Assert condition sets are disjoint, ordering is chronological, and embargo conditions appear in no evaluation partition.

- [ ] **Step 2: Write RED for equal total market weight**

Use conditions with 2 and 4 rows and assert each condition's weights sum to `1.0`.

- [ ] **Step 3: Write RED for insufficient/single-class training data**

Require `SplitIntegrityError` when unique-market count is below `min_markets` or training targets contain one class only.

- [ ] **Step 4: Run RED**

Run: `pytest tests/modeling/test_split.py -v`  
Expected: FAIL.

- [ ] **Step 5: Implement deterministic boundaries**

Order unique conditions by `(market_start_at, condition_id)`, compute integer partition boundaries deterministically, reserve one whole market at each boundary when configured, and expand membership back to rows. Store exact condition IDs in `MarketPartition` objects.

- [ ] **Step 6: Run GREEN**

Run: `pytest tests/modeling/test_split.py -v`  
Expected: PASS.

- [ ] **Step 7: Commit**

Commit message: `feat: add chronological market splits`

---

### Task 4: Add probability metrics, calibration, coverage, and baselines

**Files:**
- Create: `src/bp_engine/modeling/metrics.py`
- Create: `src/bp_engine/modeling/baselines.py`
- Test: `tests/modeling/test_metrics.py`
- Test: `tests/modeling/test_baselines.py`

**Interfaces:**

```python
def evaluate_probabilities(
    rows: tuple[SupervisedRow, ...],
    probabilities: tuple[float, ...],
    weights: tuple[float, ...],
) -> MetricSummary: ...

class PriorBaseline:
    def fit(self, rows: tuple[SupervisedRow, ...], weights: tuple[float, ...]) -> None: ...
    def predict_proba(self, rows: tuple[SupervisedRow, ...]) -> tuple[float, ...]: ...

class MarketPriceBaseline:
    def __init__(self, fallback_probability: float) -> None: ...
    def predict_proba(self, rows: tuple[SupervisedRow, ...]) -> tuple[float, ...]: ...
```

- [ ] **Step 1: Write metric RED with hand-checkable probabilities**

Assert weighted accuracy, Brier, log loss, class recall/balanced accuracy, 50–55 through 90%+ calibration buckets, ECE, and confidence coverage thresholds.

- [ ] **Step 2: Write baseline RED**

Assert prior probability uses training weights only. Market baseline uses `pm_up_price`, clips to `[1e-6, 1 - 1e-6]`, and uses only the supplied training-prior fallback when price is NULL.

- [ ] **Step 3: Run RED**

Run: `pytest tests/modeling/test_metrics.py tests/modeling/test_baselines.py -v`  
Expected: FAIL.

- [ ] **Step 4: Implement metrics without favorable coercion**

Return `balanced_accuracy=None` when a split lacks a class. Reject probabilities outside finite `[0, 1]`; no NaN/Inf is accepted.

- [ ] **Step 5: Run GREEN**

Run: `pytest tests/modeling/test_metrics.py tests/modeling/test_baselines.py -v`  
Expected: PASS.

- [ ] **Step 6: Commit**

Commit message: `feat: add Phase 7 baseline metrics`

---

### Task 5: Train logistic and XGBoost challengers with train-only preprocessing

**Files:**
- Create: `src/bp_engine/modeling/trainers.py`
- Create: `src/bp_engine/modeling/artifacts.py`
- Test: `tests/modeling/test_trainers.py`
- Test: `tests/modeling/test_artifacts.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class PreparedMatrix:
    predictor_names: tuple[str, ...]
    dropped_all_missing: tuple[str, ...]
    x_train: Any
    x_validation: Any
    x_test: Any


def prepare_matrices(split: DatasetSplit) -> PreparedMatrix: ...
def train_logistic(split: DatasetSplit, prepared: PreparedMatrix) -> TrainedModel: ...
def train_xgboost(split: DatasetSplit, prepared: PreparedMatrix) -> TrainedModel: ...
def write_model_artifact(model: object, *, output_dir: Path, name: str) -> ModelArtifact: ...
```

- [ ] **Step 1: Write RED proving preprocessing is train-only**

Construct a predictor whose validation/test values would change the train median if leaked. Assert imputed validation values use the train median. Assert an all-NULL training column is dropped even if validation/test contain values.

- [ ] **Step 2: Write logistic determinism RED**

Train twice on the same synthetic split and assert identical validation probabilities within `1e-12` and finite results.

- [ ] **Step 3: Write XGBoost determinism RED**

Use fixed config: `n_estimators=200`, `max_depth=3`, `learning_rate=0.05`, `subsample=0.9`, `colsample_bytree=0.9`, `reg_lambda=1.0`, `objective="binary:logistic"`, `eval_metric="logloss"`, `tree_method="hist"`, `random_state=20260825`, `n_jobs=1`.

- [ ] **Step 4: Run RED**

Run: `pytest tests/modeling/test_trainers.py tests/modeling/test_artifacts.py -v`  
Expected: FAIL.

- [ ] **Step 5: Implement train-only matrix prep and both trainers**

Use `SimpleImputer(strategy="median")` plus `StandardScaler()` for logistic. XGBoost receives median-imputed numeric arrays using the same train-derived imputer but no scaler. Apply `equal_market_weights` as sample weights.

- [ ] **Step 6: Implement joblib artifact hashing**

Write atomically through a temporary path, `os.replace`, then SHA-256 the final bytes. Artifact manifest stores family, file name, size, SHA-256, and library version; it never embeds secrets.

- [ ] **Step 7: Run GREEN**

Run: `pytest tests/modeling/test_trainers.py tests/modeling/test_artifacts.py -v`  
Expected: PASS.

- [ ] **Step 8: Commit**

Commit message: `feat: train deterministic baseline models`

---

### Task 6: Add immutable model-training registry

**Files:**
- Create: `migrations/0007_model_training_runs.sql`
- Modify: `src/bp_engine/storage/schema.py`
- Create: `src/bp_engine/modeling/repository.py`
- Test: `tests/modeling/test_model_run_schema.py`
- Test: `tests/modeling/test_model_run_repository.py`
- Test: `tests/modeling/test_postgres_model_runs.py`

**Interfaces:**

```python
class TrainingRunConflict(RuntimeError): ...

class ModelTrainingRunRepository:
    def store(self, connection: Connection, report: TrainingRunReport) -> StoreResult: ...
```

- [ ] **Step 1: Write schema RED**

Require columns `run_id`, versions, horizon, requested start/end, dataset hash, split hash/config, predictor names, dropped names, model configs, validation champion, report JSON, artifact manifest, semantic hash, created_at; unique `run_id`; positive horizon/window checks.

- [ ] **Step 2: Write repository RED**

First insert returns created. Exact semantic rerun returns existing and preserves original `created_at`. Same `run_id` with changed dataset/config/metrics/artifact hash raises `TrainingRunConflict`.

- [ ] **Step 3: Run RED**

Run: `pytest tests/modeling/test_model_run_schema.py tests/modeling/test_model_run_repository.py -v`  
Expected: FAIL.

- [ ] **Step 4: Implement migration/schema/repository**

Semantic hash excludes `created_at` and artifact absolute directory path but includes artifact file SHA-256 and semantic report content.

- [ ] **Step 5: Add PostgreSQL integration test and run GREEN**

Run: `pytest tests/modeling/test_model_run_schema.py tests/modeling/test_model_run_repository.py tests/modeling/test_postgres_model_runs.py -v`  
Expected: PASS.

- [ ] **Step 6: Commit**

Commit message: `feat: add immutable model run registry`

---

### Task 7: Build horizon training service and CLI with test-set isolation

**Files:**
- Create: `src/bp_engine/modeling/service.py`
- Create: `src/bp_engine/modeling/cli.py`
- Create: `scripts/train_baselines.py`
- Test: `tests/modeling/test_training_service.py`
- Test: `tests/modeling/test_training_cli.py`
- Test: `tests/modeling/test_validation_champion.py`

**Interfaces:**

```python
def train_horizon(
    connection: Connection,
    *,
    start: datetime,
    end: datetime,
    horizon_seconds: int,
    feature_version: str,
    label_version: str,
    output_dir: Path,
    min_markets: int,
) -> TrainingRunReport: ...
```

- [ ] **Step 1: Write RED proving validation champion cannot change from test results**

Build synthetic candidates where logistic wins validation but XGBoost wins test. Assert `validation_champion == "logistic"` and `best_test_result == "xgboost"` remain distinct.

- [ ] **Step 2: Write promotion-rule RED**

XGBoost promotion is true only when validation log loss and Brier beat every simple baseline and test log loss is lower without worse Brier.

- [ ] **Step 3: Write CLI RED**

Require timezone-aware `--start/--end`, `--output-dir`, versions, optional repeated `--horizon-seconds`, JSON stdout, and non-zero failure on insufficient markets.

- [ ] **Step 4: Run RED**

Run: `pytest tests/modeling/test_training_service.py tests/modeling/test_training_cli.py tests/modeling/test_validation_champion.py -v`  
Expected: FAIL.

- [ ] **Step 5: Implement orchestration**

For each horizon: load dataset → split → fit prior → freeze market baseline fallback → prepare matrices → fit logistic/XGBoost → compute validation metrics → select champion → compute test metrics → compute by-offset metrics and gross executable-ask diagnostic → write learned artifacts → build deterministic report/run id → store registry row.

- [ ] **Step 6: Run GREEN plus full modeling suite**

Run: `pytest tests/modeling -v`  
Expected: PASS.

- [ ] **Step 7: Commit**

Commit message: `feat: add Phase 7 training service`

---

### Task 8: Add production data-expansion and acceptance helpers

**Files:**
- Create: `scripts/deploy/phase7_host_acceptance.sh`
- Create: `scripts/deploy/phase7_cloudshell_accept.sh`
- Create: `docs/PHASE-7-DEPLOYMENT.md`
- Modify: `.github/workflows/ci.yml`
- Test: `tests/modeling/test_phase7_deployment_assets.py`

**Interfaces:**
- Canonical acceptance window: `2026-08-24T00:00:00Z <= market_start_at < 2026-08-25T00:00:00Z`.
- Minimum unique markets: 5m >= 100; 15m >= 30.

- [ ] **Step 1: Write deployment RED**

Assert the scripts contain exact-head guard, safety settings, recorder before/after, disk preflight, Phase 4 standard backfill, Phase 5 labels, Phase 6 features, migration 0007, two immediate training runs, market-count minimums, no split overlap checks, deterministic rerun checks, artifact-hash checks, and final PASS fields.

- [ ] **Step 2: Run RED**

Run: `pytest tests/modeling/test_phase7_deployment_assets.py -v`  
Expected: FAIL because assets do not exist.

- [ ] **Step 3: Implement host acceptance fail-closed**

The host script must:

```text
verify exact HEAD
verify LIVE_TRADING_ENABLED=false and zero limits
verify bp-recorder active
verify storage report disk.status=ok
apply migration 0007
run historical_backfill.py standard for fixed day
run generate_labels.py for fixed day
run generate_features.py for fixed day
run train_baselines.py for both horizons twice
verify >=100 5m and >=30 15m unique markets
verify identical dataset/split/config/champion/semantic metrics across training rerun
verify registry second run is existing/no-op
verify artifact hashes are present
verify no condition appears across split partitions
verify both classes exist in every non-embargo split
verify recorder active after
print VERDICT=PASS and safety fields
```

- [ ] **Step 4: Implement Cloud Shell isolated-worktree wrapper**

Follow the Phase 6 pattern: fetch only `build/phase-7-baseline-modeling`, verify exact `PHASE7_HEAD`, create detached `/var/tmp` worktree, invoke host script without replacing `/opt/bp`.

- [ ] **Step 5: Add runbook and CI shell syntax checks**

Document evidence paths, known Bybit 403 behavior, expected runtime/data volume caveat without lowering gates, and exact PASS summary.

- [ ] **Step 6: Run GREEN**

Run: `pytest tests/modeling/test_phase7_deployment_assets.py -v && bash -n scripts/deploy/phase7_host_acceptance.sh && bash -n scripts/deploy/phase7_cloudshell_accept.sh`  
Expected: PASS.

- [ ] **Step 7: Commit**

Commit message: `feat: add Phase 7 production acceptance gate`

---

### Task 9: Freeze candidate and run complete pre-host verification

**Files:**
- No semantic code changes after candidate freeze.

- [ ] **Step 1: Run full local/CI-equivalent verification**

Run:

```bash
ruff check .
pytest
bash -n scripts/deploy/phase7_host_acceptance.sh
bash -n scripts/deploy/phase7_cloudshell_accept.sh
```

Expected: all pass.

- [ ] **Step 2: Open Phase 7 PR as draft**

PR body must link spec/plan, state the fixed acceptance day/minimum market counts, and explicitly state that live trading remains disabled.

- [ ] **Step 3: Require exact-head GitHub gates**

Require successful CI, Historical Backfill Smoke, Live Recorder Smoke, and Recorder Short Soak on the exact candidate SHA before production acceptance.

- [ ] **Step 4: Production host acceptance**

Run the one-line Cloud Shell wrapper only with the verified exact SHA. Preserve complete PASS/FAIL evidence; do not alter accepted source rows or lower market/storage/safety thresholds to force success.

- [ ] **Step 5: Closeout only after PASS**

Record sanitized evidence, update `PROJECT_STATE.json`, `docs/CHANGELOG.md`, `docs/DECISION-LOG.md`, and `START-HERE.md`; run the full exact-head gate set again on the closeout HEAD; mark PR ready; merge using `expected_head_sha`; verify `main` afterward.

Phase 8 remains blocked until this final step is complete.
