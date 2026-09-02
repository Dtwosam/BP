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

**Review the completed continuous V2 forward-coverage collector as a draft PR; do not activate it on production, do not select a V2 policy, and do not start Phase 15.**

Gate A itself is now production-accepted. The guarded rollout advanced `/opt/bp` from `be1f82f65d15b2e172495e6ae934ec9a78648c32` to `d077e45f24704e6038c947169c84527e954de975` with canonical forward epoch `2026-09-02T12:18:02Z`. Host acceptance proved real dedicated Polymarket last-trade provenance, preserved its timestamp across unrelated market activity, generated exactly four immutable `core-v2-last-trade` rows at offsets 60/120/180/240 for one completed post-epoch 5m market, found zero future-source-cutoff violations, and kept `policy_selected=false` and `automatic_promotion=false`. Sanitized evidence is `docs/evidence/phase-14-v2-gate-a-rollout-20260902.json`.

The next implementation package on `phase14-v2-gate-a-rollout-evidence` adds only continuous **outcome-blind** collection. It discovers completed post-epoch 5m markets with missing approved V2 natural keys, materializes only missing immutable rows, reports descriptive V2 coverage, and uses the database as the restart-safe checkpoint. It adds a hardened `bp-v2-forward-coverage.service` oneshot plus persistent one-minute `bp-v2-forward-coverage.timer`, and an exact-head rollback-capable rollout helper. Rollback may restore checkout/unit state but must never delete or rewrite `market_features`.

Pre-packaging exact-head CI #1978 (`33639062997`) passed all 860 Python tests, Ruff, deployment-asset validation including Python compile and Bash syntax gates, health checks, dashboard tests, TypeScript typecheck, and dashboard production build. Full diff review against deployed head `d077e45f24704e6038c947169c84527e954de975` shows only the collector/orchestration, systemd/rollout packaging, tests, CI validation, and documentation/evidence files. Frozen V1 feature service, `live_prediction`, `calibration`, and `execution` paths are unchanged; no migration, wallet/secret change, risk-limit increase, geoblock bypass, live activation, or Phase 15 implementation is present.

The continuous collector has **not** been deployed or enabled. A draft-PR review and fresh docs-complete CI/PR smoke gates come first. Any later activation of `bp-v2-forward-coverage.timer` is a separate explicit production-rollout authorization with bounded acceptance and rollback. Passing that operational rollout would establish continuing forward coverage only; it would not authorize Gate B, labels/outcomes, timing/freshness/model/calibration/edge selection, paper execution, promotion, or live trading.

Existing V1 predictions, evaluations, paper orders/fills/settlements, and P&L remain a separate immutable evidence epoch. Do not rewrite or blend them into V2 profitability evidence, and do not use the V1 failures or the single Gate A acceptance market to choose V2 freshness, timing, calibration, model, or edge parameters. Selected-book freshness remains exactly 10 seconds.

`MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0` remain mandatory. `automatic_promotion=false`; the Master live gate remains `fail`; geographic/compliance restrictions must not be bypassed; Phase 15 remains blocked; and explicit real-money authorization remains separately required.
