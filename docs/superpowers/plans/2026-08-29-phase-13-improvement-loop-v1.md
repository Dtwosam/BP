# Phase 13 Improvement Loop V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an immutable, reproducible champion/challenger improvement loop that can test explicit hypotheses, compare challengers with accepted Phase 9 policies, and record deliberate research/paper promotion decisions without reusing known holdouts as fresh confirmation or enabling live trading.

**Architecture:** Add a focused `bp_engine.improvement` package around the existing immutable modeling/backtest/calibration/live-prediction/paper-execution records. The package freezes experiment specs first, validates exact evidence roles/provenance, computes deterministic paired economic uncertainty and calibration guardrails, stores immutable evaluations and decisions, and exposes a network-free CLI. The first concrete challenger tests a 5m spread/abstention guard using the existing Phase 9 edge machinery; known Phase 8/9 holdouts are development/legacy evidence only, never fresh promotion confirmation.

**Tech Stack:** Python 3.12, dataclasses, `hashlib`/canonical JSON, SQLAlchemy Core, PostgreSQL 16, NumPy for deterministic resampling, existing BP modeling/backtesting/calibration/execution packages, pytest, Ruff, Bash deployment validation.

**Spec:** `docs/superpowers/specs/2026-08-29-phase-13-improvement-loop-v1-design.md`

## Global Constraints

- Keep `LIVE_TRADING_ENABLED=false` and real-money limits at zero.
- No wallet, signer, allowance, CLOB placement/cancel, or live execution path may be added.
- Existing Phase 9 champions are `phase9-300-c9f0e00eb7836af08008c66909f8f179` and `phase9-900-15c234f25588b23cce73a12f87a2e2ea`.
- Existing Phase 8/9 final holdouts are already observed project evidence and are not eligible as new Phase 13 `fresh_holdout` confirmation.
- Selection may use only development train/validation evidence; ordinary OOS is diagnostic/development evidence once repeatedly consulted.
- Fresh promotion confirmation must be chronologically later or prospective and frozen after the experiment/challenger semantics are frozen.
- Independent-confirmation identifier sets cannot be reused by a different experiment as fresh confirmation.
- Promotion requires positive paired economic delta with 95% lower bootstrap bound strictly above zero plus challenger calibrated log loss <= champion and challenger calibrated Brier <= champion on the same independent-confirmation markets.
- Inconclusive evidence keeps the champion. Promotion eligibility never automatically changes the champion.
- All new records are immutable; identical reruns are no-op/existing and semantic rewrites fail closed.
- Phase 13 may promote only future research/paper policy selection. Live activation remains Phase 14 plus explicit authorization.

---

## File Structure

### New package

- `src/bp_engine/improvement/__init__.py` — public constants/types.
- `src/bp_engine/improvement/__main__.py` — CLI module entrypoint.
- `src/bp_engine/improvement/models.py` — frozen experiment/evaluation/decision/evidence dataclasses and enums.
- `src/bp_engine/improvement/hashing.py` — canonical payload conversion, SHA-256 helpers, deterministic ids/seeds.
- `src/bp_engine/improvement/repository.py` — append-only Postgres stores and prior-confirmation lookup.
- `src/bp_engine/improvement/evidence.py` — evidence-role and temporal/reuse validation.
- `src/bp_engine/improvement/statistics.py` — deterministic paired market bootstrap, drawdown and losing-streak helpers.
- `src/bp_engine/improvement/comparison.py` — pure promotion eligibility logic.
- `src/bp_engine/improvement/source.py` — read-only accepted champion/source ledger loaders.
- `src/bp_engine/improvement/service.py` — register/evaluate/decide/report orchestration.
- `src/bp_engine/improvement/challenger.py` — first 5m spread/abstention challenger construction using existing Phase 9 machinery.
- `src/bp_engine/improvement/cli.py` — JSON command surface.

### Storage/deployment

- `src/bp_engine/storage/schema.py` — SQLAlchemy tables.
- `migrations/0013_improvement_loop.sql` — Postgres migration.
- `scripts/run_improvement.py` — thin CLI wrapper.
- `scripts/deploy/phase13_host_acceptance.sh` — production acceptance.
- `scripts/deploy/phase13_cloudshell_accept.sh` — exact-SHA Cloud Shell helper.
- `.github/workflows/ci.yml` — syntax/compile validation for new deployment assets.
- `docs/PHASE-13-RESEARCH.md` — operator/research runbook.

### Tests

- `tests/improvement/test_models_hashing.py`
- `tests/improvement/test_repository_postgres.py`
- `tests/improvement/test_evidence.py`
- `tests/improvement/test_statistics.py`
- `tests/improvement/test_comparison.py`
- `tests/improvement/test_source_postgres.py`
- `tests/improvement/test_service_postgres.py`
- `tests/improvement/test_challenger_postgres.py`
- `tests/improvement/test_cli.py`
- `tests/improvement/test_phase13_deployment_assets.py`

---

### Task 1: Frozen improvement models and deterministic hashing

**Files:**
- Create: `src/bp_engine/improvement/__init__.py`
- Create: `src/bp_engine/improvement/models.py`
- Create: `src/bp_engine/improvement/hashing.py`
- Test: `tests/improvement/test_models_hashing.py`

**Interfaces:**
- Produces: `EXPERIMENT_VERSION = "improvement-experiment-v1"`
- Produces: `EVALUATION_VERSION = "improvement-evaluation-v1"`
- Produces: `DECISION_VERSION = "improvement-decision-v1"`
- Produces enums `ChangeFamily`, `EvidenceRole`, `PromotionDecision`
- Produces dataclasses `ChampionRef`, `EvidenceItem`, `ImprovementExperimentSpec`, `PolicyMetrics`, `ImprovementEvaluationReport`, `ImprovementPromotionDecision`
- Produces `canonical_payload(value) -> JSON-compatible object`, `semantic_sha256(value) -> str`, `derive_id(prefix, semantic_sha256) -> str`, `derive_seed(*parts) -> int`

- [ ] **Step 1: Write the failing model/hash tests**

Create tests that require deterministic canonical hashing across dict ordering and timezone-aware dataclasses, validate enum values, reject blank hypotheses, reject naive timestamps, reject non-64-character semantic hashes, and prove creation timestamps are excluded from experiment semantic identity.

Core test shape:

```python
from datetime import UTC, datetime, timedelta

from bp_engine.improvement.hashing import semantic_sha256
from bp_engine.improvement.models import (
    EXPERIMENT_VERSION,
    ChangeFamily,
    ChampionRef,
    ImprovementExperimentSpec,
)


def champion() -> ChampionRef:
    return ChampionRef(
        calibration_run_id="phase9-300-c9f0e00eb7836af08008c66909f8f179",
        calibration_semantic_sha256="a" * 64,
        backtest_run_id="phase8-300-example",
        backtest_semantic_sha256="b" * 64,
        training_run_id="phase7-300-example",
        training_semantic_sha256="c" * 64,
    )


def test_experiment_semantics_ignore_created_at():
    base = dict(
        experiment_version=EXPERIMENT_VERSION,
        hypothesis="A max-spread guard reduces negative executable outcomes.",
        horizon_seconds=300,
        change_family=ChangeFamily.ABSTENTION,
        champion=champion(),
        challenger={"max_spread_grid": [0.02, 0.04, 0.06]},
        source_versions={"feature": "core-v1", "label": "official-outcome-v1"},
        research_start=datetime(2026, 8, 24, tzinfo=UTC),
        research_end=datetime(2026, 8, 25, tzinfo=UTC),
        selection_policy={"allowed_roles": ["development_validation"]},
        confirmation_policy={"allowed_roles": ["fresh_holdout", "prospective_paper"]},
        cost_assumptions={"fee_rate": 0.07, "slippage_buffer": 0.01},
        primary_metric="net_pnl_delta",
        guardrail_metrics=("calibrated_log_loss", "calibrated_brier"),
    )
    first = ImprovementExperimentSpec.build(**base, created_at=datetime(2026, 8, 29, tzinfo=UTC))
    second = ImprovementExperimentSpec.build(**base, created_at=datetime(2026, 8, 30, tzinfo=UTC))
    assert first.experiment_id == second.experiment_id
    assert first.semantic_sha256 == second.semantic_sha256
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
pytest tests/improvement/test_models_hashing.py -q
```

Expected: collection/import failure because `bp_engine.improvement` does not exist.

- [ ] **Step 3: Implement minimal frozen models and hashing**

Implementation rules:

```python
class ChangeFamily(StrEnum):
    FEATURE = "feature"
    MODEL = "model"
    CALIBRATION = "calibration"
    TIMING = "timing"
    ABSTENTION = "abstention"
    COST_ASSUMPTION = "cost_assumption"

class EvidenceRole(StrEnum):
    DEVELOPMENT_TRAIN = "development_train"
    DEVELOPMENT_VALIDATION = "development_validation"
    ORDINARY_OOS = "ordinary_oos"
    FRESH_HOLDOUT = "fresh_holdout"
    PROSPECTIVE_PAPER = "prospective_paper"

class PromotionDecision(StrEnum):
    REJECT_CHALLENGER = "reject_challenger"
    KEEP_CHAMPION = "keep_champion"
    PROMOTE_CHALLENGER = "promote_challenger"
```

`ImprovementExperimentSpec.build(...)` must canonicalize a semantic payload that excludes `created_at`, derive `semantic_sha256`, and set `experiment_id = f"phase13-exp-{digest[:32]}"`.

`derive_seed(*parts)` must hash UTF-8 joined parts with SHA-256 and convert the first 8 bytes to an unsigned integer; it must not use Python's randomized `hash()`.

- [ ] **Step 4: Run focused tests and Ruff**

```bash
pytest tests/improvement/test_models_hashing.py -q
ruff check src/bp_engine/improvement tests/improvement/test_models_hashing.py
```

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/bp_engine/improvement tests/improvement/test_models_hashing.py
git commit -m "feat: add phase13 experiment models"
```

---

### Task 2: Append-only Phase 13 schema and repositories

**Files:**
- Create: `migrations/0013_improvement_loop.sql`
- Modify: `src/bp_engine/storage/schema.py`
- Create: `src/bp_engine/improvement/repository.py`
- Test: `tests/improvement/test_repository_postgres.py`

**Interfaces:**
- Consumes Task 1 dataclasses.
- Produces SQLAlchemy tables `improvement_experiments`, `improvement_evaluations`, `improvement_promotion_decisions`.
- Produces repositories `ImprovementExperimentRepository`, `ImprovementEvaluationRepository`, `ImprovementPromotionDecisionRepository`.
- Produces conflicts `ImprovementExperimentConflict`, `ImprovementEvaluationConflict`, `ImprovementPromotionDecisionConflict`.
- Produces `confirmation_identifiers_used_by_other_experiments(connection, *, experiment_id, identifiers) -> set[str]`.

- [ ] **Step 1: Write RED Postgres repository tests**

Test all three immutable records with this contract:

```python
created = repo.store(connection, record)
assert created.created is True
assert created.existing is False
connection.commit()

existing = repo.store(connection, record)
assert existing.created is False
assert existing.existing is True

mutated = replace(record, hypothesis="Different semantics")
with pytest.raises(ImprovementExperimentConflict):
    repo.store(connection, mutated)
```

Also insert two evaluations with different experiment ids and overlapping `fresh_holdout` evidence identifiers, then require `confirmation_identifiers_used_by_other_experiments(...)` to return the overlap.

- [ ] **Step 2: Verify RED**

```bash
pytest tests/improvement/test_repository_postgres.py -q
```

Expected: missing migration/schema/repository failures.

- [ ] **Step 3: Implement migration and SQLAlchemy tables**

`0013_improvement_loop.sql` must create the three tables with unique immutable ids, positive horizon checks, JSONB payloads, 64-character hash checks where practical, foreign-key-like logical ids stored as strings, and indexes on horizon/created time, experiment id, evaluation id, and promotion eligibility.

Do **not** add triggers that mutate source ledgers.

- [ ] **Step 4: Implement repository semantics**

Use the existing Phase 7/9 repository pattern: load by id, compare stored semantic hash plus canonical report/spec/decision JSON, return existing on exact match, fail closed on conflict.

The fresh-confirmation lookup must inspect prior evaluation evidence manifests and only consider items whose role is `fresh_holdout` or `prospective_paper`.

- [ ] **Step 5: Run focused Postgres tests plus migration compatibility**

```bash
pytest tests/improvement/test_repository_postgres.py -q
pytest tests/execution/test_repository_postgres.py tests/dashboard/test_phase12_paper_repository_postgres.py -q
ruff check src/bp_engine/improvement src/bp_engine/storage/schema.py tests/improvement
```

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

```bash
git add migrations/0013_improvement_loop.sql src/bp_engine/storage/schema.py src/bp_engine/improvement/repository.py tests/improvement/test_repository_postgres.py
git commit -m "feat: persist immutable phase13 experiments"
```

---

### Task 3: Evidence-role, temporal, and source-provenance validation

**Files:**
- Create: `src/bp_engine/improvement/evidence.py`
- Create: `src/bp_engine/improvement/source.py`
- Test: `tests/improvement/test_evidence.py`
- Test: `tests/improvement/test_source_postgres.py`

**Interfaces:**
- Produces `validate_evidence_manifest(...) -> None`.
- Produces `load_champion_ref(connection, calibration_run_id) -> ChampionRef`.
- Produces `load_phase9_report(connection, calibration_run_id) -> Mapping[str, Any]`.
- Produces `EvidenceIntegrityError` and `ChampionIntegrityError`.

- [ ] **Step 1: Write evidence RED tests**

Required cases:

```python
with pytest.raises(EvidenceIntegrityError, match="known legacy final holdout"):
    validate_evidence_manifest(
        experiment=experiment,
        evidence=(legacy_final_as_fresh_holdout,),
        prior_confirmation_identifiers=set(),
    )

with pytest.raises(EvidenceIntegrityError, match="must post-date challenger freeze"):
    validate_evidence_manifest(
        experiment=experiment,
        evidence=(prospective_prediction_created_before_experiment,),
        prior_confirmation_identifiers=set(),
    )

with pytest.raises(EvidenceIntegrityError, match="already consumed"):
    validate_evidence_manifest(
        experiment=experiment,
        evidence=(fresh_holdout_item,),
        prior_confirmation_identifiers={fresh_holdout_item.identifier},
    )
```

Selection evidence containing `fresh_holdout`/`prospective_paper` is allowed only as confirmation, never as a parameter-selection role.

- [ ] **Step 2: Write source-loader RED tests**

Seed accepted-style `model_training_runs`, `backtest_runs`, and `calibration_edge_runs` rows. Require exact hash-chain reconstruction into `ChampionRef`. Corrupt any source hash and require `ChampionIntegrityError`.

- [ ] **Step 3: Verify RED**

```bash
pytest tests/improvement/test_evidence.py tests/improvement/test_source_postgres.py -q
```

- [ ] **Step 4: Implement evidence validation**

The experiment model must carry explicit `legacy_confirmation_identifiers` for known Phase 8/9 final holdout condition ids when a challenger adapter constructs a spec. `validate_evidence_manifest` rejects those identifiers when labeled `fresh_holdout`.

All evidence timestamps must be timezone-aware. `prospective_paper.observed_at` must be strictly greater than the experiment/challenger freeze timestamp.

- [ ] **Step 5: Implement accepted champion chain loader**

`load_champion_ref` must read the immutable Phase 9 row, verify its `source_backtest_run_id` and semantic hash, load the Phase 8 row, verify its source training run id/hash, load the Phase 7 row, and return the exact chain. No source record is mutated.

- [ ] **Step 6: Run focused tests and Ruff**

```bash
pytest tests/improvement/test_evidence.py tests/improvement/test_source_postgres.py -q
ruff check src/bp_engine/improvement tests/improvement
```

- [ ] **Step 7: Commit Task 3**

```bash
git add src/bp_engine/improvement/evidence.py src/bp_engine/improvement/source.py tests/improvement/test_evidence.py tests/improvement/test_source_postgres.py
git commit -m "feat: enforce phase13 evidence boundaries"
```

---

### Task 4: Deterministic uncertainty and promotion comparison

**Files:**
- Create: `src/bp_engine/improvement/statistics.py`
- Create: `src/bp_engine/improvement/comparison.py`
- Test: `tests/improvement/test_statistics.py`
- Test: `tests/improvement/test_comparison.py`

**Interfaces:**
- Produces `paired_bootstrap_mean_delta(pairs, *, seed, resamples=10_000) -> BootstrapInterval`.
- Produces `max_drawdown(values) -> float` and `max_losing_streak(values) -> int`.
- Produces `compare_policies(...) -> ComparisonResult`.
- Produces exact ineligibility reason codes.

- [ ] **Step 1: Write bootstrap RED tests**

Use condition-level pairs:

```python
pairs = (
    ("c1", -0.10, 0.20),
    ("c2", 0.00, 0.30),
    ("c3", -0.20, 0.10),
    ("c4", 0.05, 0.25),
)
first = paired_bootstrap_mean_delta(pairs, seed=1234)
second = paired_bootstrap_mean_delta(tuple(reversed(pairs)), seed=1234)
assert first == second
assert first.mean_delta > 0
```

The implementation must sort by condition id before resampling so input order cannot change the result.

Test empty/no-finite pairs fail closed rather than returning zero.

- [ ] **Step 2: Write comparison RED tests**

Cases:

- positive mean delta but lower bound <= 0 -> `promotion_eligible=False`, reason `economic_uncertainty_not_positive`;
- lower bound > 0 but challenger log loss worse -> ineligible `calibration_log_loss_worse`;
- lower bound > 0 but challenger Brier worse -> ineligible `calibration_brier_worse`;
- no independent confirmation -> ineligible `independent_confirmation_missing`;
- integrity violation -> ineligible `integrity_violation`;
- all economic/calibration/integrity gates pass -> eligible.

- [ ] **Step 3: Verify RED**

```bash
pytest tests/improvement/test_statistics.py tests/improvement/test_comparison.py -q
```

- [ ] **Step 4: Implement statistics**

Use `numpy.random.Generator(numpy.random.PCG64(seed))`. For each resample, sample paired market indices with replacement, compute challenger minus champion mean, then return deterministic 2.5/97.5 percentiles using NumPy's default linear percentile method. Report resample count and paired market count.

- [ ] **Step 5: Implement pure comparison policy**

`compare_policies` must not touch the database. It consumes already validated exact metrics/evidence flags and returns:

```python
@dataclass(frozen=True)
class ComparisonResult:
    economic_delta: float | None
    economic_interval: BootstrapInterval | None
    calibration_log_loss_delta: float | None
    calibration_brier_delta: float | None
    promotion_eligible: bool
    ineligibility_reasons: tuple[str, ...]
```

Sort/deduplicate reason codes for deterministic semantics.

- [ ] **Step 6: Run focused tests and Ruff**

```bash
pytest tests/improvement/test_statistics.py tests/improvement/test_comparison.py -q
ruff check src/bp_engine/improvement tests/improvement
```

- [ ] **Step 7: Commit Task 4**

```bash
git add src/bp_engine/improvement/statistics.py src/bp_engine/improvement/comparison.py tests/improvement/test_statistics.py tests/improvement/test_comparison.py
git commit -m "feat: compare phase13 challengers conservatively"
```

---

### Task 5: Improvement service and deliberate decision records

**Files:**
- Create: `src/bp_engine/improvement/service.py`
- Test: `tests/improvement/test_service_postgres.py`

**Interfaces:**
- Produces `register_experiment(connection, spec) -> StoreResult`.
- Produces `store_evaluation(connection, report) -> StoreResult` after evidence validation/reuse check.
- Produces `record_decision(connection, *, evaluation_id, decision, rationale, created_at) -> ImprovementPromotionDecision`.
- Produces `get_experiment_report(connection, experiment_id) -> dict[str, Any]`.

- [ ] **Step 1: Write service RED tests**

Test temporal ordering and deliberate decisions:

```python
with pytest.raises(ImprovementDecisionError, match="not promotion eligible"):
    record_decision(
        connection,
        evaluation_id=ineligible_id,
        decision=PromotionDecision.PROMOTE_CHALLENGER,
        rationale="force it",
        created_at=now,
    )

keep = record_decision(
    connection,
    evaluation_id=ineligible_id,
    decision=PromotionDecision.KEEP_CHAMPION,
    rationale="Independent confirmation is inconclusive.",
    created_at=now,
)
assert keep.decision is PromotionDecision.KEEP_CHAMPION
```

Also reject blank rationale and decisions timestamped before their evaluation.

- [ ] **Step 2: Verify RED**

```bash
pytest tests/improvement/test_service_postgres.py -q
```

- [ ] **Step 3: Implement orchestration**

`store_evaluation` must load its experiment, query prior fresh-confirmation identifiers from other experiments, validate the manifest, then store the immutable report.

`record_decision` loads the evaluation; `promote_challenger` requires `promotion_eligible=True`. `keep_champion` and `reject_challenger` are always permitted after a valid evaluation. No decision writes any live/paper policy pointer or modifies Phase 9 source records.

- [ ] **Step 4: Run focused tests and Ruff**

```bash
pytest tests/improvement/test_service_postgres.py -q
ruff check src/bp_engine/improvement tests/improvement
```

- [ ] **Step 5: Commit Task 5**

```bash
git add src/bp_engine/improvement/service.py tests/improvement/test_service_postgres.py
git commit -m "feat: orchestrate phase13 improvement decisions"
```

---

### Task 6: Network-free CLI and reproducible reporting

**Files:**
- Create: `src/bp_engine/improvement/cli.py`
- Create: `src/bp_engine/improvement/__main__.py`
- Create: `scripts/run_improvement.py`
- Test: `tests/improvement/test_cli.py`

**Interfaces:**
- Produces commands `register`, `report`, and `decide` immediately.
- Produces `evaluate` after Task 7 supplies the challenger adapter.
- Reads `DATABASE_URL` via existing `get_settings()` / `BP_ENV_FILE` behavior; never places network trading requests.

- [ ] **Step 1: Write CLI RED tests**

Use monkeypatched service functions and temporary JSON specs. Require structured single-object JSON output, non-zero exit on semantic conflict, and `--help` text that explicitly states research/paper only.

- [ ] **Step 2: Verify RED**

```bash
pytest tests/improvement/test_cli.py -q
```

- [ ] **Step 3: Implement CLI**

`register --spec FILE` loads a JSON experiment payload and constructs the frozen spec.

`report --experiment-id ID` emits experiment/evaluations/decisions.

`decide --evaluation-id ID --decision keep_champion|reject_challenger|promote_challenger --rationale TEXT` records the append-only decision.

Before Task 7, `evaluate` may fail with an explicit `challenger adapter not installed` error rather than silently doing nothing.

- [ ] **Step 4: Run CLI tests, pycompile, Ruff**

```bash
pytest tests/improvement/test_cli.py -q
python -m py_compile src/bp_engine/improvement/*.py scripts/run_improvement.py
ruff check src/bp_engine/improvement scripts/run_improvement.py tests/improvement/test_cli.py
```

- [ ] **Step 5: Commit Task 6**

```bash
git add src/bp_engine/improvement/cli.py src/bp_engine/improvement/__main__.py scripts/run_improvement.py tests/improvement/test_cli.py
git commit -m "feat: add phase13 research cli"
```

---

### Task 7: First 5m spread/abstention challenger

**Files:**
- Create: `src/bp_engine/improvement/challenger.py`
- Modify: `src/bp_engine/improvement/cli.py`
- Modify: `src/bp_engine/improvement/service.py`
- Test: `tests/improvement/test_challenger_postgres.py`

**Interfaces:**
- Produces `build_spread_guard_experiment(connection, *, created_at) -> ImprovementExperimentSpec`.
- Produces `evaluate_spread_guard_challenger(connection, *, experiment_id, created_at) -> ImprovementEvaluationReport`.
- Uses the accepted 5m Phase 9 champion and existing Phase 8/9 data as **development/legacy** evidence only.

- [ ] **Step 1: Write RED test for frozen champion/source contract**

Require the adapter to load exactly:

```text
phase9-300-c9f0e00eb7836af08008c66909f8f179
```

and reject any mismatching semantic hash chain.

- [ ] **Step 2: Write RED test for development max-spread selection**

The challenger definition must predeclare this V1 grid:

```python
MAX_SPREAD_GRID = (0.02, 0.04, 0.06, 0.08, 0.10, None)
```

For each existing Phase 8 fold, evaluate each max-spread candidate using the same frozen training/validation/test membership and the same fee/slippage/min-edge selection mechanics. Choose `max_spread` from validation only using this ordering:

1. highest validation `realized_pnl_after_assumed_costs`;
2. highest validation `cost_adjusted_expected_edge_sum`;
3. lower trade count (prefer abstention on an economic tie);
4. tighter max spread, with `None` last.

Then evaluate that frozen choice on the fold's ordinary test. Never use the ordinary test result to choose the spread guard.

- [ ] **Step 3: Write RED test proving legacy final holdout is not fresh confirmation**

The adapter may report the Phase 9 final holdout as `ordinary_oos`/legacy evidence for context, but the resulting Phase 13 evaluation must have `independent_confirmation_present=False` and therefore `promotion_eligible=False` until a later fresh holdout/prospective paper manifest is supplied.

- [ ] **Step 4: Verify RED**

```bash
pytest tests/improvement/test_challenger_postgres.py -q
```

- [ ] **Step 5: Implement challenger adapter with no Phase 9 mutation**

Reuse pure functions from `bp_engine.calibration.evaluation` and `bp_engine.calibration.edge`. If a small pure helper must be extracted from Phase 9 to evaluate a fixed max-spread candidate, preserve all existing Phase 9 behavior and add regression tests before changing it.

The challenger semantic hash must include the full spread grid, tie-break rules, accepted champion hash, source fold membership hashes, fee/slippage assumptions, and selected per-fold spread guards.

- [ ] **Step 6: Wire `evaluate` CLI**

`python -m bp_engine.improvement evaluate --experiment-id ...` supports the first `spread_guard_v1` challenger, stores the immutable evaluation, and emits JSON including `promotion_eligible` plus explicit reasons.

- [ ] **Step 7: Run focused and Phase 9 regression tests**

```bash
pytest tests/improvement/test_challenger_postgres.py -q
pytest tests/calibration -q
ruff check src/bp_engine/improvement src/bp_engine/calibration tests/improvement
```

Expected: PASS; accepted Phase 9 semantics unchanged.

- [ ] **Step 8: Commit Task 7**

```bash
git add src/bp_engine/improvement src/bp_engine/calibration tests/improvement/test_challenger_postgres.py
git commit -m "feat: evaluate 5m spread guard challenger"
```

---

### Task 8: Deployment validation and production acceptance tooling

**Files:**
- Create: `scripts/deploy/phase13_host_acceptance.sh`
- Create: `scripts/deploy/phase13_cloudshell_accept.sh`
- Create: `docs/PHASE-13-RESEARCH.md`
- Modify: `.github/workflows/ci.yml`
- Test: `tests/improvement/test_phase13_deployment_assets.py`

**Interfaces:**
- Produces host token `PHASE13_HOST_ACCEPTANCE=PASS`.
- Produces exact-head Cloud Shell helper with no secrets in argv.
- Keeps recorder/paper worker/dashboard/Postgres continuity.

- [ ] **Step 1: Write deployment-asset RED tests**

Require:

- both scripts exist and are Bash syntax-valid;
- host acceptance requires exact SHA and `BP_ENV_FILE` indirection;
- no `DATABASE_URL="$DATABASE_URL"` or `--database-url "$DATABASE_URL"` pattern;
- acceptance checks `LIVE_TRADING_ENABLED=false`, zero real-money limits, `execution_available=false`;
- acceptance verifies accepted Phase 9 champion source ids/hashes;
- acceptance registers an experiment twice and requires second run existing/no-op;
- acceptance evaluates the spread-guard challenger twice and requires semantic equality;
- acceptance records `keep_champion` when the evaluation is not promotion-eligible;
- acceptance refuses `promote_challenger` if ineligible;
- acceptance checks all five production services remain active and reconciliation is OK.

- [ ] **Step 2: Verify RED**

```bash
pytest tests/improvement/test_phase13_deployment_assets.py -q
```

- [ ] **Step 3: Implement host acceptance**

Acceptance may validly PASS with:

```text
DECISION=keep_champion
PROMOTION_ELIGIBLE=false
```

provided the experiment/evaluation/decision are real immutable production records and all research/integrity gates pass.

Do not fabricate fresh confirmation. If no new fresh holdout/prospective paper evidence is available, record `independent_confirmation_missing` honestly.

- [ ] **Step 4: Add CI syntax/compile validation**

Add:

```bash
bash -n scripts/deploy/phase13_host_acceptance.sh
bash -n scripts/deploy/phase13_cloudshell_accept.sh
python -m py_compile scripts/run_improvement.py
```

- [ ] **Step 5: Write research runbook**

Document experiment registration, evaluation, decision semantics, fresh evidence rules, and why a `keep_champion` result is acceptable. Repeat that live trading remains disabled.

- [ ] **Step 6: Run full local/CI-equivalent suite**

```bash
pytest -q
ruff check .
bash -n scripts/deploy/phase13_host_acceptance.sh
bash -n scripts/deploy/phase13_cloudshell_accept.sh
python -m bp_engine.health
```

Dashboard lane remains unchanged but must pass CI test/typecheck/build.

- [ ] **Step 7: Commit Task 8**

```bash
git add .github/workflows/ci.yml scripts/deploy/phase13_* docs/PHASE-13-RESEARCH.md tests/improvement/test_phase13_deployment_assets.py
git commit -m "ops: add phase13 host acceptance"
```

---

### Task 9: Exact-head CI, production experiment, and Phase 13 closeout

**Files:**
- Modify after production evidence: `PROJECT_STATE.json`
- Modify after production evidence: `docs/CHANGELOG.md`
- Modify after production evidence: `START-HERE.md`
- Modify after production evidence: `README.md`
- Modify after production evidence: `docs/BUILD-ORDER.md`
- Create after production evidence: `docs/evidence/phase-13-closeout-YYYYMMDD.json`

**Interfaces:**
- Consumes exact accepted branch SHA and production-host evidence.
- Produces Phase 13 closeout only after CI + production acceptance.

- [ ] **Step 1: Require exact-head branch CI GREEN**

Confirm backend complete suite, Ruff, deployment validation, health, and dashboard test/typecheck/build all pass on the exact candidate SHA.

- [ ] **Step 2: Run production host acceptance on the exact SHA**

The host run must show a real registered hypothesis, deterministic challenger evaluation, deliberate decision, source-hash integrity, service continuity, paper reconciliation OK, and no real execution availability.

- [ ] **Step 3: Inspect the actual research result without forcing a win**

If the challenger is ineligible or worse, record `keep_champion` or `reject_challenger`. If it is eligible under all frozen rules, record `promote_challenger` for research/paper only. Do not alter live-trading settings in either case.

- [ ] **Step 4: Record sanitized closeout evidence**

Evidence must include exact SHA, CI run, experiment/evaluation/decision ids and hashes, economic/calibration comparison, ineligibility reasons if any, service health, reconciliation, and safety flags. Do not include credentials.

- [ ] **Step 5: Advance project handoff to Phase 14 only if Phase 13 acceptance is satisfied**

Update source version to `0.13.0`, mark Phase 13 complete, set Phase 14 Live Readiness as next, and keep all real-money limits at zero.

- [ ] **Step 6: Require closeout CI GREEN, merge, then require main CI GREEN**

Do not declare Phase 13 complete before the exact closeout tree and resulting `main` merge both pass ordinary CI.

---

## Plan Self-Review

- Spec coverage: immutable specs/evaluations/decisions, evidence roles, fresh confirmation, anti-reuse, deterministic uncertainty, calibration guardrails, deliberate promotion, first 5m challenger, CLI, deployment safety, and production acceptance are each mapped to a task.
- Placeholder scan: no implementation step relies on `TBD`, `TODO`, or unspecified error handling.
- Type consistency: Task 1 dataclasses feed repositories; Task 3 evidence/source validation feeds Task 5 service; Task 4 comparison feeds Task 7 evaluation; Task 7 enables Task 8 host acceptance.
- Scope control: Phase 13 V1 intentionally does not add new deep models, wallet/signing, live execution, automated promotion, or automatic feature generation.
