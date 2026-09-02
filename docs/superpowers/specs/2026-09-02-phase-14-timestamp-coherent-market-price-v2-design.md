# Phase 14 — Timestamp-Coherent Market-Price V2 Design

**Date:** 2 September 2026  
**Status:** Approved architecture; design review pending before implementation planning  
**Phase:** 14 — live readiness research correction  
**Mode:** RESEARCH only; live trading disabled; all real-money limits remain zero  
**Canonical decision:** D-033  
**Base branch:** `phase14-market-price-v2-design` from `be1f82f65d15b2e172495e6ae934ec9a78648c32`

## 1. Purpose

The accepted V1 `market_price` research path is not timestamp-coherent. It compares a Polymarket CLOB `/prices-history` probability observation with a separately fresh executable WebSocket book ask. The live timing probe over 27 settled 5m paper trades found that every V1 probability observation was 33–51 seconds old at the scheduled prediction time while every selected-side book was approximately 0–1 second old. That mismatch can turn ordinary market movement between two different effective times into very large apparent edge.

This design creates a new, forward-compatible market-price research path whose probability source has explicit event and availability timestamps. It does **not** patch V1 in place, does not choose a new edge threshold from the failed V1 sample, does not enable live trading, and does not start Phase 15.

The design intentionally decomposes the work into three gated subprojects:

1. **V2 provenance + forward research features** — preserve timestamped Polymarket last-trade evidence and materialize a new immutable feature version. This is the first implementation plan after design approval.
2. **V2 model/timing/calibration/edge validation** — only after sufficient independent V2 evidence exists; choose timing, market-price freshness eligibility, calibration, and edge policy through new chronological validation.
3. **V2 prospective shadow/paper epoch** — only after a V2 policy is independently accepted; collect a separate prospective evidence epoch before any future live-gate reassessment.

No later subproject is implicitly authorized by implementing an earlier one.

## 2. Root cause and why V1 remains immutable

### 2.1 V1 probability source

V1 uses the latest first-party Polymarket CLOB `/prices-history` Up-token point satisfying:

```text
observed_at <= scheduled_at
```

The request uses one-minute fidelity. V1 records the selected point timestamp, but it imposes no maximum probability age.

### 2.2 V1 executable source

The selected-side bid/ask comes from `market_state_1s`. The existing source reader requires state to be known by feature/prediction time and applies the frozen 10-second compact-state freshness rule.

### 2.3 V1 edge mismatch

The V1 edge engine computes, conceptually:

```text
calibrated probability based on older price-history point
    minus
fresh selected-side executable ask
```

The two values can therefore represent materially different market states.

### 2.4 Evidence policy

Existing V1 artifacts remain immutable and truthful evidence of the deployed V1 pipeline:

- `core-v1` feature rows;
- Phase 7 model-training runs;
- Phase 8 walk-forward runs;
- Phase 9 calibration/edge runs;
- `live-prediction-v1` predictions and evaluations;
- paper orders, fills, terminal events, settlements, reconciliation, and P&L.

They are not rewritten, deleted, relabeled, or recomputed under V2 semantics. V1 prospective P&L remains evidence that **V1 as deployed loses money after costs**.

V1 economics must not be blended with V2 economics and must not be used to choose V2 timing, freshness, calibration, or edge parameters.

## 3. Non-goals and hard safety boundaries

This work does not:

- enable real-money trading;
- add or activate a real-order path;
- alter `LIVE_TRADING_ENABLED=false`;
- increase `MAX_TRADE_SIZE_USD` above zero;
- increase `MAX_DAILY_LOSS_USD` above zero;
- start Phase 15;
- bypass or route around Polymarket geographic restrictions;
- loosen the existing selected-book 10-second freshness rule;
- retune V1 `min_edge`, calibration, timing, or model from the 27 failed V1 trades;
- silently substitute midpoint, selected ask, opposite-token transforms, or an untimestamped REST last-trade response for the V2 probability source;
- claim that fixing timestamp coherence proves profitability.

`automatic_promotion=false` remains mandatory throughout all V2 stages.

## 4. Design principles

### 4.1 New semantics require new versions

V2 is not a bugfix that changes the meaning of existing immutable rows. Every semantic layer that changes receives a new version or a new run identity.

The intended names are:

```text
feature_version       = core-v2-last-trade
source model family   = market_last_trade
live_input_version    = phase14-live-market-input-v2       # later subproject
prediction_version    = live-prediction-v2                 # later subproject
```

Dataset, walk-forward, calibration, and edge run identities remain content-addressed and must explicitly reference `core-v2-last-trade`. Their exact run IDs are generated from immutable semantics rather than predeclared here.

V1 version constants and hashes remain unchanged.

### 4.2 Availability time is different from provider event time

A V2 Polymarket last-trade observation has two time concepts:

- `source_at`: timestamp supplied by the Polymarket WebSocket event;
- `received_at`: UTC timestamp at which BP received the event.

For leakage/availability, **`received_at` is authoritative**: BP cannot use an event before BP actually received it.

For provider chronology and diagnostics, `source_at` is also preserved when valid. V2 must never replace one with the other silently.

### 4.3 Freshness belongs to the specific observation

Generic compact-state `last_event_at` cannot represent last-trade freshness. A later `book` or `price_change` event can refresh the compact state without refreshing the last trade.

V2 therefore preserves dedicated last-trade timestamps that change **only** when a `last_trade_price` event for that exact token is observed.

### 4.4 Missing/stale evidence fails closed

No training/evaluation row may pretend a missing last-trade observation is current market-price evidence. No eventual V2 trade policy may treat a missing or policy-stale last trade as executable edge.

The default before V2 policy validation is therefore `no_trade`/shadow-only.

## 5. Recorder provenance contract

### 5.1 Existing raw event remains source of truth

The existing Polymarket Market WebSocket parser already records `last_trade_price` as an immutable `RawEvent` and parses a provider timestamp for non-book event types when the payload supplies one.

That raw event remains the strongest provenance record. V2 does not invent a second event source.

### 5.2 Dedicated compact-state fields

For each exact Polymarket token state, a `last_trade_price` event updates the following explicit fields:

```text
last_trade_price
last_trade_size
last_trade_side
last_trade_source_at
last_trade_received_at
last_trade_event_dedupe_key
```

The existing generic `last_price`, `last_trade_size`, and `last_trade_side` behavior may remain for backward compatibility, but V2 consumers use the explicit V2 trade fields above.

`book` and `price_change` events may continue updating book/change state and generic `last_event_at`, but they **must not** modify:

```text
last_trade_source_at
last_trade_received_at
last_trade_event_dedupe_key
```

unless the incoming event itself is `last_trade_price` for the same exact token.

### 5.3 Timestamp checks

For a V2 last-trade event to be eligible as timestamped evidence:

- `received_at` must be timezone-aware UTC after normalization;
- a provider `source_at` must be present and parseable for the V2 probability source;
- both timestamps and the raw event identity are preserved;
- V2 as-of readers require `received_at <= feature_at`;
- V2 as-of readers require `source_at <= feature_at`;
- any existing feed clock-skew incident remains visible and is not hidden by timestamp normalization.

If a Market WebSocket last-trade payload lacks a usable provider timestamp, the raw event remains valid recorder evidence but is **not** V2 probability evidence.

### 5.4 No retrospective state rewrite

Existing `market_state_1s` rows are not rewritten to manufacture dedicated last-trade timestamps. New fields appear only in snapshots produced after the recorder change.

Historical raw events may be inspected read-only where retained, but V2 may not reconstruct an exact last-trade timestamp from generic historical `last_event_at` or from unchanged `last_price` values and then present that inference as exact provenance.

## 6. `core-v2-last-trade` feature contract

### 6.1 Scope

The first V2 research feature version targets the **5-minute horizon only** because the demonstrated economic defect is in the active 5m trade policy. The accepted 15m policy remains `no_trade`; 15m V2 work requires separate evidence and is not bundled into the first implementation plan.

### 6.2 Forward-only materialization

`core-v2-last-trade` begins only after the recorder provenance change is deployed and proven. It does not backfill old rows from incomplete compact-state provenance.

For every eligible 5m market, V2 materializes the same minute-inside-market research cadence used by the feature engine:

```text
60s, 120s, 180s, 240s after market start
```

This deliberately avoids carrying forward V1's selected 240-second prediction timing. Future V2 walk-forward validation must choose timing again from the V2 data.

### 6.3 V2 market-price fields

At minimum the V2 feature payload records:

```text
pm_up_last_trade_price
pm_up_last_trade_source_age_s
pm_up_last_trade_availability_age_s
pm_down_last_trade_price
pm_down_last_trade_source_age_s
pm_down_last_trade_availability_age_s
```

The input fingerprint records the exact token, raw-event dedupe key, source timestamp, received timestamp, price, size/side when present, and the compact-state row used to retrieve the observation.

The feature row also retains the existing separate Up/Down executable book fields and their missing/stale semantics. The selected-book 10-second freshness behavior is not changed.

### 6.4 As-of semantics

For a feature at `T`:

```text
last_trade_received_at <= T
last_trade_source_at   <= T
book bucket_at         <= T
book last_event_at     <= T
```

A later source event cannot change the regenerated feature at `T`.

The feature engine must include perturbation tests proving that events inserted strictly after `T` do not alter V2 payloads, missing flags, source cutoffs, fingerprints, or feature hashes.

### 6.5 Missing semantics

The V2 feature layer records absence and age; it does **not** choose an economic freshness cutoff.

At feature-generation time:

- no timestamped last trade at/before `T` => last-trade price fields are missing;
- a timestamped last trade exists => price and both ages are recorded even if old;
- book freshness continues to use the existing 10-second rule.

This separation is intentional: source capture must remain descriptive, while the V2 research policy later selects a finite last-trade eligibility rule without rewriting the source feature version.

## 7. Independent freshness-policy selection

### 7.1 No threshold from the V1 failure sample

The 27 V1 settled trades may be used to establish the **existence** of the V1 defect. Their outcomes, P&L, edge values, and observed 33–51 second ages cannot be used to choose the V2 last-trade freshness cutoff or candidate grid.

### 7.2 Coverage-only pre-registration gate

Before V2 labels/outcomes are joined for model or economic selection, run a coverage-only report over `core-v2-last-trade` timestamps. The report may examine only:

- observation availability;
- last-trade source/receipt ages;
- book availability/age;
- source timestamp ordering;
- feed incident metadata;
- market/horizon/time metadata.

It may **not** read:

- official outcomes;
- correctness;
- paper settlements;
- V1/V2 P&L;
- calibration metrics.

That coverage-only report freezes a finite candidate set of `max_last_trade_age_seconds` values plus explicit `no_trade`. The candidate set must have a maximum no greater than the already-frozen 10-second selected-book freshness ceiling; this prevents the V2 probability leg from being permitted to be older than the maximum age already allowed for the execution leg.

The exact candidate values and the canonical hash of the coverage-only selection input are committed as research configuration **before** any labeled V2 model/edge selection run. This is the independent derivation required by D-033.

### 7.3 Validation-only selection

Once the candidate set is frozen, a later V2 research run may choose among those candidates only from its allowed training/validation context. Test, final holdout, V1 prospective evidence, and later V2 prospective evidence cannot rewrite the choice.

Every accepted V2 economic policy must have a finite `max_last_trade_age_seconds`. An unlimited/`None` market-price age is not an eligible V2 trading policy.

## 8. V2 model and timing research

This is the second subproject and begins only when the forward V2 source has enough evidence to construct legitimate chronological partitions.

### 8.1 New dataset identity

Build a new supervised dataset identity tied to `core-v2-last-trade`. It joins official outcomes only after immutable V2 feature generation, preserving the existing label authority and no-leakage rules.

V1 datasets remain unchanged.

### 8.2 Source model

The first simple V2 source baseline is:

```text
family    = market_last_trade
predictor = pm_up_last_trade_price
```

No midpoint, executable ask, opposite-side transform, or V1 `/prices-history` value substitutes when the predictor is missing.

A missing V2 market-price observation is explicit. It does not become a trade signal through a training-prior fallback.

### 8.3 Timing selection

V2 timing is reselected from the V2 feature rows on validation only. V1's 240-second 5m selection is historical evidence and is **not** automatically inherited.

### 8.4 Model complexity

The first V2 question is whether timestamp-coherent first-party market-price evidence is itself a useful baseline. No more-complex model is promoted merely because the V1 baseline was defective.

Any later logistic/XGBoost challenger follows the existing rule that complexity must beat simple baselines out of sample.

## 9. V2 calibration and edge research

This remains part of the second subproject, after a source/timing model is accepted for evaluation.

### 9.1 Calibration

V1 calibration coefficients are not reused as V2 coefficients. V2 calibration is fit only on permitted V2 training data and selected only on V2 validation data.

Identity remains the mandatory baseline. Any challenger must preserve the same leakage and monotonicity safeguards already established by the project.

### 9.2 Executable price

V2 continues to use the observed selected-side best ask as executable price. Midpoint and synthetic fills remain forbidden.

The existing selected-book 10-second freshness contract remains unchanged.

### 9.3 Market-price age eligibility

A candidate V2 edge policy is executable only if all of the following are true:

- timestamped last-trade probability evidence exists;
- last-trade receipt/source cutoffs are at or before prediction time;
- last-trade availability age is within the candidate's finite pre-registered maximum;
- selected-side book exists and satisfies the existing 10-second freshness rule;
- selected-side ask/bid/spread satisfy the usual numeric integrity rules.

Otherwise the reason is explicit and the action is no-trade.

### 9.4 Edge threshold

V1 `min_edge` is not carried forward. V2 minimum-edge selection starts from a new validation-only run under V2 eligibility. `no_trade` remains a first-class candidate.

A positive result on training/validation alone cannot authorize paper/live promotion. Untouched evaluation and then separate prospective confirmation remain required.

## 10. Prospective V2 prediction architecture

This is the third subproject and does not begin merely because recorder/feature V2 exists.

### 10.1 New versions

When an independently accepted V2 policy exists, prospective prediction uses:

```text
live_input_version = phase14-live-market-input-v2
prediction_version = live-prediction-v2
```

The existing `(condition_id, prediction_version)` natural-key design permits V1 and V2 predictions to coexist without rewriting V1.

### 10.2 Live input

V2 live prediction reads timestamped last-trade evidence from the recorder database rather than making the V1 `/prices-history` request.

The V2 immutable input provenance must include at least:

- token id;
- last-trade price;
- provider source timestamp;
- BP receive timestamp;
- raw-event dedupe key;
- raw-event/payload hash;
- selected book bid/ask and book cutoff;
- explicit last-trade and book ages;
- input fingerprint.

If any accepted policy freshness requirement is not met, the prediction records no-trade. It does not fall back to V1 `/prices-history` for trade eligibility.

### 10.3 Storage compatibility

Existing `live_predictions` V1 rows and semantic hashes remain untouched.

Before implementing V2 prospective prediction, add only the minimal additive storage required to preserve a distinct market-probability receive timestamp and raw-event identity. V1 fields stay byte-for-byte semantically stable; V1 verification must not begin hashing new V2-only columns.

V2 semantic hashing includes the V2-specific provenance fields.

### 10.4 Shadow before paper

The first production V2 runtime is shadow-only:

- prediction/evaluation evidence may be appended;
- the existing paper worker must not consume `live-prediction-v2` until a separate V2 paper-execution rollout is explicitly accepted;
- zero real-order side effects remain mandatory.

This prevents a newly versioned data correction from becoming an execution-policy change in the same deployment.

## 11. Evidence epochs

V1 and V2 are separate statistical evidence epochs.

### V1 epoch

Contains all V1 predictions/paper execution under the asynchronous `/prices-history` contract. It remains immutable and reportable as V1 evidence.

### V2 source/shadow epoch

Begins only after the V2 provenance deployment boundary. It measures timestamped last-trade coverage and, later, V2 shadow predictions/evaluations.

### V2 paper epoch

Begins only after the V2 policy and V2 paper path are separately accepted. P&L/sample/calibration gates are computed from V2 paper evidence only.

No reporter may sum or bootstrap V1 and V2 paper P&L as though they came from one unchanged policy.

## 12. Compatibility with existing services

### Recorder

The recorder change is additive: preserve extra last-trade provenance fields. Existing raw events and book behavior stay intact.

### Feature engine

`core-v1` is unchanged. `core-v2-last-trade` is a separate feature version.

### Labels

`official-outcome-v1` remains authoritative and unchanged.

### Modeling/backtesting

V1 runs remain immutable. V2 uses new dataset/run semantics.

### Dashboard/reporting

Initial V2 work may expose read-only source coverage and version labels. It must not merge V1/V2 economic headlines.

### Paper execution

The current paper worker continues to consume only its already-approved prediction version until a separate V2 paper rollout is explicitly approved and tested.

### Live readiness

The Master live gate remains `fail`. V2 engineering cannot turn a gate green merely by existing.

## 13. Error handling and fail-closed rules

Fail or mark unavailable when:

- last-trade provider timestamp is missing for a V2 probability observation;
- last-trade timestamps are malformed/non-finite/not timezone-aware after parsing;
- last-trade source or receipt time is after the as-of feature/prediction time;
- exact token/condition identity does not match;
- dedicated last-trade provenance is absent;
- a semantic V2 feature/prediction natural key conflicts with an existing immutable row;
- a V2 policy attempts to use an unlimited last-trade age;
- a V2 policy tries to inherit a V1 calibration/edge run without a V2 validation chain;
- a V2 reporter attempts to blend V1 and V2 profitability epochs;
- any deployment changes live/money safety settings.

Do not fail merely because:

- there is no recent last trade;
- coverage is low;
- the accepted result is `no_trade`;
- V2 calibration fails to improve;
- V2 holdout or prospective P&L is negative.

Those are valid research outcomes.

## 14. TDD requirements

Implementation is test-first. At minimum:

### Recorder provenance RED tests

- `last_trade_price` stores dedicated source and receive timestamps;
- later `book` does not refresh dedicated trade timestamps;
- later `price_change` does not refresh dedicated trade timestamps;
- a later actual `last_trade_price` does refresh them;
- token isolation prevents one asset's trade from updating the other asset;
- raw event dedupe identity is preserved.

### V2 feature RED tests

- newest timestamped trade known by `T` is selected;
- trade received after `T` cannot affect the feature at `T`;
- provider timestamp after `T` cannot affect the feature at `T`;
- missing provider timestamp is unavailable for V2 market-price probability;
- old last trade is recorded with age rather than silently treated as fresh;
- book freshness remains exactly the existing 10-second behavior;
- future perturbation does not change an immutable V2 feature at `T`;
- `core-v1` output and hashes remain unchanged.

### Coverage-only selection tests

- coverage report cannot query labels/evaluations/settlements;
- candidate max age is finite;
- candidate max age cannot exceed the existing 10-second book ceiling;
- candidate set is frozen before labeled V2 selection;
- V1 27-trade outcomes/P&L are not an input.

### Later V2 policy tests

- V1 selected offset is not automatically reused;
- V1 calibration fit is not automatically reused;
- V1 `min_edge` is not automatically reused;
- stale/missing V2 market-price evidence => no-trade;
- test/holdout cannot rewrite timing/freshness/calibration/edge selection;
- `no_trade` remains valid.

### Prospective V2 tests

- V1 and V2 natural keys coexist;
- V1 semantic hashes remain unchanged;
- V2 semantic hash includes dedicated trade provenance;
- shadow V2 prediction produces no paper or real order side effect;
- V1/V2 economic reports remain separate.

## 15. Acceptance gates by subproject

### Gate A — V2 provenance + feature capture

Required before any V2 model selection:

- exact candidate SHA;
- full CI green;
- existing recorder smoke/short-soak gates green;
- dedicated last-trade timestamps demonstrated on real Polymarket `last_trade_price` events;
- book/price-change events proven not to refresh trade timestamp;
- forward `core-v2-last-trade` rows created for 5m markets;
- every source cutoff <= feature time;
- V1 feature/prediction semantic evidence unchanged;
- recorder/services healthy;
- `RESEARCH`, live false, zero-money interlocks unchanged;
- zero real-order side effects.

Gate A does **not** require profitability and does not authorize paper V2 execution.

### Gate B — V2 research policy

Required before V2 prospective shadow prediction policy is considered accepted:

- enough forward V2 evidence for legitimate chronological partitions;
- coverage-only freshness candidate set frozen before label-based selection;
- timing selected only on permitted validation data;
- calibration selected only on permitted validation data;
- market-price age rule selected only from pre-registered candidates;
- edge threshold selected only on permitted validation data;
- untouched final holdout evaluated once;
- no evidence reuse/partition leakage;
- policy may validly resolve to `no_trade`.

### Gate C — V2 prospective shadow

Required before any V2 paper-execution consideration:

- new `live-prediction-v2` rows exist prospectively before outcome;
- dedicated last-trade source/receive provenance verified;
- outcome/evaluation append-only path works;
- V2 reporter separates V1 and V2 evidence;
- paper worker has zero V2 orders/fills by construction;
- money/live safety unchanged.

### Gate D — V2 paper

Requires a separate future design/implementation approval. It is not part of the first implementation plan and is not authorized by this spec review alone.

## 16. First implementation plan scope

After this design is reviewed and approved, the immediate implementation plan covers **Gate A only**:

1. recorder dedicated last-trade provenance;
2. source-reader support for exact timestamped last trades;
3. `core-v2-last-trade` 5m forward feature version;
4. read-only coverage diagnostics;
5. tests and non-spending host acceptance for provenance/feature capture.

It explicitly does **not** implement:

- V2 calibration;
- V2 minimum-edge selection;
- V2 live prediction;
- V2 paper execution;
- any live trading.

This keeps the first code change small enough to verify and ensures future policy decisions are made only after the new source evidence actually exists.

## 17. Rollback and operational safety

Gate A rollout changes only research recorder/feature provenance. Rollback returns the runtime to the previous recorder candidate; immutable raw events and any already-written `core-v2-last-trade` rows remain evidence and are not deleted.

No rollback script may reset or delete V1 research/paper ledgers.

A Gate A deployment fails closed if:

- deployed head is unexpected;
- checkout residue violates the existing guarded deployment policy;
- safety env differs from research/live-false/zero-money;
- recorder or PostgreSQL is unhealthy;
- new smoke/soak tests fail;
- any real-order side effect is observed.

## 18. Design consequences

The expected short-term outcome is **less apparent edge and potentially much lower trade coverage**. That is acceptable. The V2 goal is not to preserve V1 activity; it is to preserve causal, timestamp-coherent evidence.

If timestamp-coherent last-trade evidence is too sparse to support a reliable 5m trading policy, the correct result is `no_trade` while additional shadow evidence accumulates or a separately designed predictor is researched.

A successful V2 implementation therefore means the project can answer, with immutable provenance:

> What first-party Polymarket market-price observation had BP actually received by this prediction time, how old was that exact observation, what executable book was available at the same decision time, and was the policy allowed to compare them?

Only after that question is answered correctly should profitability be evaluated again.
