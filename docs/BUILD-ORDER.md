# BTC Polymarket Prediction Engine — Build Order

**Version:** 0.1.0  
**Authority:** Subordinate to `MASTER-SOURCE-OF-TRUTH.md`.

This file tells a future chat or developer exactly what to build next.

---

## Operating rule

Never skip forward because a later phase looks more interesting.

For each phase:

1. implement;
2. test;
3. record evidence;
4. update `PROJECT_STATE.json`;
5. update the changelog;
6. only then move to the next phase.

---

## 0. Bootstrap the repository

Create the repository structure specified in the Master Source of Truth.

Minimum deliverables:

- Python project;
- test runner;
- linter/formatter;
- `.env.example`;
- `.gitignore`;
- Docker/Docker Compose baseline;
- PostgreSQL local service;
- config loader;
- structured logging;
- health command;
- CI if GitHub is used.

Acceptance:

- fresh clone can start the local stack from documented commands;
- tests pass;
- no secrets are committed.

---

## 1. Build Polymarket market discovery first

Why first: the whole project ultimately trades specific Polymarket markets. We need to know exactly what market exists, its token IDs, interval, start/end, and rules.

Build:

- Gamma API client;
- recurring BTC Up/Down market discovery;
- parser for horizon/start/end;
- storage in `polymarket_markets`;
- official rules/resolution source capture;
- active/closed/resolved state updater;
- unit fixtures from real API responses.

Important:

- horizons are config-driven;
- do not assume 10m exists;
- rule changes must be detectable.

Acceptance:

- current 5m/15m BTC Up/Down markets can be found without manually pasting IDs.

---

## 2. Build the 24/7 recorder

Start recording immediately because private high-frequency history becomes more valuable every day.

### Polymarket collector

Record:

- market WebSocket events;
- book snapshots/changes;
- best bid/ask;
- spread;
- last trade;
- timestamps;
- reconnect/gap events.

### BTC collector

Begin with one primary venue, then add a secondary venue.

Record the minimum useful raw/aggregated data:

- trades;
- top/order-book data;
- best bid/ask;
- volume/flow;
- derivatives signals where available.

### Reliability

Implement:

- reconnect with backoff;
- health heartbeat;
- stale-feed detection;
- UTC timestamps;
- NTP requirement;
- idempotent writes;
- graceful restart.

Acceptance:

- 24-hour soak test;
- no unexplained data gaps;
- gaps/reconnects are explicitly logged.

---

## 3. Add retention and aggregation

Before raw data grows uncontrolled:

- decide snapshot/update retention;
- keep compact derived intervals;
- add database indexes/partitions as needed;
- measure disk growth/day;
- add disk-space alert.

Acceptance:

- estimate how long the free server can retain data;
- database stays performant.

---

## 4. Historical backfill

Build reproducible scripts for:

- Polymarket market/event history;
- Polymarket historical token prices;
- BTC candles/trades/other available history.

Save:

- source;
- download timestamp;
- source parameters;
- data version/checksum when practical.

Do not pretend unavailable historical order-book detail exists.

Acceptance:

- backfill can be re-run without duplicating/corrupting data;
- historical limitations are documented.

---

## 5. Official outcome/label pipeline

Create labels from official resolved markets.

For each target market:

- capture official outcome;
- capture start/end reference where available;
- verify market rule;
- attach label only after resolution.

Acceptance:

- labels are deterministic;
- unresolved markets cannot accidentally be treated as labels;
- leakage tests pass.

---

## 6. Feature engine

Build features in groups.

Start with:

1. Polymarket state;
2. BTC price/momentum;
3. basic trade flow;
4. order-book imbalance;
5. volatility;
6. time remaining/reference distance.

Then derivatives/cross-market features.

Rules:

- feature calculation only uses data available at feature timestamp;
- feature versions are immutable;
- missing data gets explicit flags.

Acceptance:

- same raw inputs + same feature version = same output;
- automated future-data test passes.

---

## 7. Baselines before fancy ML

Build:

- majority/naive baseline;
- simple market-price baseline;
- logistic regression;
- LightGBM or XGBoost.

Metrics:

- accuracy;
- balanced accuracy where useful;
- log loss;
- Brier score;
- calibration;
- confidence coverage;
- P&L under simulated execution.

Acceptance:

- boosted model must beat simple baselines out-of-sample before more complexity is justified.

---

## 8. Walk-forward backtester

Requirements:

- chronological data only;
- purging/embargo for overlap where required;
- no random leakage;
- configurable train/validation/test windows;
- frozen evaluation outputs;
- regime breakdown;
- realistic executable prices.

Acceptance:

- one command reproduces an evaluation report from a dataset/model version.

---

## 9. Probability calibration + edge engine

Implement calibrated probabilities.

Then compute potential trade edge using the executable market price.

First version may remain simple:

```text
gross_edge = p_model - executable_price
```

Then subtract/penalize for:

- spread;
- fees;
- slippage;
- uncertainty;
- staleness.

Acceptance:

- no trade when the configured minimum edge is not met;
- edge calculations have unit tests.

---

## 10. Live prediction engine

Run on live feeds with money disabled.

Every prediction stores:

- model version;
- feature version;
- timestamp;
- market;
- probability;
- predicted side;
- market bid/ask;
- edge;
- decision.

After resolution, append outcome/evaluation without rewriting the original prediction.

Acceptance:

- dashboard can prove predictions existed before outcomes.

---

## 11. Dashboard V1

Only build what is useful:

- active markets;
- model probabilities;
- market prices;
- edge/action;
- feed health;
- prediction history;
- accuracy/calibration;
- paper P&L;
- current mode.

Acceptance:

- someone can understand system health/performance without opening the database.

---

## 12. Paper execution

Implement the same interface live trading will use, but use simulated orders.

Model:

- bid/ask;
- depth;
- partial fills;
- latency;
- slippage;
- cancellations;
- expiry;
- fees.

Acceptance:

- paper trades reconcile against immutable signals.

---

## 13. Improvement loop

Test hypotheses, not random features.

For each experiment:

- hypothesis;
- data range;
- features;
- model;
- validation method;
- result;
- decision: reject/keep.

Use champion/challenger promotion.

Acceptance:

- no model is promoted because of one unusually good backtest.

---

## 14. Live readiness

Do not proceed unless Master Source of Truth live gate passes.

Add:

- Polymarket geoblock check;
- official SDK trading client;
- wallet/funder setup;
- secret storage;
- risk engine;
- order reconciliation;
- kill switch;
- live-mode interlock.

Acceptance:

- live mode defaults OFF;
- integration tests cannot accidentally spend money;
- explicit user authorization is documented before any real-money transition.

Engineering status: complete. Production non-spending host acceptance passed on exact candidate `5854e3003aa3340ce3733bf4532e204c1ec55836`, including SDK import, fail-closed interlock/risk checks, reconciliation, active-service checks, and `REAL_ORDER_SIDE_EFFECTS=0`. This does **not** mean the Master live gate passed.

---

## 15. Controlled live launch

Start deliberately small.

Measure:

- quoted vs filled price;
- latency;
- rejected/cancelled orders;
- slippage;
- live P&L;
- divergence from paper assumptions.

Do not automatically increase stake.

**Current status:** blocked. Do not begin Phase 15 until every Master Source of Truth live-gate item is `pass` and explicit real-money authorization exists.

---

## Immediate next action

**Review the completed Gate A timestamp-coherent `market_price` V2 implementation as a draft PR; do not deploy it, do not select a V2 policy, and do not start Phase 15.**

Gate A implementation on `phase14-market-price-v2-design` is code/test complete and under review. It preserves dedicated Polymarket `last_trade_price` source/receipt timestamps and dedupe identity in compact state, adds an exact-token/as-of V2 source reader, materializes the separate 5m `core-v2-last-trade` feature contract at 60/120/180/240-second offsets, and adds a deterministic read-only outcome-blind coverage reporter. The implementation checkpoints passed full CI as Task 1 CI #1913, Task 2 CI #1918, Task 3 CI #1934, and Task 4 CI #1939.

The frozen V1 path remains unchanged: `FEATURE_VERSION="core-v1"`, `src/bp_engine/features/service.py`, the full `src/bp_engine/live_prediction` subtree, and the full `src/bp_engine/calibration` subtree are byte-identical to `main`; selected-book freshness remains exactly 10 seconds. The Gate A diff contains no migration, live-order activation, wallet/secret change, risk-limit increase, geoblock bypass, or Phase 15 implementation.

Gate A has **not** been deployed. Therefore no forward production V2 provenance/feature-coverage epoch has been established and no production coverage result may be claimed. `policy_selected=false` and `automatic_promotion=false`; no V2 timing, last-trade freshness threshold, calibration, minimum-edge threshold, model policy, prospective V2 prediction path, or V2 paper-execution path has been selected or activated.

Treat existing `live-prediction-v1` predictions, paper orders, settlements, and P&L as immutable evidence of the deployed V1 pipeline. Do not rewrite or discard them, do not combine them with any future V2 profitability epoch, and do not use the 27 V1 failures to choose V2 freshness/calibration/edge parameters.

After draft-PR review, any recorder/V2 production rollout is a **separate explicit authorization step** with its own bounded acceptance and rollback plan. If such a rollout is later authorized and passes, collect only forward timestamped V2 source evidence first; run the outcome-blind coverage report before labeled/economic policy selection; and pre-register any finite freshness candidate set before joining outcomes. Until then, V2 remains research-only and undeployed.

`LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0` remain mandatory. The Master live gate remains `fail`, geographic/compliance eligibility remains unresolved/failed for live launch, explicit real-money authorization is absent, and Phase 15 remains blocked.
