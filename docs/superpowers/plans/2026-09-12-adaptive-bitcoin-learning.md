# Adaptive Bitcoin Learning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a research-only adaptive learning cycle that waits for 50 newly resolved eligible markets, trains a deterministic challenger from the accumulated supervised Bitcoin/market dataset, records immutable cycle evidence, and reports the challenger without automatically activating or promoting it.

**Architecture:** Extend the existing Phase 13 `bp_engine.improvement` champion/challenger package. A read-only readiness layer counts newly resolved labeled markets that also have the required immutable feature version. Once ready, orchestration reuses `bp_engine.modeling.service.train_horizon` for deterministic prior/market/logistic/XGBoost training and stores a separate append-only adaptive-cycle checkpoint that binds the readiness evidence to the exact training run. No final holdout is accessed in this milestone; no paper/live model switch is performed.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x, PostgreSQL/SQLite test fixtures, scikit-learn, XGBoost, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-12-adaptive-bitcoin-learning-design.md`

## Global Constraints

- Keep `MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, `MAX_DAILY_LOSS_USD=0`, and `automatic_promotion=false`.
- The 12 September 2026 V2 Gate B final holdout is permanently consumed and must not be read, reused, or referenced as unseen evidence by this implementation.
- Count resolved **markets**, not executed trades. A `NO_TRADE` market remains a valid supervised learning example.
- A market is eligible for readiness only when an official label of the requested `label_version` exists and at least one immutable feature row of the requested `feature_version` and horizon exists for the same `condition_id`.
- Readiness is based on label completion time: `market_labels.generated_at > since_at` and `<= cutoff_at`.
- All time inputs must be timezone-aware UTC-normalizable values. Fail closed on invalid windows or ambiguous reset behavior.
- Adaptive training may create research artifacts and immutable database evidence only. It must not activate a paper model, alter live services, access a fresh final holdout, or create a promotion decision.
- Reuse existing immutable/hash helpers and model-training code. Do not duplicate a second modeling framework.
- Follow TDD: add a failing test, verify the intended failure, implement the minimum code, rerun the focused test, then run the broader relevant suite.

---

## Task 1 — Add deterministic adaptive readiness reporting

**Files:**
- Create: `src/bp_engine/improvement/adaptive.py`
- Create: `tests/improvement/test_adaptive_readiness.py`

- [ ] Add unit tests using SQLite `schema.metadata.create_all()` with `market_labels` and `market_features` fixtures.
- [ ] Cover exact horizon, feature-version, and label-version filtering.
- [ ] Prove the query counts distinct `condition_id` values even when a market has multiple feature timestamps.
- [ ] Prove labels at/before `since_at` are excluded and labels after `cutoff_at` are excluded.
- [ ] Prove a labeled market without the requested feature version is excluded.
- [ ] Prove `NO_TRADE`/paper-order existence is irrelevant: readiness derives only from eligible resolved supervised markets.
- [ ] Prove invalid inputs fail closed: naive datetimes, `cutoff_at <= since_at`, nonpositive horizon, blank versions, nonpositive trigger.
- [ ] Prove semantic output is deterministic when database row insertion order changes.

Implement in `src/bp_engine/improvement/adaptive.py`:

```python
DEFAULT_ADAPTIVE_TRIGGER_COUNT = 50
ADAPTIVE_READINESS_VERSION = "adaptive-readiness-v1"

@dataclass(frozen=True)
class AdaptiveReadinessReport:
    readiness_version: str
    horizon_seconds: int
    feature_version: str
    label_version: str
    since_at: datetime
    cutoff_at: datetime
    trigger_count: int
    eligible_resolved_market_count: int
    first_label_generated_at: datetime | None
    last_label_generated_at: datetime | None
    condition_ids_sha256: str
    ready: bool
    semantic_sha256: str


def build_adaptive_readiness_report(
    connection: Connection,
    *,
    horizon_seconds: int,
    feature_version: str,
    label_version: str,
    since_at: datetime,
    cutoff_at: datetime,
    trigger_count: int = DEFAULT_ADAPTIVE_TRIGGER_COUNT,
) -> AdaptiveReadinessReport:
    ...
```

Implementation rules:
- Query `market_labels` by requested horizon/label version and label `generated_at` window.
- Require `EXISTS` at least one `market_features` row with matching `condition_id`, requested horizon, and requested feature version.
- Return ordered condition IDs internally only long enough to hash them; do not place the raw list in the public report.
- Compute `condition_ids_sha256` with the existing deterministic `semantic_sha256` helper over the ordered IDs.
- Compute `semantic_sha256` over all report semantics except the digest itself.
- `ready` is exactly `eligible_resolved_market_count >= trigger_count`.

**Verification:**

```bash
pytest -q tests/improvement/test_adaptive_readiness.py
ruff check src/bp_engine/improvement/adaptive.py tests/improvement/test_adaptive_readiness.py
```

**Commit:** `Add adaptive resolved-market readiness`

---

## Task 2 — Add an append-only adaptive learning-cycle ledger

**Files:**
- Modify: `src/bp_engine/storage/improvement_schema.py`
- Modify: `src/bp_engine/storage/__init__.py`
- Modify: `src/bp_engine/improvement/adaptive.py`
- Create: `src/bp_engine/improvement/adaptive_repository.py`
- Create: `tests/improvement/test_adaptive_repository.py`

- [ ] Write repository tests first for append-only/idempotent storage and semantic-conflict rejection.
- [ ] Test lookup of the latest completed cycle by exact `(horizon_seconds, feature_version, label_version)`.
- [ ] Test that a cycle for a different feature or label version cannot advance another learning stream’s `since_at` boundary.
- [ ] Test that the ledger can be created under SQLite metadata for unit coverage and PostgreSQL-compatible SQLAlchemy types.

Add table `adaptive_learning_cycles` to `improvement_schema.py` with:
- integer primary key;
- unique `cycle_id`;
- `cycle_version`;
- horizon/feature/label identity;
- trigger count;
- `since_at`, `cutoff_at`, and eligible-market count;
- `readiness_semantic_sha256`;
- `training_start_at`;
- `training_run_id` and `training_semantic_sha256`;
- canonical `summary` JSON;
- cycle `semantic_sha256`;
- `created_at`.

Expose the table through `bp_engine.storage.schema` in `storage/__init__.py` following the existing improvement tables.

Add to `adaptive.py`:

```python
ADAPTIVE_CYCLE_VERSION = "adaptive-learning-cycle-v1"

@dataclass(frozen=True)
class AdaptiveLearningCycle:
    cycle_id: str
    cycle_version: str
    horizon_seconds: int
    feature_version: str
    label_version: str
    trigger_count: int
    since_at: datetime
    cutoff_at: datetime
    eligible_resolved_market_count: int
    readiness_semantic_sha256: str
    training_start_at: datetime
    training_run_id: str
    training_semantic_sha256: str
    summary: dict[str, Any]
    semantic_sha256: str
    created_at: datetime

    @classmethod
    def build(...):
        ...
```

Cycle semantics must include `automatic_promotion: false` and `final_holdout_accessed: false` in the summary produced by orchestration.

Repository interface:

```python
class AdaptiveLearningCycleRepository:
    def get(self, connection: Connection, cycle_id: str) -> dict[str, Any] | None: ...
    def latest_completed(
        self,
        connection: Connection,
        *,
        horizon_seconds: int,
        feature_version: str,
        label_version: str,
    ) -> dict[str, Any] | None: ...
    def store(
        self,
        connection: Connection,
        cycle: AdaptiveLearningCycle,
    ) -> ImprovementStoreResult: ...
```

Use the same immutable behavior as `ImprovementExperimentRepository`: exact semantic rerun is a no-op; same immutable ID with altered semantics raises an adaptive-cycle conflict.

**Verification:**

```bash
pytest -q tests/improvement/test_adaptive_repository.py tests/improvement/test_repository_postgres.py
ruff check src/bp_engine/improvement/adaptive.py src/bp_engine/improvement/adaptive_repository.py src/bp_engine/storage/improvement_schema.py src/bp_engine/storage/__init__.py tests/improvement/test_adaptive_repository.py
```

**Commit:** `Add immutable adaptive learning cycle ledger`

---

## Task 3 — Orchestrate a research-only training cycle using existing modeling code

**Files:**
- Modify: `src/bp_engine/improvement/adaptive.py`
- Create: `tests/improvement/test_adaptive_training.py`

- [ ] Write service-level tests with monkeypatched `modeling.service.train_horizon` so orchestration behavior is isolated from model runtime.
- [ ] Test first-cycle behavior: with no prior cycle, `bootstrap_since_at` is mandatory.
- [ ] Test subsequent-cycle behavior: `since_at` is exactly the latest completed cycle’s `cutoff_at`.
- [ ] Fail closed if a caller supplies `bootstrap_since_at` after a prior completed cycle exists; resetting the learning boundary must never happen silently.
- [ ] Test not-ready behavior: fewer than the trigger count raises `AdaptiveLearningNotReadyError` and does not call training or store a cycle.
- [ ] Test ready behavior: existing `train_horizon` is called once with exact horizon/feature/label, caller-specified `training_start_at`, and the frozen `cutoff_at`.
- [ ] Test that the cycle is stored only after a valid training report exists.
- [ ] Test summary binding to the exact training report hashes/artifacts and explicit safety flags.
- [ ] Test that orchestration has no final-holdout/backtest/promotion API call.

Add exceptions and orchestration:

```python
class AdaptiveLearningError(RuntimeError): ...
class AdaptiveLearningNotReadyError(AdaptiveLearningError): ...


def run_adaptive_training_cycle(
    connection: Connection,
    *,
    horizon_seconds: int,
    feature_version: str,
    label_version: str,
    bootstrap_since_at: datetime | None,
    cutoff_at: datetime,
    training_start_at: datetime,
    output_dir: Path,
    min_markets: int,
    trigger_count: int = DEFAULT_ADAPTIVE_TRIGGER_COUNT,
    created_at: datetime,
) -> tuple[AdaptiveReadinessReport, TrainingRunReport, AdaptiveLearningCycle]:
    ...
```

Behavior:
1. Read latest completed adaptive cycle for exact horizon/feature/label.
2. If none exists, require `bootstrap_since_at`; otherwise derive `since_at` from the latest cycle and reject any supplied bootstrap reset.
3. Build readiness at the requested frozen `cutoff_at`.
4. If not ready, raise before any model training.
5. Validate `training_start_at < cutoff_at`.
6. Call existing `bp_engine.modeling.service.train_horizon` with the accumulated supervised dataset window.
7. Build cycle summary from immutable training evidence, including:
   - dataset/split hashes;
   - validation champion;
   - best test result;
   - `boosted_promotion_eligible` as research evidence only;
   - model evaluations;
   - artifact manifests;
   - gross execution diagnostic;
   - `automatic_promotion=false`;
   - `final_holdout_accessed=false`;
   - `paper_model_activated=false`;
   - `live_trading_enabled=false`.
8. Store the cycle append-only and return it.

Do not call Phase 8 final-holdout evaluation in this milestone. The existing generic Phase 8 backtester currently evaluates its own market-price fold predictor and is not yet the correct arbitrary adaptive challenger evaluator.

**Verification:**

```bash
pytest -q tests/improvement/test_adaptive_training.py tests/improvement/test_adaptive_readiness.py tests/improvement/test_adaptive_repository.py
ruff check src/bp_engine/improvement/adaptive.py tests/improvement/test_adaptive_training.py
```

**Commit:** `Add research-only adaptive training orchestration`

---

## Task 4 — Expose read-only readiness and explicit adaptive training CLI commands

**Files:**
- Modify: `src/bp_engine/improvement/cli.py`
- Modify: `tests/improvement/test_cli.py`

Add commands:

```text
adaptive-readiness
adaptive-train
```

`adaptive-readiness` arguments:
- `--horizon-seconds` required;
- `--feature-version` required;
- `--label-version` required;
- `--bootstrap-since-at` optional, but required when no previous cycle exists;
- `--cutoff-at` required for reproducibility;
- `--trigger-count` default `50`.

`adaptive-train` adds:
- `--training-start-at` required;
- `--output-dir` required;
- `--min-markets` required.

- [ ] Extend CLI tests first.
- [ ] Verify `adaptive-readiness` emits exactly one JSON object and performs no writes.
- [ ] Verify the command derives `since_at` from the previous cycle when present.
- [ ] Verify no prior cycle + missing bootstrap returns structured nonzero JSON.
- [ ] Verify `adaptive-train` returns structured `not ready` failure without invoking training.
- [ ] Verify a successful mocked training cycle returns readiness, training identity, and cycle identity.
- [ ] Preserve the help statement that the CLI is research/paper-only and never submits live requests.

Use the existing `_parse_datetime`, `_json_value`, `_emit`, and database lifecycle patterns. Do not introduce a second CLI entry point.

**Verification:**

```bash
pytest -q tests/improvement/test_cli.py tests/improvement/test_adaptive_readiness.py tests/improvement/test_adaptive_training.py
ruff check src/bp_engine/improvement/cli.py tests/improvement/test_cli.py
```

**Commit:** `Expose adaptive learning research commands`

---

## Task 5 — Make the adaptive learning contract canonical in project state

**Files:**
- Modify: `docs/MASTER-SOURCE-OF-TRUTH.md`
- Modify: `PROJECT_STATE.json`
- Modify: `docs/BUILD-ORDER.md`
- Modify: `docs/DECISION-LOG.md`
- Modify: `docs/CHANGELOG.md`
- Create: `tests/improvement/test_adaptive_source_truth.py`
- Modify: `docs/superpowers/specs/2026-09-12-adaptive-bitcoin-learning-design.md` to mark the approved/implemented milestone accurately.

- [ ] Determine the next Decision Log number from the actual file tail before writing; do not guess it.
- [ ] Determine the next changelog version from the current branch/main before writing.
- [ ] Add a concise Master clarification under controlled retraining/training that resolved market examples—not executed trades alone—drive supervised learning.
- [ ] Record the initial threshold of 50 newly resolved eligible markets as an adaptive-cycle trigger, explicitly noting that it is a research-policy threshold rather than a proof-of-profitability sample-size rule.
- [ ] Record that adaptive cycles train/report challengers only; `automatic_promotion=false` remains authoritative.
- [ ] Record the 12 September Gate B result as complete but not accepted and its final holdout as permanently consumed.
- [ ] Update BUILD-ORDER’s immediate next action away from the already-completed Gate B run and toward adaptive research readiness/training evidence.
- [ ] Update machine-readable project state without changing the top-level phase/status: `PHASE_14_ENGINEERING_COMPLETE_LIVE_GATE_BLOCKED`, RESEARCH, live disabled, money limits zero.
- [ ] Add source-of-truth tests that assert these safety and learning-objective invariants.

**Verification:**

```bash
pytest -q tests/improvement/test_adaptive_source_truth.py tests/improvement
ruff check src tests
python -m json.tool PROJECT_STATE.json >/dev/null
```

**Commit:** `Record adaptive learning milestone in source of truth`

---

## Task 6 — Full verification and PR closeout

- [ ] Run the focused improvement suite.
- [ ] Run the repository’s full pytest suite.
- [ ] Run Ruff.
- [ ] Run any existing deployment/research safety validation commands required by CI if available in repo workflow.
- [ ] Confirm diff contains no production deployment helper invocation, service mutation, wallet/secret change, geographic bypass, risk-limit increase, automatic promotion, final-holdout access, or live-order activation.
- [ ] Confirm `main` has not moved unexpectedly; if it has, rebase/merge only through the normal repository workflow and rerun verification.
- [ ] Update PR #188 body to include implementation evidence and exact test counts.
- [ ] Mark PR ready for review only after verification-before-completion passes.
- [ ] Do not deploy to production or activate a model from this PR.

**Expected end state:** BP can deterministically answer “are there at least 50 new resolved eligible learning examples?”, can launch and record a research challenger training cycle using the existing model ladder when ready, and can report the resulting immutable evidence. It still cannot automatically promote, activate V2/V3 paper execution, access a fresh final holdout, enable nonzero money, or trade live.