# Phase 9 Probability Calibration + Edge Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, leakage-safe probability calibration and executable-edge/abstention engine on top of immutable Phase 8 walk-forward runs for the verified 5m and 15m BTC Polymarket horizons.

**Architecture:** Load a restricted immutable Phase 8 source specification, reconstruct each stored fold at its already-selected offset, fit identity and monotone Platt calibration on train only, select calibration and minimum executable-edge policy on validation only, and evaluate frozen decisions on test/final holdout. Edge uses observed selected-side best ask, explicit taker-fee/slippage assumptions, missing/stale no-fill semantics, and an always-available `no_trade` policy; immutable Phase 9 registry rows preserve every choice and hash.

**Tech Stack:** Python 3.12, SQLAlchemy 2, PostgreSQL 16, scikit-learn logistic regression, pytest, Ruff, existing `bp_engine` feature/model/backtest contracts.

**Spec:** `docs/superpowers/specs/2026-08-25-phase-9-probability-calibration-edge-design.md`

## Global Constraints

- RESEARCH mode only; live trading remains disabled.
- Accepted source backtest version is exactly `walk-forward-v1`.
- Calibration version is exactly `platt-or-identity-v1`.
- Edge policy version is exactly `selected-ask-edge-v1`.
- Phase 8 selected offsets are reused; Phase 9 never reselects timing from test/holdout data.
- Calibration fits on train only; calibration method and minimum edge select on validation only.
- Rows without observed `pm_up_price` are never trade eligible.
- Missing/stale selected-side book or invalid ask means unavailable/no trade.
- Midpoint, price-history substitutes, and synthetic fills are forbidden.
- Spread is paid through executable ask and reported/gated, not double-subtracted.
- Fee curve is `fee_rate * ask * (1 - ask)` per one share.
- Production research fee-rate assumption is `0.07`; slippage sensitivity assumption is `0.01` USDC/share.
- Default min-edge grid is `(0.00, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15)`.
- `no_trade` is a valid deterministic policy outcome.
- Ordinary test markets cannot repeat; final holdout cannot overlap ordinary OOS.
- Identical registry reruns are no-op; semantic conflicts fail closed.

---

### Task 1: Calibration primitives and model types

**Files:**
- Create: `src/bp_engine/calibration/__init__.py`
- Create: `src/bp_engine/calibration/models.py`
- Create: `src/bp_engine/calibration/calibrators.py`
- Test: `tests/calibration/test_calibrators.py`

**Interfaces:**
- Consumes: `SupervisedRow`, `evaluate_probabilities`, `equal_market_weights`.
- Produces:
  - `CALIBRATION_VERSION = "platt-or-identity-v1"`
  - `IdentityCalibrator.fit(rows, probabilities) -> None`
  - `IdentityCalibrator.predict(probabilities) -> tuple[float, ...]`
  - `PlattCalibrator.fit(rows, probabilities, weights) -> None`
  - `PlattCalibrator.predict(probabilities) -> tuple[float, ...]`
  - `CalibrationFit(method, intercept, coefficient)`
  - `CalibrationSelection(method, fit, validation_metrics, candidates)`
  - `select_calibrator(train_rows, train_probabilities, validation_rows, validation_probabilities) -> CalibrationSelection`

- [ ] **Step 1: Write RED calibration tests**

```python
from bp_engine.calibration.calibrators import (
    IdentityCalibrator,
    PlattCalibrator,
    select_calibrator,
)


def test_identity_clips_probabilities():
    model = IdentityCalibrator()
    assert model.predict((0.0, 0.25, 1.0)) == (1e-6, 0.25, 0.999999)


def test_platt_fit_is_deterministic_and_monotone(supervised_rows):
    rows = supervised_rows((0, 0, 1, 1))
    probs = (0.2, 0.4, 0.6, 0.8)
    left = PlattCalibrator().fit(rows, probs, (1.0,) * 4)
    right = PlattCalibrator().fit(rows, probs, (1.0,) * 4)
    assert left == right
    assert left.coefficient > 0


def test_platt_promotes_only_when_both_logloss_and_brier_improve(...):
    selection = select_calibrator(...)
    assert selection.method == "identity"
```

Create fixture helpers that construct real `SupervisedRow` instances with one row per condition and known targets.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `pytest tests/calibration/test_calibrators.py -v`  
Expected: import/module failures for the new calibration package.

- [ ] **Step 3: Implement deterministic identity and Platt calibrators**

Use:

```python
_EPSILON = 1e-6

def clip_probability(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("probability must be finite")
    return min(max(value, _EPSILON), 1.0 - _EPSILON)


def logit(value: float) -> float:
    p = clip_probability(value)
    return math.log(p / (1.0 - p))
```

Fit Platt with one-feature `LogisticRegression(solver="lbfgs", max_iter=1000, random_state=20260825)` and equal-market weights. Persist only finite intercept/coefficient. Raise `CalibrationRejected` for non-positive slope; selection catches challenger rejection and retains identity.

- [ ] **Step 4: Implement validation-only calibrator selection**

Fit both candidates from train probabilities. Evaluate validation probabilities with existing `evaluate_probabilities`. Promote Platt only when:

```python
platt.log_loss < identity.log_loss and platt.brier_score < identity.brier_score
```

Otherwise select identity. Store both candidate metric summaries in deterministic order.

- [ ] **Step 5: Run focused calibration tests**

Run: `pytest tests/calibration/test_calibrators.py -v`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/bp_engine/calibration tests/calibration/test_calibrators.py
git commit -m "feat: add deterministic probability calibration"
```

---

### Task 2: Executable edge, cost assumptions, and abstention selection

**Files:**
- Create: `src/bp_engine/calibration/edge.py`
- Extend: `src/bp_engine/calibration/models.py`
- Test: `tests/calibration/test_edge.py`

**Interfaces:**
- Consumes: calibrated Up probability and a `SupervisedRow` at the frozen selected offset.
- Produces:
  - `EDGE_POLICY_VERSION = "selected-ask-edge-v1"`
  - `EdgeConfig(fee_rate, slippage_buffer, min_edge_grid, min_validation_trades, max_spread)`
  - `EdgeDecision`
  - `edge_decision(row, p_up, config, min_edge) -> EdgeDecision`
  - `evaluate_edge_policy(rows, probabilities, config, min_edge) -> EdgePolicyMetrics`
  - `select_validation_edge_policy(rows, probabilities, config) -> EdgePolicySelection`

- [ ] **Step 1: Write RED edge tests**

Cover exact calculations:

```python
def test_up_edge_uses_fresh_up_ask_and_fee_curve(row):
    decision = edge_decision(row, 0.72, config(fee_rate=0.07, slippage_buffer=0.01), 0.02)
    assert decision.side == "up"
    assert decision.ask == 0.60
    assert decision.fee == pytest.approx(0.07 * 0.60 * 0.40)
    assert decision.cost_adjusted_edge == pytest.approx(0.72 - 0.60 - decision.fee - 0.01)


def test_down_edge_uses_down_ask(row): ...
def test_stale_selected_side_is_no_fill(row): ...
def test_missing_market_probability_is_not_trade_eligible(row): ...
def test_no_trade_selected_when_every_validation_threshold_loses(...): ...
def test_minimum_validation_trade_gate_blocks_one_lucky_trade(...): ...
```

Also assert source text has no midpoint/synthetic-price path.

- [ ] **Step 2: Run focused edge tests and verify RED**

Run: `pytest tests/calibration/test_edge.py -v`  
Expected: missing edge module/functions.

- [ ] **Step 3: Implement `EdgeConfig` validation**

Require finite non-negative fee/slippage/min-edge values, strictly increasing unique threshold grid, positive `min_validation_trades`, and `max_spread` either `None` or `(0, 1]`.

- [ ] **Step 4: Implement side-aware observed-price decision**

Use calibrated side, existing Phase 8 missing/stale flag semantics, observed selected-side best ask, and selected-side best bid for spread reporting. If `pm_up_price` is absent, return abstention reason `missing_market_probability` before considering the ask.

Use:

```python
fee = config.fee_rate * ask * (1.0 - ask)
raw_edge = p_side - ask
cost_adjusted_edge = raw_edge - fee - config.slippage_buffer
trade = cost_adjusted_edge >= min_edge
```

If book/ask is unavailable return `no_fill`; if measured spread exceeds configured maximum return `spread_too_wide`.

- [ ] **Step 5: Implement realized edge-policy metrics and validation selection**

For trades, calculate:

```python
payout = 1.0 if row.target == predicted_target else 0.0
gross_pnl = payout - ask
assumed_cost_pnl = gross_pnl - fee - config.slippage_buffer
```

Evaluate every threshold from the frozen grid plus explicit `no_trade`. A threshold is selectable only with at least `min_validation_trades`, total assumed-cost P&L > 0, and mean assumed-cost P&L > 0. Select by total P&L, mean P&L, then larger threshold. If none qualifies, select `no_trade`.

- [ ] **Step 6: Run focused edge tests**

Run: `pytest tests/calibration/test_edge.py -v`  
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/bp_engine/calibration/models.py src/bp_engine/calibration/edge.py tests/calibration/test_edge.py
git commit -m "feat: add executable edge abstention policy"
```

---

### Task 3: Restricted immutable Phase 8 source loader

**Files:**
- Create: `src/bp_engine/calibration/source.py`
- Test: `tests/calibration/test_source.py`
- Test: `tests/calibration/test_source_postgres.py`

**Interfaces:**
- Consumes: `backtest_runs` immutable row.
- Produces:
  - `BacktestSourceSpec`
  - `SourceFoldSpec(index, membership_sha256, train_condition_ids, validation_condition_ids, test_condition_ids, selected_offset_seconds)`
  - `FinalSourceSpec(...)`
  - `load_backtest_source_spec(connection, run_id) -> BacktestSourceSpec`

The returned type must omit Phase 8 test/holdout metrics and execution results.

- [ ] **Step 1: Write RED source-loader tests**

Construct a stored `backtest_runs` row with report membership and assert:

```python
spec = load_backtest_source_spec(connection, "phase8-...")
assert spec.backtest_version == "walk-forward-v1"
assert spec.folds[0].selected_offset_seconds == 240
assert not hasattr(spec.folds[0], "metrics")
```

Add rejection tests for wrong backtest version, duplicate ordinary test condition, final holdout overlap, missing selected offset, malformed SHA, and inconsistent report horizon/source identity.

- [ ] **Step 2: Run source tests and verify RED**

Run: `pytest tests/calibration/test_source.py tests/calibration/test_source_postgres.py -v`  
Expected: module/function missing.

- [ ] **Step 3: Implement loader using `BacktestRunRepository().get`**

Validate top-level stored columns against semantic report fields. Extract only immutable identity, memberships, hashes, and offsets. Build a `seen_test` set while reading folds and reject repeats. Reject final holdout overlap with `seen_test`.

- [ ] **Step 4: Run source tests**

Run: `pytest tests/calibration/test_source.py tests/calibration/test_source_postgres.py -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bp_engine/calibration/source.py tests/calibration/test_source.py tests/calibration/test_source_postgres.py
git commit -m "feat: load restricted Phase 8 source specs"
```

---

### Task 4: Fold-level calibration and edge evaluation

**Files:**
- Create: `src/bp_engine/calibration/evaluation.py`
- Extend: `src/bp_engine/calibration/models.py`
- Test: `tests/calibration/test_evaluation.py`

**Interfaces:**
- Consumes: `BacktestSourceSpec`, reconstructed `DatasetSnapshot`, `EdgeConfig`.
- Produces:
  - `CalibrationEdgeFoldReport`
  - `CalibrationEdgeFinalReport`
  - `evaluate_source_fold(dataset, fold_spec, edge_config) -> CalibrationEdgeFoldReport`
  - `evaluate_final_source(dataset, final_spec, edge_config) -> CalibrationEdgeFinalReport`

- [ ] **Step 1: Write RED evaluation tests**

Use rows at multiple offsets and distinct condition IDs. Assert:

```python
report = evaluate_source_fold(dataset, source_fold(offset=240), edge_config)
assert report.selected_offset_seconds == 240
assert report.calibration_selection_fit_partition == "train"
assert report.calibration_selection_partition == "validation"
assert report.edge_selection_partition == "validation"
```

Change only test targets and assert selected calibration method/min-edge are unchanged. Change only validation targets and assert selection may change. Add a test that a missing `pm_up_price` row is included in prediction coverage but excluded from trade eligibility.

- [ ] **Step 2: Run evaluation tests and verify RED**

Run: `pytest tests/calibration/test_evaluation.py -v`  
Expected: module/function missing.

- [ ] **Step 3: Implement partition reconstruction**

Reuse `rows_at_offset` and explicit condition membership. Validate one row per condition at the stored offset. Fit the Phase 8 market-price predictor on training rows only for source consistency, but use only rows with observed `pm_up_price` for Phase 9 calibration and edge decisions.

- [ ] **Step 4: Implement frozen fold selection/evaluation**

For each fold:

1. raw training/validation/test probabilities from the source predictor;
2. select calibrator using train fit + validation metrics;
3. calibrate validation and choose min-edge/no-trade;
4. freeze both;
5. calibrate/evaluate test once;
6. return raw/calibrated probability metrics plus edge policy metrics and provenance.

Do not expose a callback or parameter that can inspect test metrics before selection returns.

- [ ] **Step 5: Implement final train/validation/holdout evaluation with identical selection boundaries**

Use final-train fit, final-validation selection, final-holdout evaluation. Assert final holdout IDs do not occur in ordinary OOS before the service calls this function.

- [ ] **Step 6: Run evaluation tests**

Run: `pytest tests/calibration/test_evaluation.py -v`  
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/bp_engine/calibration/models.py src/bp_engine/calibration/evaluation.py tests/calibration/test_evaluation.py
git commit -m "feat: evaluate frozen calibration edge folds"
```

---

### Task 5: Phase 9 report orchestration and deterministic semantics

**Files:**
- Create: `src/bp_engine/calibration/service.py`
- Extend: `src/bp_engine/calibration/models.py`
- Test: `tests/calibration/test_service.py`

**Interfaces:**
- Produces:
  - `CalibrationEdgeReport`
  - `run_calibration_edge_analysis(connection, source_backtest_run_id, start, end, edge_config, created_at) -> CalibrationEdgeReport`

- [ ] **Step 1: Write RED service tests**

Assert exact source dataset SHA reconstruction, ordinary-test non-reuse, final-holdout disjointness, stable report semantics, and deterministic run id:

```python
left = run_calibration_edge_analysis(..., created_at=t0)
right = run_calibration_edge_analysis(..., created_at=t1)
assert left.semantic_sha256 == right.semantic_sha256
assert left.run_id == right.run_id
assert left.created_at != right.created_at
```

- [ ] **Step 2: Run service tests and verify RED**

Run: `pytest tests/calibration/test_service.py -v`  
Expected: missing service/report implementation.

- [ ] **Step 3: Implement service source/dataset integrity**

Load restricted Phase 8 source, reconstruct `load_dataset` with source versions/horizon/window, and require reconstructed `dataset_sha256 == source.dataset_sha256` plus exact requested window/horizon.

- [ ] **Step 4: Evaluate all ordinary folds and aggregate frozen test decisions**

Reject duplicate ordinary test IDs. Aggregate raw/calibrated metrics and edge economics from already-frozen fold outputs without selecting any global OOS calibrator or edge threshold.

- [ ] **Step 5: Evaluate final holdout and build semantic payload**

Hash source identities, versions, window, dataset SHA, config, source plan/fold hashes, fold reports, aggregate OOS report, and final report. Exclude only `created_at`. Use:

```python
semantic_sha256 = canonical_hash(semantic_payload)
run_id = f"phase9-{horizon_seconds}-{semantic_sha256[:32]}"
```

- [ ] **Step 6: Run service tests**

Run: `pytest tests/calibration/test_service.py -v`  
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/bp_engine/calibration/models.py src/bp_engine/calibration/service.py tests/calibration/test_service.py
git commit -m "feat: orchestrate calibration edge reports"
```

---

### Task 6: Immutable Phase 9 registry

**Files:**
- Create: `migrations/0009_calibration_edge_runs.sql`
- Modify: `src/bp_engine/storage/schema.py`
- Create: `src/bp_engine/calibration/repository.py`
- Test: `tests/calibration/test_repository.py`
- Test: `tests/calibration/test_repository_postgres.py`

**Interfaces:**
- Produces:
  - SQLAlchemy `calibration_edge_runs` table
  - `CalibrationEdgeRunRepository.get(connection, run_id)`
  - `CalibrationEdgeRunRepository.store(connection, report) -> CalibrationEdgeStoreResult`
  - `CalibrationEdgeRunConflict`

- [ ] **Step 1: Write RED repository/schema tests**

Require migration/table fields and immutable behavior. Store a report twice and assert created then existing; mutate the semantic report under the same run id and assert `CalibrationEdgeRunConflict`.

- [ ] **Step 2: Run repository tests and verify RED**

Run: `pytest tests/calibration/test_repository.py tests/calibration/test_repository_postgres.py -v`  
Expected: missing table/repository.

- [ ] **Step 3: Add migration and SQLAlchemy schema**

Create columns:

```text
run_id, calibration_version, edge_policy_version,
source_backtest_run_id, source_backtest_semantic_sha256,
source_training_run_id, source_training_semantic_sha256,
dataset_version, feature_version, label_version, horizon_seconds,
requested_start, requested_end, dataset_sha256,
config, config_sha256, source_plan_sha256, source_fold_membership_sha256,
report, semantic_sha256, created_at
```

Add positive horizon/window checks, unique run id, and `(horizon_seconds, created_at)` index.

- [ ] **Step 4: Implement immutable repository**

Mirror Phase 8 `_jsonable`/semantic-report behavior. Compare stored semantic SHA and complete semantic report on duplicate run id. Identical rerun is existing/no-op; differences fail closed.

- [ ] **Step 5: Run repository tests**

Run: `pytest tests/calibration/test_repository.py tests/calibration/test_repository_postgres.py -v`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add migrations/0009_calibration_edge_runs.sql src/bp_engine/storage/schema.py src/bp_engine/calibration/repository.py tests/calibration/test_repository.py tests/calibration/test_repository_postgres.py
git commit -m "feat: add immutable calibration edge registry"
```

---

### Task 7: CLI and reproducible offline command

**Files:**
- Create: `src/bp_engine/calibration/cli.py`
- Create: `scripts/run_calibration_edge.py`
- Test: `tests/calibration/test_cli.py`

**Interfaces:**
- CLI accepts repeatable source run IDs and emits stable JSON report objects.

- [ ] **Step 1: Write RED CLI tests**

Test parser defaults and rejection cases. Require explicit `--fee-rate` and `--slippage-buffer` and default threshold grid/min trades. Test one invocation stores report and prints JSON; immediate rerun prints the same semantic content and does not create another registry row.

- [ ] **Step 2: Run CLI tests and verify RED**

Run: `pytest tests/calibration/test_cli.py -v`  
Expected: missing CLI/script.

- [ ] **Step 3: Implement CLI parser and validation**

Inputs:

```text
--source-backtest-run-id (append, required)
--start / --end (required ISO UTC)
--fee-rate (required float)
--slippage-buffer (required float)
--min-edge (append; default frozen grid)
--min-validation-trades (default 3)
--max-spread (optional)
--env-file
--output-dir (optional)
```

- [ ] **Step 4: Implement transactional run/store/output flow**

For each source run: run analysis, store immutably in one transaction, serialize deterministic JSON, optionally write `<run_id>.json` atomically, and print a JSON array sorted by horizon.

- [ ] **Step 5: Run CLI tests and syntax/lint**

Run:

```bash
pytest tests/calibration/test_cli.py -v
ruff check src/bp_engine/calibration scripts/run_calibration_edge.py tests/calibration
python -m compileall -q src scripts/run_calibration_edge.py
```

Expected: all PASS / Ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/bp_engine/calibration/cli.py scripts/run_calibration_edge.py tests/calibration/test_cli.py
git commit -m "feat: add calibration edge analysis CLI"
```

---

### Task 8: Consolidated leakage/economics acceptance tests

**Files:**
- Create: `tests/calibration/test_phase9_contract.py`
- Modify only if a genuine bug is exposed: Phase 9 modules from Tasks 1–7.

**Interfaces:** End-to-end unit/PostgreSQL contract before deployment assets.

- [ ] **Step 1: Write consolidated contract tests**

Include explicit assertions that:

```python
assert report.source_selected_offsets == accepted_phase8_offsets
assert report.test_selection_inputs == ()
assert report.final_holdout_selection_inputs == ()
assert report.registry_second_run_delta == 0
```

Represent the intent through real report fields rather than fake flags where possible. Add a test with spectacular test outcomes but losing validation economics and assert validation still selects `no_trade`. Add a test with changed final-holdout targets and assert calibrator/threshold hashes are unchanged.

- [ ] **Step 2: Run full calibration suite**

Run: `pytest tests/calibration -v`  
Expected: PASS. If a new RED exposes a real implementation defect, fix only that defect and rerun until green.

- [ ] **Step 3: Run full project CI commands locally/in workflow-compatible form**

Run:

```bash
ruff check .
pytest
python -m compileall -q src scripts
```

Expected: all clean.

- [ ] **Step 4: Commit**

```bash
git add tests/calibration src/bp_engine/calibration
git commit -m "test: lock Phase 9 leakage and edge semantics"
```

---

### Task 9: Phase 9 production acceptance assets

**Files:**
- Create: `scripts/deploy/phase9_host_acceptance.sh`
- Create: `scripts/deploy/phase9_cloudshell_accept.sh`
- Create: `docs/PHASE-9-DEPLOYMENT.md`
- Create: `tests/calibration/test_phase9_deployment_assets.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:** Exact-SHA production-host acceptance, no Git ownership coupling, no live trading.

- [ ] **Step 1: Write RED deployment-asset tests**

Require:

- exact `BP_VERIFIED_HEAD` provenance;
- root-owned detached worktree + exact `git archive` into bp-owned non-Git source directory;
- migration `0009_calibration_edge_runs.sql`;
- accepted 5m/15m Phase 8 source run IDs/semantic hashes;
- fixed day start/end;
- `--fee-rate 0.07 --slippage-buffer 0.01`;
- semantic rerun and registry second-run delta zero;
- source selected-offset equality checks;
- zero ordinary-test/final-holdout overlap;
- fallback/stale/missing executable trade violations zero;
- explicit cost assumptions present;
- `no_trade` accepted as valid;
- bounded `storage_maintenance.py disk-health` preflight **and** postflight;
- recorder active before/after;
- live trading false and trade/loss limits zero;
- `VERDICT=PASS` / `PHASE9_HOST_ACCEPTANCE=PASS`;
- no `safe.directory`, midpoint, synthetic fill, or unbounded `storage_maintenance.py report` in acceptance critical path.

- [ ] **Step 2: Run deployment tests and verify RED**

Run: `pytest tests/calibration/test_phase9_deployment_assets.py -v`  
Expected: missing deployment assets.

- [ ] **Step 3: Implement host acceptance**

Use the same verified-export architecture accepted in Phases 7/8. Run migration, execute Phase 9 analysis twice against accepted source IDs, validate stored run count and semantic equality, and produce `research-summary.txt` + `final-summary.txt` under `/var/lib/bp/evidence/phase9-calibration-edge/<stamp>`.

Do not fail merely because the selected policy is `no_trade` or test/holdout P&L is negative. Fail only contract/safety/reproducibility violations.

- [ ] **Step 4: Implement Cloud Shell wrapper**

Pin `PHASE9_HEAD`; create root-owned detached worktree from `/opt/bp`, verify exact SHA, archive exact candidate into bp-owned `/var/tmp/bp-phase9-src-*`, execute host gate with `BP_REPO` and `BP_VERIFIED_HEAD`, tee latest log, remove temp worktree/source on exit.

- [ ] **Step 5: Update CI syntax validation and deployment runbook**

Add:

```bash
bash -n scripts/deploy/phase9_host_acceptance.sh
bash -n scripts/deploy/phase9_cloudshell_accept.sh
```

Document evidence paths, cost-assumption semantics, and the fact that `no_trade`/negative evaluation remains a valid research result.

- [ ] **Step 6: Run focused + full verification**

Run:

```bash
pytest tests/calibration/test_phase9_deployment_assets.py -v
ruff check .
pytest
bash -n scripts/deploy/phase9_host_acceptance.sh
bash -n scripts/deploy/phase9_cloudshell_accept.sh
```

Expected: all PASS / clean.

- [ ] **Step 7: Commit**

```bash
git add scripts/deploy/phase9_* docs/PHASE-9-DEPLOYMENT.md tests/calibration/test_phase9_deployment_assets.py .github/workflows/ci.yml
git commit -m "feat: add Phase 9 production acceptance"
```

---

### Task 10: Pre-host release gate

**Files:**
- No semantic code changes unless verification exposes a defect.
- Update PR conversation with exact evidence.

- [ ] **Step 1: Run/fetch fresh exact-head CI**

Require full CI success on the final candidate SHA.

- [ ] **Step 2: Require fresh exact-head Historical Backfill Smoke**

Require success on the same SHA.

- [ ] **Step 3: Require fresh exact-head Live Recorder Smoke**

Require success on the same SHA.

- [ ] **Step 4: Require fresh exact-head Recorder Short Soak**

Require success on the same SHA.

- [ ] **Step 5: Verify PR head and safety**

Confirm draft PR head equals the exact candidate, mergeability is true, and no code enables live trading.

- [ ] **Step 6: Publish only the exact-SHA Cloud Shell acceptance command**

The command must download `scripts/deploy/phase9_cloudshell_accept.sh` from that exact SHA and set `PHASE9_HEAD` to the same SHA.

Phase 10 remains blocked until host acceptance returns PASS, Phase 9 closeout is committed, fresh closeout-head gates pass, and the Phase 9 PR is merged with an expected-head guard.
