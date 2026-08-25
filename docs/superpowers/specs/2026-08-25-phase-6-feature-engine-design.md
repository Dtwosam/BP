# Phase 6 Feature Engine Design

**Date:** 25 August 2026  
**Phase:** 6 — Feature engine  
**Status:** Approved under standing project authorization  
**Base:** `main` after Phase 5 merge `da32120c86e1aac787a8b37438ee71bb4eae60e1`

## Objective

Build a deterministic, immutable, leakage-safe feature engine for BTC Polymarket Up/Down research. A feature row must represent only information that was available at its `feature_at` timestamp. The same selected source observations plus the same feature version must produce the same feature payload and input fingerprint.

Phase 6 does not train models, backtest strategies, produce live predictions, execute paper orders, or enable live trading.

## Constraints inherited from earlier phases

- Official resolved Polymarket outcome is a label and must never be read by feature computation.
- Phase 5 `start_reference` and `end_reference` are NULL because no independently verified first-party reference-price field was established. Feature code must not silently replace them with Coinbase, Bybit, or CLOB prices.
- Historical first-party Polymarket L2 is unavailable/unverified outside captured recorder state and must never be synthesized.
- Bybit REST history can be unavailable on US-hosted environments; missing Bybit historical inputs must remain explicit.
- The known Phase 3 damaged raw interval `2026-08-22T20:00:00Z <= t < 2026-08-22T21:00:00Z` is excluded from raw-dependent feature calculation.
- The Phase 3 rollout-era raw-coverage limitation after `2026-08-23T21:00:00Z` and before `2026-08-24T10:18:16.692122Z` is excluded from raw-dependent feature calculation unless independently recovered.
- `market_state_1s` is retained for 90 days and is valid observed state only when both `bucket_at <= feature_at` and `last_event_at <= feature_at`.
- Live trading remains disabled and trade/loss limits remain zero.

## Approaches considered

### A. Versioned JSON feature snapshots with strict as-of readers — selected

Persist one immutable row per `(condition_id, feature_at, feature_version)`. The row contains a JSON feature payload, explicit missing flags, source cutoffs, an input fingerprint, and a feature hash. Feature computation is split into small source readers that enforce time cutoffs before values reach calculators.

Advantages:
- feature versions can evolve without a schema migration for every feature;
- leakage enforcement is centralized and testable;
- missing/unavailable source semantics stay explicit;
- reproducibility can be proven with input and output hashes;
- later model-dataset code can flatten a chosen feature version.

Trade-off: SQL ad-hoc analysis is less convenient than a fully wide typed table.

### B. One wide typed SQL table per feature version

Advantages: convenient analytics and strong column typing.  
Rejected for V1 because every feature experiment would create migration churn and couple research iteration to database schema changes.

### C. SQL views/materialized views over source tables

Advantages: concise SQL and no duplicate persisted features.  
Rejected because reproducibility becomes dependent on mutable retention/availability of source rows, versioning is awkward, and leakage rules are harder to enforce consistently across PostgreSQL and offline tests.

## Feature row contract

Create additive table `market_features`.

Natural key:

```text
(condition_id, feature_at, feature_version)
```

Required columns:

- `condition_id`
- `slug`
- `horizon_seconds`
- `market_start_at`
- `market_end_at`
- `feature_at`
- `feature_offset_seconds`
- `feature_version`
- `features` JSON
- `missing_flags` JSON
- `source_cutoffs` JSON
- `input_fingerprint` SHA-256
- `feature_hash` SHA-256
- `generated_at`

Checks:

- `horizon_seconds > 0`
- `market_end_at > market_start_at`
- `market_start_at < feature_at < market_end_at` for V1 generated rows
- unique natural key

Repository semantics mirror Phases 4 and 5:

- first insert returns created;
- exact semantic rerun returns existing and preserves original `generated_at`;
- any semantic difference at the same natural key raises `FeatureConflict`;
- `generated_at` is not part of semantic equality.

## Feature version

Initial version is:

```text
core-v1
```

The version fixes:

- source-selection rules;
- staleness thresholds;
- trailing-window lengths;
- formulas;
- missing-data semantics;
- raw-data exclusions;
- feature names.

Changing any of those requires a new feature version rather than rewriting `core-v1` rows.

## Target and timestamp planning

The core calculator consumes a `FeatureTarget` containing only static market metadata:

- condition id;
- slug;
- horizon;
- market start/end.

It does not consume outcome, resolved outcome, label references, or any post-resolution label metadata.

For historical batch generation, the target loader may read the Phase 5 label table only through a strict static-column projection that excludes `official_outcome`, references, source resolution time, and label-generation time. Labels are joined to features only in later model-dataset work.

V1 batch cadence is one feature row per completed minute inside the market:

```text
feature_at = market_start_at + N * 60 seconds
N >= 1
feature_at < market_end_at
```

This yields four V1 rows for a 5-minute market and fourteen for a 15-minute market. It deliberately does not decide the eventual optimal prediction time; later model evaluation can compare offsets without regenerating source features.

## As-of source boundary

No calculator queries tables directly. `FeatureSourceReader` is responsible for selecting observations and proving each selected observation is available by `feature_at`.

A selected observation must have an effective availability timestamp no later than `feature_at`.

### Polymarket token prices

Source: `polymarket_price_history`.

For each outcome (`Up`, `Down`), select the latest observation satisfying:

```text
condition_id = target condition
observed_at <= feature_at
```

Record its `asset_id`, observation time, fidelity, and price in the input fingerprint. If either side is absent, outcome-pair features are NULL and the corresponding missing flag is true.

### BTC candles

Primary historical source: Coinbase `BTC-USD`, one-minute candles in `btc_candles`.

A candle is usable only after it is fully closed:

```text
bucket_at + interval_seconds <= feature_at
```

Merely having `bucket_at <= feature_at` is not sufficient.

Bybit spot/linear candle features are optional. If historical rows do not exist because the source was audited unavailable, their feature values are NULL and explicit missing flags are true.

### Compact one-second state

Source: `market_state_1s`.

A snapshot is usable only when:

```text
bucket_at <= feature_at
last_event_at <= feature_at
```

V1 considers a compact state fresh when `feature_at - last_event_at <= 10 seconds`. Stale snapshots are not converted into current-state values; the group is marked stale/missing.

For Polymarket outcome books, the latest price-history observation identifies the outcome asset id, which is then used to locate compact state for that token. No future market snapshot is needed to map Up/Down token state.

### Raw trade flow

Source: `raw_market_events` over the trailing 60 seconds ending at `feature_at`.

The flow calculator supports observed trade events from:

- Polymarket market stream;
- Coinbase spot market trades;
- Bybit spot trades;
- Bybit linear trades.

Raw trade flow is computed only when the trailing window does not overlap a known raw-data exclusion and there is evidence of feed coverage in the same trailing window. If raw data are pruned, excluded, or feed coverage is unproven, flow values are NULL with explicit flags; absence is not silently interpreted as zero flow.

## Input fingerprint

Every selected input contributes a deterministic canonical observation descriptor. Examples:

- historical token price: source natural key + observed value;
- candle: source/market/symbol/interval/bucket + OHLCV;
- compact state: row id, state key, bucket, last event time, selected state fields;
- raw event: dedupe key, received time, event type, selected trade fields.

Descriptors are sorted and canonical-JSON encoded, then SHA-256 hashed as `input_fingerprint`.

The feature payload plus missing flags is canonicalized and SHA-256 hashed as `feature_hash`.

No label outcome or post-resolution label field may appear in either hash input.

## V1 feature groups

### 1. Time / market geometry

Always available from static market metadata:

- `seconds_elapsed`
- `seconds_remaining`
- `fraction_elapsed`
- `horizon_seconds`

### 2. Polymarket price state

When both outcome prices are available:

- `pm_up_price`
- `pm_down_price`
- `pm_price_sum`
- `pm_up_minus_down`
- `pm_up_price_staleness_s`
- `pm_down_price_staleness_s`

These are market-state inputs, not labels.

### 3. Polymarket book state

When fresh compact state exists for an outcome token:

- best bid/ask;
- mid;
- spread;
- bid depth;
- ask depth;
- book imbalance `(bid_depth - ask_depth) / (bid_depth + ask_depth)` when denominator is positive.

Up and Down token features are stored separately. Missing historical L2 remains NULL.

### 4. Coinbase BTC price / momentum / volatility

Using only fully closed one-minute candles:

- latest close;
- 1-minute return;
- 5-minute return;
- 15-minute return;
- realized volatility over 5 and 15 one-minute returns;
- return from the last fully closed pre-market-start Coinbase candle, named explicitly as a proxy and never as the official Chainlink reference.

### 5. Bybit derivatives/cross-market state

When observed compact state is fresh:

- spot last/mid where available;
- linear last/mark/index;
- funding rate;
- open interest;
- linear-vs-spot basis where both sides are available.

Missing historical Bybit REST data does not prevent compact-state features when the recorder actually observed the WebSocket feeds.

### 6. Basic trailing trade flow

For each supported feed over 60 seconds:

- buy volume;
- sell volume;
- signed volume (`buy - sell`);
- trade count.

If source semantics report side from the taker/aggressor perspective, preserve that native meaning and document it in the parser. Do not infer side when the source event does not provide one.

### 7. Official reference distance

`official_reference_distance` is NULL in `core-v1` and `official_reference_missing=true` because Phase 5 did not verify an authoritative reference value.

A separate `coinbase_return_from_prestart_close` may be populated as an explicitly named proxy. It must never be presented as the official Polymarket/Chainlink reference distance.

## Missing flags

At minimum V1 stores booleans for:

- `pm_up_price_missing`
- `pm_down_price_missing`
- `pm_up_book_missing`
- `pm_down_book_missing`
- `coinbase_candles_missing`
- `bybit_spot_state_missing`
- `bybit_linear_state_missing`
- `raw_trade_flow_missing`
- `raw_trade_flow_excluded`
- `official_reference_missing`

Staleness is represented separately from source absence when useful.

## Source cutoffs

`source_cutoffs` records the latest effective timestamp actually consumed per source group. Every non-NULL cutoff must satisfy:

```text
cutoff <= feature_at
```

The service validates this invariant before persistence. A future cutoff raises `FeatureLeakageError`.

## Error handling

Fail closed for:

- timezone-naive timestamps;
- feature time outside the market window;
- selected source data after `feature_at`;
- a partially open candle being selected;
- contradictory static target metadata at one condition;
- semantic conflict at an existing feature natural key;
- non-finite numeric calculation;
- malformed trade events that would otherwise be counted with ambiguous semantics.

Missing optional source data is not an exception; it produces NULL feature values and explicit missing flags.

## Testing strategy

### Unit tests

- schema and unique/check constraints;
- repository first insert / exact rerun / conflict;
- candle close-time boundary (`bucket + interval <= feature_at`);
- token-price as-of boundary;
- compact-state `bucket_at` and `last_event_at` boundaries;
- stale-state handling;
- trade-flow parsers for each source;
- raw exclusion overlap;
- book imbalance and volatility formulas;
- input/feature hash determinism;
- target planner minute offsets.

### Future-data tests

The critical automated proof is perturbation-based:

1. generate a feature row at `T`;
2. insert/change source observations strictly after `T`;
3. regenerate at `T`;
4. assert identical feature payload, missing flags, source cutoffs, input fingerprint, and feature hash.

Additional tests inject a source observation with an effective timestamp after `T` through the reader boundary and assert `FeatureLeakageError`.

### PostgreSQL integration

Against PostgreSQL 16:

- apply migration;
- insert realistic Phase 4/recorder fixtures;
- generate a set of feature rows;
- rerun and verify zero new rows;
- verify natural-key uniqueness;
- verify every source cutoff is `<= feature_at`;
- verify no feature payload contains official outcome/label fields;
- verify semantic conflicts fail closed.

### Production host acceptance

On `bp-recorder`, using an isolated candidate worktree:

- trading must remain disabled and limits zero;
- recorder active before and after;
- apply additive feature migration;
- generate `core-v1` rows for the Phase 5 accepted market-start window;
- immediate rerun inserts zero rows;
- target feature rows > 0;
- all source cutoffs `<= feature_at`;
- no duplicate natural keys;
- no label/outcome keys in feature payloads;
- official reference values remain NULL/missing;
- missing optional data are explicitly flagged;
- disk health remains non-critical.

Phase 6 closes only after production acceptance, durable evidence, fresh exact-head repository gates, and merge.

## Out of scope

- label generation changes;
- model training or model selection;
- target encoding;
- backtesting;
- probability calibration;
- live prediction;
- paper/live execution;
- feature importance pruning;
- choosing a final prediction offset;
- inventing unavailable historical order books or official reference prices.
