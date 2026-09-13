# Phase 14 BTC-First V3 — Gate A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:test-driven-development` for every behavior change and `superpowers:verification-before-completion` before claiming Gate A complete. Steps use checkbox syntax for tracking.

**Goal:** Add a separate immutable `core-v3-btc-native` feature family that derives timestamp-coherent BTC-native predictors from Coinbase/Bybit `market_state_1s` at fixed 60/120/180/240-second offsets for 5-minute markets, plus outcome-blind coverage reporting. Do not train or activate a model.

**Architecture:** Reuse the existing compact-state table, `FeatureSourceReader` semantics, immutable `MarketFeature` repository, `time_geometry`, hashing, and dataset infrastructure. V3 adds a narrow as-of BTC observation reader and pure BTC calculators; Polymarket prices/books are deliberately excluded from the first V3 forecast feature vector and remain a later execution/evaluation concern.

**Tech Stack:** Python 3.12, SQLAlchemy Core, PostgreSQL 16/SQLite fixtures, pytest, Ruff, existing BP feature hashing/repository infrastructure.

**Spec:** `docs/superpowers/specs/2026-09-13-phase-14-btc-first-v3-challenger-design.md`

## Global Constraints

- `V3_FEATURE_VERSION = "core-v3-btc-native"`; do not modify `FEATURE_VERSION = "core-v1"` or `V2_FEATURE_VERSION = "core-v2-last-trade"`.
- 5-minute horizon only: `horizon_seconds == 300`.
- Fixed feature offsets: `60, 120, 180, 240` seconds after market start.
- Forecast predictors come from Coinbase/Bybit BTC state only. No Polymarket price/book predictor key is allowed in the V3 forecast payload.
- Use only observations known at or before the requested as-of timestamp. Future rows must never influence historical features.
- Missing/stale state remains explicit. No fallback to Polymarket, training prior, another venue, or future data.
- The 84-trade diagnosis cohort is contaminated for future policy selection; Gate A reads no labels/outcomes/P&L.
- The consumed V2 final holdout is never reused.
- `adaptive-train` remains paused. No V2/V3 training, final-holdout access, activation, production deploy/restart/migration, paper activation, Phase 15, geographic bypass, live trading, or money-limit change.

---

### Task 1: V3 static contract and deterministic BTC as-of reader

**Files:**
- Create: `src/bp_engine/features/v3_models.py`
- Create: `src/bp_engine/features/v3_sources.py`
- Create: `tests/features/test_v3_sources.py`
- Reuse/characterize: `src/bp_engine/features/sources.py`, `tests/features/test_feature_sources.py`

**Interfaces:**

```python
V3_FEATURE_VERSION = "core-v3-btc-native"
V3_OFFSETS_SECONDS = (60, 120, 180, 240)

@dataclass(frozen=True)
class V3FeatureTarget:
    condition_id: str
    slug: str
    horizon_seconds: int
    market_start_at: datetime
    market_end_at: datetime

@dataclass(frozen=True)
class BTCStateObservation:
    row_id: int
    bucket_at: datetime
    last_event_at: datetime
    source: str
    stream: str
    instrument: str
    price: Decimal
    state: dict[str, Any]
    fresh: bool
    age_seconds: float

class V3FeatureSourceReader:
    def latest_btc_state(
        self,
        connection: Connection,
        *,
        source: str,
        stream: str,
        instrument: str,
        as_of: datetime,
    ) -> BTCStateObservation | None: ...
```

- [ ] **Step 1: Write RED source tests.** Prove `bucket_at <= T` and `last_event_at <= T`; Coinbase spot, Bybit spot, and Bybit linear identities are isolated; same-time selection is deterministic; invalid/missing `last_price` makes the anchor unavailable; existing 10-second freshness/age semantics are preserved; a later row inserted after `T` cannot change the selected row at `T`.
- [ ] **Step 2: Run RED.** `pytest tests/features/test_v3_sources.py -v` must fail because the V3 modules do not yet exist.
- [ ] **Step 3: Implement minimally.** Prefer composition around `FeatureSourceReader.latest_state(...)`; convert only valid positive finite `state["last_price"]` values. Add no storage migration, network call, or Polymarket dependency.
- [ ] **Step 4: Run GREEN + regression.**

```text
pytest tests/features/test_v3_sources.py tests/features/test_feature_sources.py -v
ruff check src/bp_engine/features/v3_models.py src/bp_engine/features/v3_sources.py tests/features/test_v3_sources.py
```

- [ ] **Step 5: Commit.** `feat: add BTC-native V3 as-of source reader`

---

### Task 2: Pure BTC-native return and cross-venue calculators

**Files:**
- Create: `src/bp_engine/features/v3_calculators.py`
- Create: `tests/features/test_v3_calculators.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class BTCAnchorSet:
    market_start: BTCStateObservation | None
    trailing_120s: BTCStateObservation | None
    trailing_60s: BTCStateObservation | None
    trailing_30s: BTCStateObservation | None
    current: BTCStateObservation | None


def btc_return_group(prefix: str, anchors: BTCAnchorSet) -> FeatureGroup: ...

def btc_cross_venue_group(
    coinbase: BTCAnchorSet,
    bybit_spot: BTCAnchorSet,
    bybit_linear: BTCAnchorSet,
) -> FeatureGroup: ...
```

`btc_return_group("coinbase", ...)` produces exactly `coinbase_return_from_market_start`, `coinbase_return_30s`, `coinbase_return_60s`, and `coinbase_return_120s`; equivalent fields exist for Bybit spot and linear.

Cross-venue fields are exactly:

```text
coinbase_bybit_spot_return_spread
coinbase_bybit_spot_direction_agree
spot_linear_direction_agree
bybit_linear_vs_spot_basis
bybit_linear_funding_rate
bybit_linear_open_interest
```

Agreement is `1.0` for equal strict signs, `0.0` for opposite strict signs, and `None` if either compared return is missing or zero. Basis is current Bybit linear last / spot last - 1. Funding/open-interest come only from current Bybit linear state.

- [ ] **Step 1: Write RED calculator tests.** Assert positive/negative returns, exact 30/60/120 anchors, explicit missing flags, deterministic agreement, basis/funding/open-interest behavior, complete source descriptors, and zero `pm_`/`polymarket` keys.
- [ ] **Step 2: Run RED.** `pytest tests/features/test_v3_calculators.py -v` must fail for the missing module.
- [ ] **Step 3: Implement minimally.** Reuse `FeatureGroup`; keep functions pure with no DB, labels, or execution logic.
- [ ] **Step 4: Run GREEN.**

```text
pytest tests/features/test_v3_calculators.py -v
ruff check src/bp_engine/features/v3_calculators.py tests/features/test_v3_calculators.py
```

- [ ] **Step 5: Commit.** `feat: add BTC-native V3 feature calculators`

---

### Task 3: Materialize immutable `core-v3-btc-native` feature rows

**Files:**
- Create: `src/bp_engine/features/v3_service.py`
- Create: `tests/features/test_v3_service.py`
- Create: `tests/features/test_v3_future_data_leakage.py`
- Reuse: `src/bp_engine/features/repository.py`
- Reuse: `src/bp_engine/features/calculators.py::time_geometry`

**Interfaces:**

```python
@dataclass(frozen=True)
class V3FeatureGenerationStats:
    targets_considered: int
    planned_rows: int
    inserted: int
    existing: int
    missing_group_counts: dict[str, int]


def plan_v3_feature_times(target: V3FeatureTarget) -> tuple[datetime, ...]: ...

def build_v3_feature(
    connection: Connection,
    target: V3FeatureTarget,
    feature_at: datetime,
    *,
    generated_at: datetime,
    repository: MarketFeatureRepository | None = None,
) -> MarketFeature: ...

def generate_v3_features(
    connection: Connection,
    targets: Iterable[V3FeatureTarget],
    *,
    generated_at: datetime,
    preserve_existing: bool = False,
) -> V3FeatureGenerationStats: ...
```

For feature time `T`, load each venue at market start, `T-120s`, `T-60s`, `T-30s`, and `T`. If a trailing anchor precedes market start, leave it unavailable; do not cross into a previous market window merely to fill it.

Merge only:

```text
time_geometry(target, T)
btc_return_group("coinbase", ...)
btc_return_group("bybit_spot", ...)
btc_return_group("bybit_linear", ...)
btc_cross_venue_group(...)
```

No `book_state`, `polymarket_prices`, V2 last-trade calculator, label, outcome, calibration, or edge group belongs in V3 Gate A.

- [ ] **Step 1: Write RED planner/service tests.** Require offsets `[60,120,180,240]`, exact feature version, zero `pm_` feature keys, rejection of non-300-second or non-exact-five-minute targets, cutoffs `<= feature_at`, complete anchor fingerprinting, immutable reruns, and no dependency on V1/V2 builders.
- [ ] **Step 2: Write RED future-data perturbation test.** Build at `T`, insert later Coinbase/Bybit rows after `T`, rebuild, and require identical `features`, `missing_flags`, `source_cutoffs`, `input_fingerprint`, and `feature_hash`.
- [ ] **Step 3: Run RED.**

```text
pytest tests/features/test_v3_service.py tests/features/test_v3_future_data_leakage.py -v
```

- [ ] **Step 4: Implement minimally.** Follow `v2_service.py` patterns for target validation, group merging, fingerprinting, cutoff serialization, preserve-existing checks, and repository storage, while keeping all Polymarket groups out.
- [ ] **Step 5: Run GREEN + regressions.**

```text
pytest tests/features/test_v3_service.py tests/features/test_v3_future_data_leakage.py -v
pytest tests/features/test_v2_service.py tests/features/test_v2_sources.py tests/modeling/test_dataset.py -v
ruff check src/bp_engine/features/v3_*.py tests/features/test_v3_*.py
```

Then require the full `tests/features` suite green.

- [ ] **Step 6: Commit.** `feat: materialize immutable BTC-native V3 features`

---

### Task 4: Outcome-blind V3 coverage reporting

**Files:**
- Create: `src/bp_engine/features/v3_coverage.py`
- Create: `tests/features/test_v3_coverage.py`

**Contract:** Read only `market_features` rows where `feature_version == "core-v3-btc-native"`. Do not join labels, predictions, paper settlements, P&L, calibration, or holdout data.

Report deterministic:

```text
feature_version
row_count
market_count
offsets
by_offset counts
per-source current-state available/missing counts
per-return-field available/missing counts
per-source age summary when derivable from cutoffs
future_cutoff_violation_count
polymarket_predictor_key_count
coverage_input_sha256
policy_selected = false
training_run = false
automatic_promotion = false
```

- [ ] **Step 1: Write RED coverage tests.** Seed only V3 feature rows, assert counts/hash determinism, require `polymarket_predictor_key_count == 0`, and source-inspect the module for absence of `market_labels`, `official_outcome`, `live_prediction_evaluations`, `paper_settlements`, `realized_pnl`, calibration, and `adaptive_train` references.
- [ ] **Step 2: Run RED.** `pytest tests/features/test_v3_coverage.py -v`.
- [ ] **Step 3: Implement minimally.** Use `canonical_hash`; persist no policy and read no outcomes.
- [ ] **Step 4: Run GREEN.**

```text
pytest tests/features/test_v3_coverage.py -v
ruff check src/bp_engine/features/v3_coverage.py tests/features/test_v3_coverage.py
```

- [ ] **Step 5: Commit.** `feat: add outcome-blind V3 coverage report`

---

### Task 5: Gate A regression verification and durable handoff

**Files after implementation evidence exists:**
- Modify: `START-HERE.md`
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/BUILD-ORDER.md`
- Modify: `docs/DECISION-LOG.md`
- Modify: `PROJECT_STATE.json`
- Modify: `docs/MASTER-SOURCE-OF-TRUTH.md`
- Add/update source-of-truth regression tests under `tests/improvement/` as required by repository conventions.

- [ ] **Step 1: Verify V1/V2 immutability.** Confirm no semantic change to existing V1/V2 feature constants/builders and run their targeted tests.
- [ ] **Step 2: Run repository verification.**

```text
pytest tests/features -v
pytest tests/improvement -v
ruff check .
```

Require GitHub Actions CI green before claiming Gate A repository completion.

- [ ] **Step 3: Update canonical documentation with evidence, not projections.** Record the exact V3 Gate A repository status, version/offsets, BTC-only forecast contract, V2 adaptive pause, exact PR/commit/CI evidence, and that no production rollout/training occurred. The next action is coverage collection / Gate A acceptance, not Gate B model training unless separately authorized.
- [ ] **Step 4: Commit.** `docs: record BTC-first V3 Gate A repository state`

## Completion Gate

Gate A repository work is complete only when all new V3 tests pass, existing feature/improvement regressions remain green, CI is green, the feature family contains no Polymarket predictor keys, future-data perturbation is proven safe, and the canonical handoff documents reflect the exact repository state. No training or production action is part of this plan.
