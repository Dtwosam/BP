# Phase 14 Market-Price V2 — Gate A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:test-driven-development` for every behavior change and `superpowers:verification-before-completion` before claiming Gate A complete.

**Goal:** Add forward-only, timestamp-coherent Polymarket last-trade provenance and immutable 5m `core-v2-last-trade` research features, plus an outcome-blind coverage report. Do not create or select a V2 trading policy.

**Architecture:** Preserve dedicated Polymarket `last_trade_price` provenance inside the existing JSON compact-state payload, read it through a new additive as-of source reader keyed by exact Up/Down token IDs, materialize a separate feature version into the existing immutable `market_features` table, and report only unlabeled availability/timing coverage. V1 feature/model/prediction semantics remain unchanged.

**Tech Stack:** Python 3.12, SQLAlchemy Core, PostgreSQL 16/SQLite test fixtures, pytest, Ruff, existing recorder/feature hashing/repository infrastructure.

**Spec:** `docs/superpowers/specs/2026-09-02-phase-14-timestamp-coherent-market-price-v2-design.md`

## Hard boundaries

- Work only on `phase14-market-price-v2-design` or a descendant isolated branch; never commit implementation directly to `main`.
- `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0` remain unchanged.
- No live-order, wallet, signing, allowance, activation, promotion, or Phase 15 work.
- Existing selected-book freshness remains exactly 10 seconds.
- Do not change V1 `core-v1`, Phase 7/8/9 run semantics, V1 calibration, V1 `min_edge`, `live-prediction-v1`, or existing V1 evidence.
- Do not select a V2 timing, calibration, edge threshold, or last-trade freshness threshold in Gate A.
- Do not use the 27 failed V1 trades, their outcomes, P&L, or their 33–51 second ages to choose V2 parameters.
- Gate A coverage code must not read official outcomes, labels, prediction evaluations, paper settlements, P&L, or calibration metrics.
- No production rollout is included in this plan. Production deployment/activation of recorder or V2 collection remains a later explicit authorization step.
- No database migration is required: `market_state_1s.state` and `market_features` feature payloads are already JSON, and immutable feature identity already includes `feature_version`.

---

### Task 1: Preserve dedicated Polymarket last-trade provenance in compact state

**Files:**
- Modify: `tests/recorder/test_state_reducer.py`
- Modify: `src/bp_engine/recorder/state.py`
- Optional characterization only if needed: `tests/collectors/test_polymarket_ws.py`

**Contract:** A Polymarket `last_trade_price` event for one exact token updates:

```text
last_trade_price
last_trade_size
last_trade_side
last_trade_source_at
last_trade_received_at
last_trade_event_dedupe_key
```

The existing generic `last_price`, `last_trade_size`, and `last_trade_side` remain for backward compatibility. A later `book` or `price_change` event may advance generic `last_event_at` and book/change fields but must not alter the dedicated last-trade timestamps, price, side/size, or dedupe key.

If `RawEvent.source_timestamp` is absent, preserve the receipt time/dedupe/price as recorder evidence but store `last_trade_source_at = null`; later V2 readers must reject that observation as V2 probability evidence.

- [ ] **Step 1: Write RED reducer tests**

Add a test using the captured `last_trade_price.json` fixture and assert:

```python
snapshot.state["last_trade_price"] == event.payload["price"]
snapshot.state["last_price"] == event.payload["price"]
snapshot.state["last_trade_size"] == event.payload["size"]
snapshot.state["last_trade_side"] == event.payload["side"]
snapshot.state["last_trade_source_at"] == event.source_timestamp.isoformat().replace("+00:00", "Z")
snapshot.state["last_trade_received_at"] == event.received_at.isoformat().replace("+00:00", "Z")
snapshot.state["last_trade_event_dedupe_key"] == event.dedupe_key
```

Add a second test: observe the same token's last trade, then a later `price_change`; assert generic `last_event_at` advances but every dedicated last-trade field remains byte-equivalent.

Add a third test with a manually built `last_trade_price` `RawEvent` whose `source_timestamp=None`; assert `last_trade_source_at is None` while receipt/dedupe/price remain explicit.

- [ ] **Step 2: Verify RED in CI**

Push only the new tests. GitHub Actions `CI` runs on push. Expected result: focused/new reducer assertions fail because the dedicated keys are absent; unrelated tests/lint should remain green. Preserve the RED commit SHA and workflow result in the PR description later.

- [ ] **Step 3: Implement the minimal reducer change**

In the existing `last_trade_price` branch only, add the dedicated fields using normalized UTC ISO-8601 `Z` strings for timestamps. Do not change `book`, `price_change`, other venues, state keys, storage schema, or generic `last_event_at` semantics.

- [ ] **Step 4: Verify GREEN**

Run/observe CI for the implementation commit. At minimum these tests must pass:

```text
tests/recorder/test_state_reducer.py
tests/collectors/test_polymarket_ws.py
```

The complete CI Python lane must also remain green.

- [ ] **Step 5: Commit checkpoint**

Commit message: `feat: preserve Polymarket last-trade provenance`

---

### Task 2: Add an additive V2 as-of source reader and static 5m target contract

**Files:**
- Create: `src/bp_engine/features/v2_models.py`
- Create: `src/bp_engine/features/v2_sources.py`
- Create: `tests/features/test_v2_sources.py`

**Interfaces:**

```python
V2_FEATURE_VERSION = "core-v2-last-trade"

@dataclass(frozen=True)
class V2FeatureTarget:
    condition_id: str
    slug: str
    horizon_seconds: int
    market_start_at: datetime
    market_end_at: datetime
    up_token_id: str
    down_token_id: str

@dataclass(frozen=True)
class LastTradeObservation:
    compact_state_row_id: int
    compact_state_bucket_at: datetime
    compact_state_last_event_at: datetime
    asset_id: str
    price: Decimal
    size: Decimal | None
    side: str | None
    source_at: datetime
    received_at: datetime
    event_dedupe_key: str

class V2FeatureSourceReader(FeatureSourceReader):
    def latest_polymarket_last_trade(..., asset_id: str, feature_at: datetime) -> LastTradeObservation | None: ...
```

The reader may reuse the existing `latest_state` query for executable book state, but last-trade age is derived only from dedicated last-trade fields.

- [ ] **Step 1: Write RED source-reader tests**

Seed `market_state_1s` rows containing dedicated last-trade provenance and assert exact token isolation for Up versus Down.

Cover all boundaries:

1. complete dedicated trade with `received_at <= T` and `source_at <= T` is returned;
2. an older dedicated trade remains returned even when generic `last_event_at` is newer, proving age is not derived from generic feed activity;
3. state missing `last_trade_source_at`, `last_trade_received_at`, price, or dedupe key returns no V2 last-trade observation;
4. malformed/non-timezone timestamp strings fail closed;
5. dedicated `received_at > T` or `source_at > T` fails closed as future evidence;
6. a later compact-state row strictly after `T` cannot influence the as-of selection;
7. price must be finite and in `[0, 1]`; invalid values fail closed.

- [ ] **Step 2: Verify RED**

Push tests without modules. Expected: import/module failures only for the new V2 source contract; existing feature tests remain green.

- [ ] **Step 3: Implement models and reader minimally**

Use the existing `market_state_1s` table and exact `asset_id`; do not read `/prices-history` and do not infer a token from a price-history row. Normalize persisted SQLite naive datetimes the same defensive way existing source readers do, but require serialized dedicated trade timestamps themselves to contain timezone information.

Do not impose an economic freshness cutoff in this reader. It selects valid as-of evidence and preserves its actual age for later descriptive features.

- [ ] **Step 4: Verify GREEN**

Run/observe:

```text
pytest tests/features/test_v2_sources.py tests/features/test_feature_sources.py -v
ruff check src/bp_engine/features/v2_models.py src/bp_engine/features/v2_sources.py tests/features/test_v2_sources.py
```

Then require the full CI Python lane to pass.

- [ ] **Step 5: Commit checkpoint**

Commit message: `feat: add timestamp-coherent last-trade source reader`

---

### Task 3: Materialize forward-only immutable 5m `core-v2-last-trade` features

**Files:**
- Create: `src/bp_engine/features/v2_calculators.py`
- Create: `src/bp_engine/features/v2_service.py`
- Create: `src/bp_engine/features/v2_cli.py`
- Create: `scripts/generate_v2_features.py`
- Create: `tests/features/test_v2_calculators.py`
- Create: `tests/features/test_v2_service.py`
- Create: `tests/features/test_v2_future_data_leakage.py`
- Create: `tests/features/test_v2_cli.py`

**Feature scope:** Gate A deliberately materializes only the fields needed for timestamp-coherent market-price research and execution-availability diagnostics:

```text
seconds_elapsed
seconds_remaining
fraction_elapsed
horizon_seconds
pm_up_last_trade_price
pm_up_last_trade_source_age_s
pm_up_last_trade_availability_age_s
pm_down_last_trade_price
pm_down_last_trade_source_age_s
pm_down_last_trade_availability_age_s
pm_up_best_bid / pm_up_best_ask / pm_up_mid / pm_up_spread / depth / imbalance
pm_down_best_bid / pm_down_best_ask / pm_down_mid / pm_down_spread / depth / imbalance
```

Book values reuse the established V1 `book_state` calculator and therefore retain the frozen 10-second state freshness behavior. Gate A does not copy `/prices-history`, BTC, calibration, edge, or outcome fields into V2 merely to make the payload larger.

- [ ] **Step 1: Write calculator RED tests**

For a valid last-trade observation at `T-4s` source time and `T-3s` receipt time, assert price plus source age `4.0` and availability age `3.0`. Missing observation produces NULL values plus an explicit `pm_<side>_last_trade_missing=true` flag. Observation cutoffs/descriptors must include asset ID, source/receipt timestamps, dedupe key, price, size/side, compact row id/bucket/last-event metadata.

- [ ] **Step 2: Write service/planner RED tests**

Assert a 300-second target plans exactly offsets `[60, 120, 180, 240]`; any non-300-second target is rejected by Gate A rather than silently expanding 15m scope.

Build one feature at `T` and assert:

- `feature_version == "core-v2-last-trade"`;
- no `pm_up_price`, `pm_down_price`, `/prices-history`, label, outcome, calibration, or edge field exists;
- every persisted source cutoff is `<= T`;
- dedicated last-trade age fields are descriptive even when age exceeds 10 seconds;
- book missing/stale behavior remains the existing 10-second contract;
- input fingerprint changes when last-trade provenance changes even if numeric price is unchanged;
- feature hash changes only when feature/missing semantics change, consistent with the existing hash contract;
- exact rerun is existing/no-op through `MarketFeatureRepository`.

- [ ] **Step 3: Write future-data perturbation RED test**

Generate V2 at `T`, then insert a later compact-state row containing a new last trade/book update strictly after `T`, regenerate at `T`, and require equality of:

```text
features
missing_flags
source_cutoffs
input_fingerprint
feature_hash
```

- [ ] **Step 4: Write outcome-isolation/target-loader RED tests**

The V2 CLI target loader must query `polymarket_markets`, not `market_labels`, and select only static market identity/window/token columns. It filters exactly `horizon_seconds == 300` and the requested market-start half-open window.

Source-inspection test must fail if `v2_cli.py` or `v2_service.py` imports/references:

```text
market_labels
official_outcome
live_prediction_evaluations
paper_settlements
polymarket_price_history
```

- [ ] **Step 5: Verify RED**

Push the tests first. Expected failures are missing V2 modules/interfaces. Existing `core-v1` tests must remain green.

- [ ] **Step 6: Implement minimal V2 calculators/service/CLI**

Reuse existing immutable `MarketFeature`, `MarketFeatureRepository`, `canonical_hash`, `time_geometry`, and `book_state` infrastructure. Do not modify `FEATURE_VERSION = "core-v1"` or V1 service behavior.

`generate_v2_features.py` is an offline/research command only. It must not import HTTP/WebSocket clients, live prediction, paper execution, or live-readiness execution paths.

- [ ] **Step 7: Verify GREEN**

Require targeted V2 tests plus all existing `tests/features` to pass, then the full CI Python lane.

- [ ] **Step 8: Commit checkpoint**

Commit message: `feat: add forward-only market-price V2 features`

---

### Task 4: Add outcome-blind V2 coverage reporting without selecting policy

**Files:**
- Create: `src/bp_engine/features/v2_coverage.py`
- Create: `src/bp_engine/features/v2_coverage_cli.py`
- Create: `scripts/report_v2_feature_coverage.py`
- Create: `tests/features/test_v2_coverage.py`
- Create: `tests/features/test_v2_coverage_cli.py`

**Contract:** The report reads only `market_features` rows with `feature_version="core-v2-last-trade"` plus permitted static time/market information already stored on those rows. It does not join labels, predictions, evaluations, paper execution, or outcomes.

The report returns deterministic JSON with at least:

```text
feature_version
row_count
market_count
offsets
by_offset row/market counts
Up/Down last-trade available/missing counts
Up/Down source-age summary
Up/Down availability-age summary
Up/Down book available/missing/stale counts
Up/Down book-age summary derived from source cutoffs
invalid/nonfinite value count
future-cutoff violation count
coverage_input_sha256
policy_selected = false
automatic_promotion = false
```

Age summaries use deterministic count/min/median/p90/max calculations over finite values. The report must not output a proposed `max_last_trade_age_seconds`, timing choice, calibration, edge threshold, P&L, accuracy, or outcome metric.

- [ ] **Step 1: Write RED report tests**

Seed only V2 feature rows and assert deterministic counts/quantiles/hash. Add source-inspection assertions that the module contains no imports/references to labels, official outcome, evaluation, paper settlement/order/fill, P&L, calibration, or Phase 9 threshold sources.

- [ ] **Step 2: Write READ ONLY CLI RED test**

For PostgreSQL, the CLI must begin a transaction and issue `SET TRANSACTION READ ONLY` before reporting; for SQLite tests it must remain SELECT-only. The CLI output must include `policy_selected=false` and `automatic_promotion=false`.

- [ ] **Step 3: Verify RED**

Push tests first; expected failures are missing coverage modules/scripts only.

- [ ] **Step 4: Implement deterministic read-only coverage report**

Use the existing canonical hash helper for `coverage_input_sha256`. Do not persist report rows or candidate policy configuration in Gate A.

- [ ] **Step 5: Verify GREEN**

Require targeted coverage tests and full CI.

- [ ] **Step 6: Commit checkpoint**

Commit message: `feat: add outcome-blind V2 coverage report`

---

### Task 5: Gate A regression verification and review packaging

**Files:**
- Modify only if needed for CI compile coverage: `.github/workflows/ci.yml`
- Update: `docs/superpowers/specs/2026-09-02-phase-14-timestamp-coherent-market-price-v2-design.md` status line from design-review-pending to implementation-under-review only after all code tests pass.
- Update canonical source-of-truth again only with implementation evidence that actually exists; do not claim production rollout or coverage results before they occur.

- [ ] **Step 1: V1 immutability regression checks**

Verify the implementation did not alter:

```text
src/bp_engine/features/models.py FEATURE_VERSION="core-v1"
src/bp_engine/features/service.py V1 semantics
src/bp_engine/live_prediction/* V1 prediction semantics
src/bp_engine/calibration/* accepted V1 policy semantics
selected-book freshness = 10 seconds
```

The repository diff must contain no live-order activation, wallet/secret, risk-limit increase, geoblock bypass, or Phase 15 code.

- [ ] **Step 2: Full verification**

Require a fresh branch-head CI run with Ruff and the entire pytest suite passing. Also require relevant existing recorder smoke/short-soak workflows to pass before proposing any recorder production rollout. These workflow results prove code quality/transport compatibility only; they do not authorize deployment.

- [ ] **Step 3: Review changed-file scope**

Use the branch diff against current `main` and inspect every changed file. Expected implementation scope is recorder-state provenance, new V2 feature/report modules/tests/scripts, plan/spec/status docs, and at most CI compile/test wiring. Any change to execution/live-readiness money controls is a stop condition.

- [ ] **Step 4: Open a draft PR**

Open a draft PR from `phase14-market-price-v2-design` to `main` summarizing:

- V1 defect boundary;
- Gate A-only implementation;
- RED/GREEN checkpoint SHAs and CI results;
- no migration;
- no V2 policy selected;
- no production rollout performed;
- V1 evidence remains separate;
- live trading/zero-money/Phase 15 state unchanged.

- [ ] **Step 5: Stop before production deployment**

Do not install/restart/roll out the recorder or start forward V2 feature collection from this plan. Present the verified PR and, separately, a bounded production rollout/acceptance proposal for explicit user authorization.

## Completion criteria

Gate A implementation is complete only when all of the following are true:

1. Polymarket last-trade source/receipt timestamps and dedupe identity survive compact-state reduction and are not refreshed by unrelated book/change events.
2. V2 as-of readers use exact token IDs and dedicated last-trade timestamps, never generic `last_event_at` as trade freshness.
3. `core-v2-last-trade` 5m rows are immutable, forward-only in intended operational use, outcome-independent, and future-data perturbation safe.
4. The 10-second selected-book freshness contract is unchanged.
5. The coverage report is read-only/outcome-blind and selects no policy.
6. No V1 calibration, timing, edge, prediction, or evidence row is rewritten or reinterpreted as V2 evidence.
7. Full CI passes on the final branch head.
8. No production rollout, live money, or Phase 15 transition has occurred.
