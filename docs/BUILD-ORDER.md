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

**Run and independently verify the read-only Phase 14 production storage preflight when host access is available; keep the recorder stopped and preserve the separate explicit authorization gate for the actual partition migration.**

The partitioned-storage implementation, read-only preflight, deterministic transcript verifier, and operator-hardening wrapper are all merged to `main`. PR #52 merged the verifier layer as `b567d1118d93910a56462ea1b45d9f2a1f728f77`; PR #53 merged the one-command evidence operator path as `8c4f4eea533b01ada05307ef5e9fd4c5df304878`. Final PR-head gates on `be358f47de944e9d9318b8244fb732fb8b3202e1` passed push CI `33885085546`, PR CI `33885158191`, Historical Backfill Smoke `33885158365`, Live Recorder Smoke `33885158067`, and Recorder Short Soak `33885158081`; post-merge CI `33885404731` then passed 927 tests plus Ruff, deployment validation, health, and dashboard checks.

Run the evidence wrapper from a clean local checkout whose `HEAD` exactly equals `PHASE14_PARTITIONED_STORAGE_HEAD`. It captures the read-only preflight transcript in Cloud Shell and independently verifies it; the wrapper fails closed on a local candidate-head mismatch or dirty working tree. The underlying preflight now also reads `POSTGRES_USER` and `POSTGRES_DB` from the production environment with the existing `bp` defaults and clamps unknown PostgreSQL `reltuples` estimates to zero before emitting verifier input. Full operator steps are in `docs/PHASE-14-STORAGE-RECOVERY.md`.

The verifier still requires exact deployed/candidate SHA agreement, `RECORDER_STATE=stopped`, `MUTATIONS_PERFORMED=false`, canonical 24–48h recovery evidence, unequivocally unmigrated legacy raw storage, and sufficient migration headroom. Required free space is the larger of the configured floor and the current `raw_market_events` total relation size plus the unchanged 15 GiB critical reserve. Conflicting duplicate transcript fields fail closed.

A verified preflight PASS is prerequisite evidence only. The production partition migration remains a **separate explicit production authorization**. PR #54 merged the rollout-preflight parity hardening to `main` as `4e4ef004ba805438d8a7e68aa365d92754e57f4a`. The rollout now requires the verified preflight JSON itself, binds it to the exact deployed/candidate SHAs, records its SHA-256 in rollout evidence, and independently recomputes the dynamic migration-headroom rule before mutation and again immediately before migration apply. Final branch head `0601cf95d43bd949a7087645a3c4879d158e333d` passed push CI `33888063457`, PR CI `33888096498`, Historical Backfill Smoke `33888096374`, Live Recorder Smoke `33888096668`, and Recorder Short Soak `33888096363`; post-merge CI `33888318645` then passed 929 tests plus Ruff, deployment validation, health, and dashboard checks.

PR #56 merged the rollout live-schema-shape recheck to `main` as `fe025bc0ff204545fd4138aff9dd9c957572c248`. The production helper now independently re-queries the schema immediately before mutation and again after managed services are stopped; it fails closed if `raw_market_events` is already partitioned, `raw_market_events_legacy` already exists, or `raw_event_dedupe` already exists, preventing stale preflight evidence from authorizing a changed/partial schema. Final branch head `d56588ae0073eed5c350df9716a36f3a55a9a2df` passed push CI `33894032124`, PR CI `33894191483`, Historical Backfill Smoke `33894191580`, Live Recorder Smoke `33894191513`, and Recorder Short Soak `33894191530`; post-merge CI `33894382583` then passed 930 tests plus rollout syntax, health, and dashboard checks. This remains engineering evidence only and does not authorize production execution.

PR #57 merged machine enforcement for the separate production-migration approval gate to `main` as `987ee4d3029bc16977aebcda0e923bdc5d60ad0b`. The rollout requires explicit approved-from and approved-candidate SHA inputs that exactly equal the rollout transition; missing or mismatched approval fails before any production VM contact. Final branch head `ac1e94959f836bf019249a5e055e23685e339831` passed push CI `33895334052`, PR CI `33895337880`, Historical Backfill Smoke `33895337719`, Live Recorder Smoke `33895337810`, and Recorder Short Soak `33895337889`; post-merge CI `33895591223` then passed 931 tests plus rollout syntax, health, and dashboard checks. No approval values were set and production approval remains false.

PR #58 merged the physical-release acceptance proof to `main` as `c111ebdd29a584a05a46bfeb8d505b04f4558680`. After exact migration parity, the rollout must retire at least one non-empty verified raw partition, prove dedupe cleanup matches the archived row count, and show that total attached raw-partition relation bytes decrease across the maintenance cycle. Final branch head `604df58f48538a46518775053a5a638d54539122` passed push CI `33896515382`, PR CI `33896529731`, Historical Backfill Smoke `33896529562`, Live Recorder Smoke `33896529796`, and Recorder Short Soak `33896529803`; post-merge CI `33896713148` then passed 932 tests plus rollout syntax, health, and dashboard checks. This remains engineering evidence only; no production migration or approval occurred.

PR #59 merged the rollout local-candidate binding to `main` as `9ec294cc5f3aec29ece1c3628544d53a908c3b4b`. The Cloud Shell helper now resolves the local repository root, requires local `HEAD == PHASE14_PARTITIONED_STORAGE_HEAD`, and rejects any tracked or untracked working-tree change before any gcloud interaction. This prevents an older or locally modified rollout launcher from targeting a newer candidate whose safety gates differ. Final branch head `42549a77cdfcc6a9fcedd2ae2302ff24c525a299` passed push CI `33897575270`, PR CI `33897620395`, Historical Backfill Smoke `33897620428`, Live Recorder Smoke `33897620391`, and Recorder Short Soak `33897620444`; post-merge CI `33897830149` then passed 933 tests plus rollout syntax, health, and dashboard checks. No production execution or approval occurred.

PR #60 merged the rollout archive-evidence binding to `main` as `b4582d1c92026257c7c5e5b84fd87d1131432aa8`. The launcher extracts the verified preflight archive evidence name and `window_end`, passes both into the detached worker, and the worker requires that exact canonical recovery file and matching window instead of selecting the newest matching JSON. Final branch head `e37f58824afdd7de52a4373a77730056b4aa047c` passed push CI `33898669119`, PR CI `33898705601`, Historical Backfill Smoke `33898705613`, Live Recorder Smoke `33898705636`, and Recorder Short Soak `33898705599`; post-merge CI `33898942009` then passed 934 tests plus rollout syntax, health, and dashboard checks. No production execution or approval occurred.

When the separate migration-authorization gate is eventually satisfied, the exact-SHA rollout helper must still retain rollback material, prove exact migration parity, run one verified archive-to-partition-drop cycle, prove physical relation-size release, and end with `RECORDER_RESTARTED=false`.

After storage migration/health acceptance, the next operational step is the already-merged recorder reliability repair with `RECORDER_WRITER_WORKERS=4`, followed by all-four-feed and natural-load no-drop/backpressure acceptance. Recorder restart remains separate from schema migration.

Existing V1 evidence remains a separate immutable epoch. Selected-book freshness remains exactly 10 seconds. `MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, `MAX_DAILY_LOSS_USD=0`, and `automatic_promotion=false` remain mandatory. Gate B remains unauthorized, the Master live gate remains `fail`, geographic/compliance restrictions must not be bypassed, and Phase 15 remains blocked.
