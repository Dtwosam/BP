# Phase 10 Live Prediction Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a money-disabled prospective live prediction service that applies the accepted Phase 9 policy before outcome, stores immutable prediction evidence, and appends official-outcome evaluation without rewriting the prediction.

**Architecture:** Load a restricted final train/validation-frozen policy from explicit Phase 9 registry runs, observe only the live CLOB market-price and compact selected-side book inputs required by the accepted `market_price` policy, and persist one immutable V1 prediction per market inside a strict scheduling deadline. A separate append-only evaluator joins official `official-outcome-v1` labels after resolution. The always-on service fails closed unless research mode, live trading disabled and zero limits are all present.

**Tech Stack:** Python 3.12, SQLAlchemy 2, PostgreSQL 16, `httpx` through the existing Polymarket CLOB price-history client, asyncio, systemd, pytest, Ruff, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-26-phase-10-live-prediction-engine-design.md`

## Global Constraints

- Production prediction version: `live-prediction-v1`.
- Live input version: `phase10-live-market-input-v1`.
- Accepted source calibration version: `platt-or-identity-v1`.
- Accepted source edge version: `selected-ask-edge-v1`.
- Production source 5m run: `phase9-300-c9f0e00eb7836af08008c66909f8f179`, semantic `c9f0e00eb7836af08008c66909f8f179f03089413426508469353c75bcbcae24`.
- Production source 15m run: `phase9-900-15c234f25588b23cce73a12f87a2e2ea`, semantic `15c234f25588b23cce73a12f87a2e2ea9087490055f203f22f183594b4bcfacd`.
- `scheduled_at = market_start_at + selected_offset_seconds` and V1 `max_lateness_seconds = 10`.
- No prediction may be backfilled after its deadline or at/after market end.
- CLOB model probability comes only from official `/prices-history` ending at `scheduled_at`; no midpoint, opposite-token transform or WebSocket price substitution.
- Compact book observations require both `bucket_at <= scheduled_at` and receipt-based `last_event_at <= scheduled_at`; existing 10-second freshness semantics remain.
- Prediction computation must not query official labels/outcomes.
- `live_predictions` is immutable; outcomes live only in append-only `live_prediction_evaluations`.
- A stored `trade=true` is a research decision only; Phase 10 contains no order, wallet, signing, allowance, paper-fill or position path.
- Process startup requires research mode, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`.
- Phase 10 production acceptance is prospective and must never substitute historical replay for live evidence.

---

### Task 1: Restricted Phase 9 Live Policy Source

**Files:**
- Create: `src/bp_engine/live_prediction/__init__.py`
- Create: `src/bp_engine/live_prediction/models.py`
- Create: `src/bp_engine/live_prediction/policy.py`
- Test: `tests/live_prediction/test_policy.py`
- Test: `tests/live_prediction/test_policy_postgres.py`

**Interfaces:**
- Consumes: `CalibrationEdgeRunRepository.get(Connection, run_id)`, `ModelTrainingRunRepository.get`, `market_labels`, accepted Phase 9/8/7 provenance.
- Produces: `LIVE_PREDICTION_VERSION`, `LIVE_INPUT_VERSION`, `LivePolicySpec`, `load_live_policy(connection, run_id) -> LivePolicySpec`, `LivePolicyIntegrityError`, `LivePolicyNotFound`.

- [ ] **Step 1: Write failing policy-contract tests**

Cover exact stored/report identity, implementation version checks, final partition markers, valid offset/policy/calibration fit, exact source-training `market_price` contract, training-prior reconstruction from final train ids only, and a returned spec that has no holdout metrics/targets/predictions attributes.

Example core assertions:

```python
policy = load_live_policy(connection, accepted_phase9_run_id)
assert policy.calibration_version == "platt-or-identity-v1"
assert policy.edge_policy_version == "selected-ask-edge-v1"
assert policy.selected_offset_seconds > 0
assert policy.calibration_fit.method in {"identity", "platt"}
assert policy.edge_policy in {"trade_threshold", "no_trade"}
assert not hasattr(policy, "final_holdout_metrics")
assert not hasattr(policy, "holdout_condition_ids")
```

Mutate only final-holdout metrics/predictions in a synthetic stored report while keeping immutable selection fields fixed and assert the returned `LivePolicySpec` is unchanged; mutate selection fields and assert fail closed.

- [ ] **Step 2: Run RED tests**

Run:

```bash
pytest tests/live_prediction/test_policy.py tests/live_prediction/test_policy_postgres.py -q
```

Expected: collection succeeds and tests fail because `bp_engine.live_prediction.policy` does not exist.

- [ ] **Step 3: Implement minimal restricted loader**

Define frozen dataclasses:

```python
@dataclass(frozen=True)
class LivePolicySpec:
    source_calibration_run_id: str
    source_calibration_semantic_sha256: str
    source_backtest_run_id: str
    source_backtest_semantic_sha256: str
    source_training_run_id: str
    source_training_semantic_sha256: str
    calibration_version: str
    edge_policy_version: str
    source_feature_version: str
    label_version: str
    horizon_seconds: int
    selected_offset_seconds: int
    calibration_fit: CalibrationFit
    edge_config: EdgeConfig
    edge_policy: str
    min_edge: float | None
    training_prior: float
    policy_sha256: str
```

Validate stored immutable columns against report, require final `calibration_selection_fit_partition="train"`, `calibration_selection_partition="validation"`, `edge_selection_partition="validation"`, `evaluation_partition="holdout"`, then read only the final selection objects. Reconstruct prior from official labels for the final train condition ids and verify both classes plus finite `0 < prior < 1`. Verify the source Phase 7 training run's champion/config is the exact accepted `market_price` spec already enforced by Phase 8 predictor loading.

- [ ] **Step 4: Run GREEN tests and full CI subset**

```bash
ruff check src/bp_engine/live_prediction tests/live_prediction
pytest tests/live_prediction/test_policy.py tests/live_prediction/test_policy_postgres.py -q
pytest tests/calibration tests/backtesting -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bp_engine/live_prediction tests/live_prediction
 git commit -m "feat: load restricted live prediction policy"
```

### Task 2: Generic Edge Input Without Unknown Outcome

**Files:**
- Modify: `src/bp_engine/calibration/edge.py`
- Test: `tests/calibration/test_edge.py`
- Create: `tests/live_prediction/test_edge_adapter.py`

**Interfaces:**
- Consumes: mapping of predictor names to values.
- Produces: `edge_decision_from_predictors(predictors: Mapping[str, Any], probability_up: float, config: EdgeConfig, min_edge: float | None) -> EdgeDecision`; existing `edge_decision(row, ...)` remains a behavior-identical wrapper.

- [ ] **Step 1: Write RED parity and no-target tests**

Build the same predictors both as a `SupervisedRow` and plain dict; assert both paths produce byte-for-byte equal `EdgeDecision`. Add selected-side missing/stale/ask tests through the mapping API.

```python
assert edge_decision(row, p, config, threshold) == edge_decision_from_predictors(
    row.predictors, p, config, threshold
)
```

- [ ] **Step 2: Run RED**

```bash
pytest tests/calibration/test_edge.py tests/live_prediction/test_edge_adapter.py -q
```

Expected: fail only because the new mapping function is absent.

- [ ] **Step 3: Extract the existing calculation into the mapping primitive**

Do not change fee, spread, selected-side, missing/stale, threshold or `no_trade` semantics. Keep the old public function as:

```python
def edge_decision(row, probability_up, config, min_edge):
    return edge_decision_from_predictors(row.predictors, probability_up, config, min_edge)
```

- [ ] **Step 4: Verify Phase 9 regression parity**

```bash
ruff check src/bp_engine/calibration/edge.py tests/calibration/test_edge.py tests/live_prediction/test_edge_adapter.py
pytest tests/calibration -q
```

Expected: PASS with Phase 9 semantics unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/bp_engine/calibration/edge.py tests/calibration/test_edge.py tests/live_prediction/test_edge_adapter.py
 git commit -m "refactor: expose target-free edge decision input"
```

### Task 3: Prospective Live Input Observer

**Files:**
- Create: `src/bp_engine/live_prediction/inputs.py`
- Test: `tests/live_prediction/test_inputs.py`
- Test: `tests/live_prediction/test_inputs_postgres.py`

**Interfaces:**
- Consumes: `PolymarketPriceHistoryClient`, `FeatureSourceReader.latest_state`, market token ids, `scheduled_at`.
- Produces: `LiveMarketInput`, `observe_live_input(...) -> LiveMarketInput`, `LiveInputDeadlineExceeded`, `LiveInputIntegrityError`.

- [ ] **Step 1: Write RED input tests**

Cover:

- CLOB request uses Up token, fidelity 1 minute and `end=scheduled_at`;
- newest point `<= scheduled_at` selected;
- a post-scheduled point is rejected/ignored;
- response request params, SHA and actual download time are stored;
- no price becomes explicit `market_probability_observed=False` rather than midpoint fallback;
- Up/Down compact states load by exact token ids;
- state with `last_event_at > scheduled_at` cannot be selected;
- stale state remains explicit;
- an injected clock crossing `scheduled_at + 10s` raises deadline exceeded before a prediction can be stored.

- [ ] **Step 2: Run RED**

```bash
pytest tests/live_prediction/test_inputs.py tests/live_prediction/test_inputs_postgres.py -q
```

Expected: missing module failures only.

- [ ] **Step 3: Implement observer and immutable input dataclass**

`LiveMarketInput` stores the full price-response provenance plus both book snapshots/cutoffs and a canonical `input_fingerprint`. Construct Phase 9 predictor keys using existing `book_state("pm_up", ...)` and `book_state("pm_down", ...)` outputs plus `pm_up_price`.

Use injected async client and clock for deterministic tests. Do not write the historical `polymarket_price_history` table from this live observation path.

- [ ] **Step 4: Verify**

```bash
ruff check src/bp_engine/live_prediction/inputs.py tests/live_prediction
pytest tests/live_prediction/test_inputs.py tests/live_prediction/test_inputs_postgres.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bp_engine/live_prediction/inputs.py tests/live_prediction
 git commit -m "feat: observe timing-safe live market inputs"
```

### Task 4: Immutable Prediction and Evaluation Ledgers

**Files:**
- Create: `migrations/0010_live_predictions.sql`
- Modify: `src/bp_engine/storage/schema.py`
- Create: `src/bp_engine/live_prediction/repository.py`
- Test: `tests/live_prediction/test_repository.py`
- Test: `tests/live_prediction/test_repository_postgres.py`

**Interfaces:**
- Consumes: prediction/evaluation dataclasses from `models.py`.
- Produces: `live_predictions`, `live_prediction_evaluations`, `LivePredictionRepository`, `LivePredictionEvaluationRepository`, `LivePredictionConflict`, `LivePredictionEvaluationConflict`.

- [ ] **Step 1: Write RED schema/repository tests**

Assert table/migration names, required columns, natural-key uniqueness, first insert, identical rerun existing/no rewrite, semantic conflict rejection, and child evaluation behavior. Snapshot the stored prediction before and after evaluation insert and assert equality.

- [ ] **Step 2: Run RED**

```bash
pytest tests/live_prediction/test_repository.py tests/live_prediction/test_repository_postgres.py -q
```

Expected: missing tables/repositories.

- [ ] **Step 3: Implement migration/schema/repositories**

Use immutable natural key `(condition_id, prediction_version)` and child key `(prediction_id, label_version)`. Validate 64-char hex hashes, aware timestamps, market window ordering and semantic equality before treating reruns as existing. Never implement update/delete methods.

- [ ] **Step 4: Verify SQLite + PostgreSQL**

```bash
ruff check src/bp_engine/storage/schema.py src/bp_engine/live_prediction/repository.py tests/live_prediction
pytest tests/live_prediction/test_repository.py tests/live_prediction/test_repository_postgres.py -q
```

Expected: PASS and original `recorded_at` preserved on rerun.

- [ ] **Step 5: Commit**

```bash
git add migrations/0010_live_predictions.sql src/bp_engine/storage/schema.py src/bp_engine/live_prediction/repository.py tests/live_prediction
 git commit -m "feat: add immutable live prediction ledgers"
```

### Task 5: Deadline-Safe Prediction Computation

**Files:**
- Create: `src/bp_engine/live_prediction/predictor.py`
- Test: `tests/live_prediction/test_predictor.py`

**Interfaces:**
- Consumes: `LivePolicySpec`, market metadata, `LiveMarketInput`, `edge_decision_from_predictors`.
- Produces: `build_live_prediction(...) -> LivePrediction`, `PredictionDeadlineError`, `PredictionIntegrityError`.

- [ ] **Step 1: Write RED predictor tests**

Cover raw observed price vs training-prior fallback, identity/Platt calibration, Up/Down side, exact Phase 9 edge policy, `no_trade`, source provenance, deterministic semantic hash/id, and all timing constraints. Include a test proving the function has no label/outcome argument or DB label dependency.

- [ ] **Step 2: Run RED**

```bash
pytest tests/live_prediction/test_predictor.py -q
```

Expected: missing predictor module.

- [ ] **Step 3: Implement pure deterministic builder**

Use `apply_calibration(policy.calibration_fit, (raw_probability,))[0]`, then target-free edge computation. Set `scheduled_at` from the market/policy, accept injected `recorded_at`, reject early/late/end-of-market timestamps, and include all source/policy/input provenance in the canonical semantic hash.

`prediction_id` is derived from the stable natural identity `(condition_id, live-prediction-v1)`; semantic conflicts are handled by repository, not by generating another id.

- [ ] **Step 4: Verify**

```bash
ruff check src/bp_engine/live_prediction/predictor.py tests/live_prediction/test_predictor.py
pytest tests/live_prediction/test_predictor.py tests/calibration -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bp_engine/live_prediction/predictor.py tests/live_prediction/test_predictor.py
 git commit -m "feat: build deadline-safe live predictions"
```

### Task 6: Official Outcome Evaluation Without Prediction Rewrite

**Files:**
- Create: `src/bp_engine/live_prediction/evaluation.py`
- Test: `tests/live_prediction/test_evaluation.py`
- Test: `tests/live_prediction/test_evaluation_postgres.py`

**Interfaces:**
- Consumes: unevaluated stored predictions and `official-outcome-v1` label rows.
- Produces: `evaluate_prediction(...) -> LivePredictionEvaluation`, `append_available_evaluations(connection, ...)`, integrity errors.

- [ ] **Step 1: Write RED evaluation tests**

Cover label/window match, `source_observed_at >= market_end`, `prediction.recorded_at < market_end`, `prediction.recorded_at < label.source_observed_at`, correctness, row-level log-loss/Brier, research hypothetical P&L only for stored trade decisions, evaluation idempotence and contradictory outcome conflict. Hash the prediction row before/after evaluation insertion and assert unchanged.

- [ ] **Step 2: Run RED**

```bash
pytest tests/live_prediction/test_evaluation.py tests/live_prediction/test_evaluation_postgres.py -q
```

Expected: missing evaluation module.

- [ ] **Step 3: Implement append-only evaluator**

Query only existing immutable `market_labels` rows. Do not fetch Gamma or infer resolution. Compute evaluation from stored prediction values and label target, then insert through the child repository.

- [ ] **Step 4: Verify**

```bash
ruff check src/bp_engine/live_prediction/evaluation.py tests/live_prediction
pytest tests/live_prediction/test_evaluation.py tests/live_prediction/test_evaluation_postgres.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bp_engine/live_prediction/evaluation.py tests/live_prediction
 git commit -m "feat: append live prediction evaluations"
```

### Task 7: Due-Market Scheduler and Safety-Interlocked Service

**Files:**
- Create: `src/bp_engine/live_prediction/service.py`
- Create: `src/bp_engine/live_prediction/__main__.py`
- Test: `tests/live_prediction/test_service.py`

**Interfaces:**
- Consumes: policies by horizon, `polymarket_markets`, input observer, predictor, repositories/evaluator, Settings.
- Produces: `LivePredictionService`, `load_due_markets`, `ensure_live_prediction_safety`, `run_live_prediction_service`.

- [ ] **Step 1: Write RED safety/scheduling tests**

Test startup rejection for paper/live mode, live flag true, positive trade limit or positive daily-loss limit. Test due-market selection by exact offset/deadline, unsupported horizon exclusion, resolved/closed exclusion, already-predicted exclusion, no pre-scheduled prediction, one market failure isolation, restart idempotence and periodic evaluation append.

Mock any attempted order/auth module and assert it is never imported/called.

- [ ] **Step 2: Run RED**

```bash
pytest tests/live_prediction/test_service.py -q
```

Expected: missing service module.

- [ ] **Step 3: Implement one-second async service loop**

Use DB transactions per market. Policies load once at startup and integrity failure is fatal. Market observation/network failure logs a structured miss and continues. Check the wall clock immediately after input observation; if the lateness deadline has passed, do not call repository store.

- [ ] **Step 4: Verify service regression**

```bash
ruff check src/bp_engine/live_prediction tests/live_prediction
pytest tests/live_prediction/test_service.py tests/live_prediction -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bp_engine/live_prediction tests/live_prediction
 git commit -m "feat: run safety-interlocked live prediction service"
```

### Task 8: CLI, Integrity Report and Systemd Deployment

**Files:**
- Create: `src/bp_engine/live_prediction/cli.py`
- Create: `scripts/run_live_prediction.py`
- Create: `scripts/report_live_predictions.py`
- Create: `deploy/bp-live-predictor.service`
- Test: `tests/live_prediction/test_cli.py`
- Test: `tests/live_prediction/test_deployment_assets.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: live service/evaluator/repositories.
- Produces: long-running service entry point and read-only integrity report.

- [ ] **Step 1: Write RED CLI/deployment tests**

Require repeated explicit `--source-calibration-run-id`, default one-second poll and 10-second lateness, duplicate source rejection, service environment loading, no trade/wallet/order flags, systemd unprivileged user, restart policy, exact executable, and CI syntax validation.

The report must emit machine-readable counts for scheduled eligible markets, predictions, late/missed coverage, pre-outcome timing violations, duplicate natural keys, semantic-hash violations, evaluation count and prediction-mutation violations.

- [ ] **Step 2: Run RED**

```bash
pytest tests/live_prediction/test_cli.py tests/live_prediction/test_deployment_assets.py -q
```

Expected: missing CLI/scripts/service asset.

- [ ] **Step 3: Implement CLI/report/systemd asset**

The runtime CLI starts only the money-disabled predictor. The report is read-only and never creates historical predictions. Systemd uses `User=bp`, `Group=bp`, the production env file and an isolated service name; it must not receive Docker socket access.

- [ ] **Step 4: Verify syntax/full live-prediction suite**

```bash
ruff check .
pytest tests/live_prediction -q
python -m bp_engine.health
```

Also run `bash -n` for all new shell scripts once Task 10 adds them; CI will carry the final syntax gate.

- [ ] **Step 5: Commit**

```bash
git add src/bp_engine/live_prediction scripts deploy .github/workflows/ci.yml tests/live_prediction
 git commit -m "feat: package live prediction service"
```

### Task 9: Consolidated Prospective-Proof Contract

**Files:**
- Create: `tests/live_prediction/test_phase10_contract.py`

**Interfaces:**
- Consumes: all Phase 10 public interfaces.
- Produces: executable acceptance-level regression proof before deployment scripts.

- [ ] **Step 1: Add end-to-end contract tests**

Construct a synthetic market/policy/live input timeline and prove:

- prediction is persisted before a label exists;
- label insertion/evaluation later does not alter prediction bytes or hash;
- final-holdout source metrics cannot change live policy extraction;
- post-scheduled price/state observations cannot enter the prediction;
- a deadline miss stays absent even if rerun after resolution;
- a frozen `trade=true` remains data only and has no order effect;
- 15m `no_trade` stays `no_trade`;
- source/provenance hashes survive a repository reload.

- [ ] **Step 2: Run the contract**

```bash
pytest tests/live_prediction/test_phase10_contract.py -q
```

Expected: PASS without product changes. If it fails, diagnose the integrity gap and fix the owning earlier component test-first rather than weakening this contract.

- [ ] **Step 3: Run full project suite**

```bash
ruff check .
pytest
python -m bp_engine.health
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/live_prediction/test_phase10_contract.py
 git commit -m "test: lock Phase 10 prospective prediction contract"
```

### Task 10: Production Acceptance Assets and Host Proof

**Files:**
- Create: `scripts/deploy/phase10_host_acceptance.sh`
- Create: `scripts/deploy/phase10_cloudshell_accept.sh`
- Create: `docs/PHASE-10-DEPLOYMENT.md`
- Create: `tests/live_prediction/test_phase10_deployment_assets.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: exact candidate SHA, production DB/recorder, pinned accepted Phase 9 source ids.
- Produces: prospective host evidence and final `VERDICT=PASS` / `PHASE10_HOST_ACCEPTANCE=PASS` only when timing/provenance/safety gates pass.

- [ ] **Step 1: Write RED deployment-asset tests**

Require:

- `BP_VERIFIED_HEAD` exact match;
- pinned Phase 9 source ids/semantics;
- migration `0010_live_predictions.sql`;
- live false/zero limits and research mode;
- recorder before/after active;
- bounded disk-health before/after;
- unprivileged predictor service;
- exact-SHA root-owned worktree -> `git archive` -> bp-owned source Cloud Shell architecture;
- machine evidence tokens for prediction count by horizon, coverage/misses, max lateness, pre-outcome violations, source-cutoff violations, semantic-hash violations, duplicate keys, evaluation mutation violations, order-side-effect violations;
- no requirement that `trade=true` count or P&L be positive;
- no global `safe.directory`, Git-worktree chown or order/wallet code.

- [ ] **Step 2: Run RED**

```bash
pytest tests/live_prediction/test_phase10_deployment_assets.py -q
```

Expected: deployment assets absent.

- [ ] **Step 3: Implement host gate and Cloud Shell wrapper**

The host acceptance run must be genuinely prospective. It installs candidate code/migration, starts `bp-live-predictor`, observes future verified markets long enough to obtain at least one pre-outcome prediction for each horizon when available, records honest misses, and never synthesizes late predictions. It may append evaluations only for official labels that become available; otherwise evaluation evidence is pending rather than fabricated.

Host pass requires zero timing/provenance/immutability/order violations, not positive economics.

- [ ] **Step 4: Add CI shell syntax gates and run full exact-head workflows**

```bash
bash -n scripts/deploy/phase10_host_acceptance.sh
bash -n scripts/deploy/phase10_cloudshell_accept.sh
ruff check .
pytest
```

Then require fresh exact-head:

- CI;
- Historical Backfill Smoke;
- Live Recorder Smoke;
- Recorder Short Soak.

- [ ] **Step 5: Freeze candidate and run production host acceptance**

Keep the PR draft. Record the exact candidate SHA and all four pre-host gate runs. The user runs only the pinned Cloud Shell helper because GCP execution is outside this agent's connectors.

Do not close Phase 10 until production output contains both `VERDICT=PASS` and `PHASE10_HOST_ACCEPTANCE=PASS` with zero integrity/safety violations.

- [ ] **Step 6: Close out and merge**

After host PASS only:

- add sanitized `docs/evidence/phase-10-closeout-20260826.json`;
- update `PROJECT_STATE.json`, including correction of the Phase 9 implementation version strings to `platt-or-identity-v1` and `selected-ask-edge-v1`;
- update `START-HERE.md`, append `docs/DECISION-LOG.md`, and prepend `docs/CHANGELOG.md`;
- advance source of truth to Phase 11 dashboard-ready while keeping live trading false;
- require fresh all-four exact-head closeout workflows;
- verify no review threads and unchanged PR head;
- mark PR ready and merge with `expected_head_sha`.
