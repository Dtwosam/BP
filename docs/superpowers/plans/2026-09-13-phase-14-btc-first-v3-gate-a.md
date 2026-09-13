# Phase 14 BTC-First V3 — Gate A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:test-driven-development` for every behavior change and `superpowers:verification-before-completion` before claiming Gate A complete. Steps use checkbox syntax for tracking.

**Goal:** Add a separate immutable `core-v3-btc-native` feature family that derives timestamp-coherent BTC-native predictors from Coinbase/Bybit `market_state_1s` at fixed 60/120/180/240-second offsets for 5-minute markets, plus outcome-blind coverage reporting. Do not train or activate a model.

**Architecture:** Reuse the existing compact-state table, `FeatureSourceReader` semantics, immutable `MarketFeature` repository, `time_geometry`, hashing, and feature dataset infrastructure. V3 adds a narrow as-of BTC observation reader and pure BTC calculators; Polymarket prices/books are deliberately excluded from the V3 forecast feature vector and remain a later execution/evaluation concern.

**Tech Stack:** Python 3.12, SQLAlchemy Core, PostgreSQL 16/SQLite fixtures, pytest, Ruff, existing BP feature hashing/repository infrastructure.

**Spec:** `docs/superpowers/specs/2026-09-13-phase-14-btc-first-v3-challenger-design.md`

## Global Constraints

- `V3_FEATURE_VERSION = "core-v3-btc-native"`; do not modify `FEATURE_VERSION = "core-v1"` or `V2_FEATURE_VERSION = "core-v2-last-trade"`.
- 5-minute horizon only: `horizon_seconds == 300`.
- Fixed feature offsets: `60, 120, 180, 240` seconds after market start.
- Forecast predictors come from Coinbase/Bybit BTC state only. No Polymarket price/book predictor key is allowed in the V3 forecast feature payload.
- Use only observations known at or before the requested as-of timestamp. Future rows must never influence historical features.
- Missing/stale state remains explicit. No fallback to Polymarket, training prior, another venue, or future data.
- The 84-trade diagnosis cohort is contaminated for future policy selection; Gate A reads no labels/outcomes/P&L.
- The consumed V2 final holdout is never reused.
- `adaptive-train` remains paused. No V2/V3 training, final-holdout access, activation, production deploy/restart/migration, paper activation, Phase 15, geographic bypass, live trading, or money-limit change.

---

### Task 1: Add the V3 static contract and deterministic BTC as-of reader

**Files:**
- Create: `src/bp_engine/features/v3_models.py`
- Create: `src/bp_engine/features/v3_sources.py`
- Create: `tests/features/test_v3_sources.py`
- Characterization only if needed: `tests/features/test_feature_sources.py`

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

`latest_btc_state` must reuse the same no-future filters as `FeatureSourceReader.latest_state`: both `bucket_at <= as_of` and `last_event_at <= as_of`, ordered deterministically by newest bucket/id. The selected price is `state["last_price"]`; missing/non-numeric/non-positive/non-finite price makes that anchor unavailable.

- [ ] **Step 1: Write the RED source tests**

Add tests proving:

```python
assert reader.latest_btc_state(..., as_of=T).row_id == expected_id
```

for Coinbase spot, Bybit spot, and Bybit linear independently. Cover:

1. a row with `bucket_at > T` is excluded;
2. a row with `last_event_at > T` is excluded;
3. a later row for another venue/stream/instrument cannot bleed across identity;
4. same-time ties select deterministically by existing row ordering;
5. missing/invalid `last_price` returns `None` for that anchor;
6. `fresh`/`age_seconds` are preserved from the existing 10-second state freshness semantics;
7. inserting a later row after `T` does not change the selected row for `T`.

- [ ] **Step 2: Run RED**

Run:

```text
pytest tests/features/test_v3_sources.py -v
```

Expected: fail because `v3_models`/`v3_sources` do not exist.

- [ ] **Step 3: Implement the minimal V3 source contract**

Prefer composition around `FeatureSourceReader.latest_state(...)` rather than duplicating SQL. Convert the returned `StateObservation` into `BTCStateObservation` only when `last_price` is valid. Do not add storage schema, migration, network, or Polymarket dependencies.

- [ ] **Step 4: Run GREEN + regression**

Run:

```text
pytest tests/features/test_v3_sources.py tests/features/test_feature_sources.py -v
ruff check src/bp_engine/features/v3_models.py src/bp_engine/features/v3_sources.py tests/features/test_v3_sources.py
```

- [ ] **Step 5: Commit**

```text
feat: add BTC-native V3 as-of source reader
```

---

### Task 2: Add pure BTC-native return and cross-venue calculators

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

`btc_return_group("coinbase", ...)` produces exactly:

```text
coinbase_return_from_market_start
coinbase_return_30s
coinbase_return_60s
coinbase_return_120s
```

and equivalent fields for `bybit_spot` / `bybit_linear`. Cross-venue output produces:

```text
coinbase_bybit_spot_return_spread
coinbase_bybit_spot_direction_agree
spot_linear_direction_agree
bybit_linear_vs_spot_basis
bybit_linear_funding_rate
bybit_linear_open_interest
```

Agreement fields are numeric `1.0` when both compared returns have the same strict sign, `0.0` when signs disagree, and `None` if either return is missing or zero. Basis uses current Bybit linear last / spot last - 1. Funding/open interest come only from current Bybit linear state and remain missing when absent.

- [ ] **Step 1: Write RED calculator tests**

Test positive/negative returns using exact prices, for example:

```python
assert group.values["coinbase_return_from_market_start"] == pytest.approx(0.01)
assert group.values["coinbase_return_30s"] == pytest.approx(-0.005)
```

Also assert:

- each trailing return uses the matching 30/60/120 anchor, not a nearest different horizon;
- missing anchors produce `None` plus explicit missing flags;
- venue agreement behavior follows the frozen sign rule above;
- basis/funding/open-interest are deterministic and explicit;
- every observation descriptor includes the exact row id, bucket/event times, source, stream, instrument, and price used;
- no feature/missing key begins with `pm_` or contains `polymarket`.

- [ ] **Step 2: Run RED**

```text
pytest tests/features/test_v3_calculators.py -v
```

Expected: fail because calculator module is absent.

- [ ] **Step 3: Implement minimal calculators**

Reuse `FeatureGroup` and existing numeric validation helpers where importing them does not pull Polymarket semantics into V3. Keep these functions pure: no database reads, no labels, no market execution logic.

- [ ] **Step 4: Run GREEN**

```text
pytest tests/features/test_v3_calculators.py -v
ruff check src/bp_engine/features/v3_calculators.py tests/features/test_v3_calculators.py
```

- [ ] **Step 5: Commit**

```text
feat: add BTC-native V3 feature calculators
```

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
) -> FeatureGenerationStats: ...
```

For feature time `T`, load each venue at market start, `T-120s`, `T-60s`, `T-30s`, and `T`. If an anchor is before market start, represent that trailing anchor as unavailable; do not query future/previous-market values merely to fill it.

Groups merged into V3 payload:

```text
time_geometry(target, T)
btc_return_group("coinbase", ...)
btc_return_group("bybit_spot", ...)
btc_return_group("bybit_linear", ...)
btc_cross_venue_group(...)
```

No `book_state`, `polymarket_prices`, V2 last-trade calculator, label, outcome, calibration, or edge group is included.

- [ ] **Step 1: Write RED planner/service tests**

Assert:

```python
assert [int((x - start).total_seconds()) for x in plan_v3_feature_times(target)] == [60, 120, 180, 240]
assert feature.feature_version == "core-v3-btc-native"
assert not any(key.startswith("pm_") for key in feature.features)
```

Also require:

- non-300-second targets fail closed;
- `market_end_at - market_start_at` must equal 300 seconds;
- all serialized source cutoffs are `<= feature_at`;
- input fingerprint includes every selected BTC anchor descriptor;
- changing a legitimate pre-`T` source observation changes fingerprint and affected features;
- exact rerun preserves immutable natural-key semantics through `MarketFeatureRepository`;
- existing `core-v1` / `core-v2-last-trade` constants and builders are not imported or modified by V3 service.

- [ ] **Step 2: Write future-data perturbation RED test**

Build feature at `T`, insert later Coinbase/Bybit state rows strictly after `T`, rebuild at `T`, and require byte-equivalent:

```text
features
missing_flags
source_cutoffs
input_fingerprint
feature_hash
```

- [ ] **Step 3: Run RED**

```text
pytest tests/features/test_v3_service.py tests/features/test_v3_future_data_leakage.py -v
```

- [ ] **Step 4: Implement minimal service**

Follow `v2_service.py` structure for target validation, group merging, deterministic fingerprinting, cutoff serialization, preserve-existing checks, and repository storage. Do not copy V2's Polymarket groups.

- [ ] **Step 5: Run GREEN + feature regressions**

```text
pytest tests/features/test_v3_service.py tests/features/test_v3_future_data_leakage.py -v
pytest tests/features/test_v2_service.py tests/features/test_v2_sources.py tests/features/test_dataset.py -v
ruff check src/bp_engine/features/v3_*.py tests/features/test_v3_*.py
```

Then require the complete `tests/features` suite green.

- [ ] **Step 6: Commit**

```text
feat: materialize immutable BTC-native V3 features
```

---

### Task 4: Add outcome-blind V3 coverage reporting

**Files:**
- Create: `src/bp_engine/features/v3_coverage.py`
- Create: `tests/features/test_v3_coverage.py`

**Contract:** Read only `market_features` rows where `feature_version == "core-v3-btc-native"`. No joins to labels, predictions, paper settlements, P&L, calibration, or holdout tables.

Return deterministic coverage data including:

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

- [ ] **Step 1: Write RED coverage tests**

Seed only V3 feature rows and assert counts/hash determinism. Add a source-inspection assertion that `v3_coverage.py` contains no references to:

```text
market_labels
official_outcome
live_prediction_evaluations
paper_settlements
realized_pnl
calibration
adaptive_train
```

and require `polymarket_predictor_key_count == 0` for a valid Gate A report.

- [ ] **Step 2: Run RED**

```text
pytest tests/features/test_v3_coverage.py -v
```

- [ ] **Step 3: Implement read-only report**

Use `canonical_hash`; do not persist policy configuration or read outcomes. Keep this a pure research-coverage helper in Gate A.

- [ ] **Step 4: Run GREEN**

```text
pytest tests/features/test_v3_coverage.py -v
ruff check src/bp_engine/features/v3_coverage.py tests/features/test_v3_coverage.py
```

- [ ] **Step 5: Commit**

```text
feat: add outcome-blind V3 coverage report
```

---

### Task 5: Gate A regression verification and durable handoff

**Files:**
- Modify after implementation evidence exists: `START-HERE.md`
- Modify after implementation evidence exists: `docs/CHANGELOG.md`
- Modify after implementation evidence exists: `docs/BUILD-ORDER.md`
- Modify after implementation evidence exists: `docs/DECISION-LOG.md`
- Modify after implementation evidence exists: `PROJECT_STATE.json`
- Modify after implementation evidence exists: `docs/MASTER-SOURCE-OF-TRUTH.md`
- Add/update source-of-truth regression tests under `tests/improvement/` if required by existing conventions.

- [ ] **Step 1: Verify V1/V2 immutability**

Confirm no diff to existing V1/V2 semantic constants or feature builders except imports/tests explicitly required for compatibility. Run targeted V1/V2 feature tests.

- [ ] **Step 2: Run the complete repository verification appropriate to the branch**

At minimum:

```text
pytest tests/features -v
pytest tests/improvement -v
ruff check .
```

Then require GitHub Actions CI green before claiming Gate A repository completion.

- [ ] **Step 3: Update canonical documentation with evidence, not projections**

Record only facts demonstrated by the implementation/CI:

- V3 Gate A repository implementation status;
- exact feature version and offsets;
- BTC-only forecast feature contract;
- V2 adaptive training remains paused;
- no production rollout/training occurred;
- exact commit/PR/CI evidence;
- next action is coverage collection / Gate A acceptance, not Gate B model training unless separately authorized.

Do not claim production coverage, profitability, calibration, Gate B acceptance, model activation, or paper readiness.

- [ ] **Step 4: Commit documentation checkpoint**

```text
docs: record BTC-first V3 Gate A repository state
```

## Completion Gate

Gate A repository work is complete only when all new V3 tests pass, existing feature/improvement regressions remain green, CI is green, the feature family contains no Polymarket predictor keys, future-data perturbation is proven safe, and the canonical handoff documents reflect the exact repository state. No training or production action is part of this plan.
