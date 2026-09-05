# BTC Polymarket Prediction Engine — Master Source of Truth

**Status:** Active
**Version:** 0.1.0
**Frozen on:** 20 August 2026
**Authority:** This file is the canonical source of truth for the project.

---

## 0. How to use this document

This file is the final authority when a future chat, developer, model, note, or old message disagrees with the project.

Rules:

1. Read this file before changing architecture, scope, metrics, trading logic, data sources, model targets, or phase gates.
2. Do not silently overwrite a decision in this file.
3. If a decision changes, update:
   - this file,
   - `docs/DECISION-LOG.md`,
   - `docs/CHANGELOG.md`,
   - `PROJECT_STATE.json`.
4. The desired 80% accuracy is a research goal, **not an assumed capability**.
5. No real-money trading is allowed until the live-trading gate in this document is passed.
6. Never paste a wallet private key, seed phrase, API secret, or server secret into ChatGPT or a source file.
7. Market rules and APIs can change. Re-verify current Polymarket rules and API behavior before live trading.

---

# 1. Project mission

Build an end-to-end system that estimates whether Bitcoin will resolve **Up or Down** in short-duration Polymarket BTC prediction markets, initially focused on 5-minute and 15-minute markets, with 10-minute support if/when such a market is available.

The long-term system should:

1. collect BTC and Polymarket market data continuously;
2. build a clean historical training dataset;
3. train separate predictive models by horizon;
4. produce calibrated Up/Down probabilities;
5. compare the model probability with the tradable Polymarket price;
6. paper-trade first;
7. measure real out-of-sample performance;
8. trade automatically only after strict validation and risk gates are passed.

The end goal is **profitable, well-measured decision-making**, not merely a high headline accuracy number.

---

# 2. Current market definition

## 2.1 Verified Polymarket horizons

As of the source-of-truth freeze date:

- **5-minute BTC Up/Down markets:** verified.
- **15-minute BTC Up/Down markets:** verified.
- **10-minute BTC Up/Down markets:** desired by the project, but not verified as a current recurring Polymarket market.

Therefore, horizon support must be **configurable**, not hard-coded.

Initial configuration:

```yaml
active_horizons:
  - 5m
  - 15m

optional_horizons:
  - 10m
```

If Polymarket offers other recurring BTC Up/Down durations later, they may be added without redesigning the engine.

## 2.2 Verified resolution rule

Current verified BTC 5m/15m Polymarket examples resolve using the **Chainlink BTC/USD data stream**.

Current rule:

- **Up** wins if the BTC price at the end of the stated interval is **greater than or equal to** the BTC price at the beginning.
- Otherwise, **Down** wins.

This means the prediction target is **not simply “will Binance BTC go up?”**.

The target is:

> What is the probability that the relevant Polymarket BTC Up/Down market resolves Up according to its official resolution rule?

The market's own rule is always authoritative. If Polymarket changes the resolution source or wording, the system must detect/review the change before trading.

---

# 3. Core principle

The engine must distinguish between:

1. **Direction probability** — our estimate that Up or Down will win.
2. **Market price** — the cost of buying that outcome on Polymarket.
3. **Expected value** — whether the model's estimated probability is sufficiently better than the executable market price after fees, spread, slippage, uncertainty, and execution risk.

Example:

- model says Up = 80%;
- executable Up ask = $0.60;
- this may contain positive expected value.

But:

- model says Up = 80%;
- executable Up ask = $0.90;
- this can be a bad trade despite a strong directional prediction.

Therefore:

**Accuracy alone does not decide trades.**

---

# 4. Success criteria

## 4.1 Research target

The user would like the system to reach approximately **80% prediction accuracy**.

This is a target to investigate, not a promise.

We must report:

- accuracy across all predictions;
- accuracy by horizon;
- accuracy by confidence bucket;
- coverage (% of observations where a trade-quality signal exists);
- calibration;
- profitability;
- expected value;
- realised P&L;
- maximum drawdown;
- losing streaks;
- performance by market regime.

A useful outcome may be:

- lower overall accuracy;
- but 75–80%+ accuracy on a smaller, genuinely high-confidence subset;
- with positive net expected value and live paper-trading profitability.

## 4.2 Minimum proof standard

A model is **not considered proven** because it reaches 80% in one backtest.

It must survive:

1. data-quality validation;
2. leakage checks;
3. time-ordered holdout testing;
4. walk-forward testing;
5. multiple market regimes;
6. realistic execution simulation;
7. live paper trading with predictions timestamped before outcomes are known.

## 4.3 Live-trading gate

Real-money automation remains disabled until all of the following are true:

- historical pipeline is reproducible;
- no known target leakage;
- backtests use time-ordered splits;
- walk-forward results are stable enough to justify continuation;
- live paper predictions have a sufficiently large sample;
- profitability remains positive after realistic costs;
- confidence is calibrated;
- risk limits and kill switch are tested;
- order execution and reconciliation are tested;
- current Polymarket geographic eligibility/compliance is checked;
- the user explicitly authorizes the transition to real-money trading.

There is no fixed “magic” sample count in this version of the spec. The analysis must include uncertainty/confidence intervals rather than relying on a round number alone.

---

# 5. System architecture

The system is split into independent components.

```text
                 ┌───────────────────────┐
                 │  BTC Market Sources   │
                 │ spot / perp / trades  │
                 └──────────┬────────────┘
                            │
                            v
┌───────────────────┐   ┌──────────────────────┐
│ Polymarket Gamma  │-->|                      |
│ market discovery  │   │   Data Collectors    |
└───────────────────┘   │                      |
                        └──────────┬───────────┘
┌───────────────────┐              │
│ Polymarket CLOB   │--------------┘
│ price/order book  │
└───────────────────┘
                            v
                    ┌─────────────────┐
                    │ Raw Data Store  │
                    └────────┬────────┘
                             v
                    ┌─────────────────┐
                    │ Feature Engine  │
                    └────────┬────────┘
                             v
                    ┌─────────────────┐
                    │ Label Engine    │
                    └────────┬────────┘
                             v
                    ┌─────────────────┐
                    │ Training Store  │
                    └────────┬────────┘
                             v
              ┌─────────────────────────────┐
              │ Models by horizon           │
              │ baseline + boosted trees    │
              └──────────────┬──────────────┘
                             v
                    ┌─────────────────┐
                    │ Probability +   │
                    │ Edge Engine     │
                    └────────┬────────┘
                             v
              ┌─────────────────────────────┐
              │ Paper / Live Execution      │
              │ risk + order management     │
              └──────────────┬──────────────┘
                             v
                    ┌─────────────────┐
                    │ Audit + Metrics │
                    └────────┬────────┘
                             v
                    ┌─────────────────┐
                    │ Dashboard       │
                    └─────────────────┘
```

Each layer should be replaceable without rewriting the whole system.

---

# 6. Initial technology stack

## 6.1 Backend

- Python 3.12+
- `asyncio`
- WebSocket + HTTP clients
- Pydantic for schemas/configuration
- SQLAlchemy or psycopg for PostgreSQL
- pandas/polars for research and dataset work
- scikit-learn
- LightGBM and/or XGBoost for first serious models

Do not begin with an LLM as the prediction model.

## 6.2 Database

Initial zero-cost target:

- PostgreSQL running on the selected always-on compute instance.

The database design must support:

- retention policies;
- aggregation;
- compression/partitioning where useful;
- reproducible feature generation.

Do not store every high-frequency raw update forever without a retention strategy.

## 6.3 Dashboard

- Next.js
- TypeScript
- simple responsive UI
- Vercel free tier or equivalent for the frontend if suitable at build time

## 6.4 Compute

Initial objective: **$0/month while validating the idea**.

Preferred first option at freeze date:

- Oracle Cloud Infrastructure Always Free Ampere A1, if capacity/account creation is available.

Current Oracle documentation describes Always Free A1 allocation equivalent to up to 2 OCPUs and 12 GB memory for Always Free tenancies.

This is an **initial infrastructure choice, not a permanent dependency**.

Fallback options, in order:

1. another genuinely free always-on compute option available at build time;
2. a local computer that can stay online during early collection/testing;
3. paid low-cost VPS only after the user explicitly chooses to spend money.

Architecture must remain portable via Docker so infrastructure can be changed.

---

# 7. Data sources

## 7.1 Polymarket

Use official Polymarket interfaces wherever possible.

### Market discovery / metadata

Use Gamma API to discover relevant events/markets and obtain fields such as:

- market/event ID;
- slug;
- title;
- active/closed state;
- start/end timestamps;
- outcome token IDs;
- resolution metadata;
- liquidity/volume where available.

### Live market data

Use the CLOB public market-data endpoints/WebSocket for:

- order-book snapshots and updates;
- best bid/ask;
- spread;
- last trade price;
- token prices;
- market lifecycle changes.

The official Market WebSocket currently exposes real-time order-book, price, and market lifecycle updates.

### Historical market prices

Polymarket's `/prices-history` endpoint provides historical token price data and supports 1-minute fidelity.

This is useful for backfilling but does not replace our own full live recorder.

### Trading

When/if live trading is authorized, use the official Polymarket SDK/API.

Current official documentation supports authenticated order placement/cancellation and describes the CLOB as offchain matching with onchain settlement.

Before sending any order, the system must perform the current geographic eligibility check and respect current Polymarket restrictions.

## 7.2 Bitcoin exchange data

The system should not depend on only one exchange.

Initial plan:

- primary liquid BTC spot/perpetual feed;
- secondary exchange feed for confirmation and cross-market features.

Candidate sources include Bybit, Binance, and Coinbase, subject to API availability and legal/service access at implementation time.

Desired BTC features include:

- last price;
- best bid/ask;
- spread;
- order-book depth/imbalance;
- individual/trade aggregates;
- aggressive buy/sell flow;
- volume;
- short-term returns;
- realised volatility;
- perpetual basis;
- funding;
- open interest;
- liquidations;
- cross-exchange price differences.

Not every feature needs to exist in V1. Data quality is more important than feature count.

## 7.3 Chainlink

Because Polymarket's current BTC Up/Down examples resolve using Chainlink BTC/USD, Chainlink reference data/rules must be treated as resolution-critical.

The recorder should save enough information to reproduce:

- market start reference;
- market end reference;
- final resolved outcome.

If direct historical Chainlink data access is insufficient or changes, Polymarket's official resolved outcome remains the authoritative label for the market.

---

# 8. Data model

Exact schema may evolve, but these logical entities are required.

## 8.1 `polymarket_markets`

Minimum fields:

- internal id
- polymarket event id
- polymarket market/condition id
- slug
- title
- horizon seconds
- start timestamp UTC
- end timestamp UTC
- Up token id
- Down token id
- resolution source
- rules text/hash
- active/closed/resolved status
- resolved outcome
- discovered timestamp
- updated timestamp

## 8.2 `polymarket_book_snapshots`

Minimum fields:

- timestamp UTC
- market id
- token id
- best bid
- best ask
- midpoint
- spread
- bid depth by configured bands
- ask depth by configured bands
- book imbalance
- last trade price
- raw snapshot reference/hash if stored

Do not necessarily persist every individual book level forever. Retention/aggregation policy is required.

## 8.3 `btc_market_snapshots`

Minimum fields:

- exchange
- instrument
- timestamp UTC
- spot/perp classification
- last price
- best bid
- best ask
- spread
- trade-flow aggregates
- volume aggregates
- order-book features
- open interest if available
- funding if available
- liquidation aggregates if available

## 8.4 `features`

One row per prediction timestamp + market/horizon.

Contains only information that was knowable at that timestamp.

Must include:

- feature timestamp;
- market id;
- time remaining;
- distance from opening/reference price;
- current Polymarket executable prices;
- BTC microstructure features;
- derivatives features;
- volatility/regime features;
- missing-data flags;
- feature version.

## 8.5 `labels`

- market id
- horizon
- official resolved outcome
- start reference
- end reference where available
- price change
- label generated timestamp
- label source
- label version

Labels must never leak into features.

## 8.6 `predictions`

Every prediction is immutable.

Required:

- prediction id
- generated timestamp
- market id
- model id/version
- horizon
- probability Up
- probability Down
- confidence bucket
- predicted side
- Polymarket bid/ask at prediction time
- estimated edge
- action: `NO_TRADE`, `BUY_UP`, `BUY_DOWN`
- reason codes
- feature version
- later: official result
- later: correct/incorrect
- later: simulated/live P&L

Predictions cannot be rewritten after the result.

## 8.7 `orders` and `fills`

Required before live trading.

Track:

- signal/prediction id;
- requested order;
- signed/submitted timestamp;
- side/token;
- limit price;
- requested size;
- status;
- fills;
- average fill;
- fees;
- cancel reason;
- exchange/Polymarket IDs;
- reconciliation status.

---

# 9. Time and data integrity

Short-horizon prediction is extremely sensitive to timestamp errors.

Mandatory rules:

- Store canonical timestamps in UTC.
- Sync server clock using NTP/chrony.
- Preserve source timestamps where supplied.
- Record local receive timestamp separately.
- Detect stale feeds.
- Detect WebSocket gaps/reconnects.
- Never forward-fill critical data across long gaps without a missing-data flag.
- Use idempotent writes for reconnect/replay behavior.
- Store schema and feature versions.
- Make raw-to-feature transformation reproducible.

---

# 10. Training problem

## 10.1 Separate horizon models

Initial model families are trained separately by horizon.

At minimum:

- 5m model
- 15m model
- 10m model only if a corresponding market exists or if used as a research-only target

Do not assume the same features/parameters are optimal for each horizon.

## 10.2 Training example

Conceptually:

```text
What was knowable at prediction time?
    +
Current Polymarket state
    +
Time remaining / distance from opening price
    ->
Official market result: Up or Down
```

The system learns statistical relationships between pre-resolution conditions and outcomes.

## 10.3 Initial model ladder

Build in this order:

1. naive baseline;
2. logistic regression baseline;
3. LightGBM/XGBoost;
4. calibrated boosted-tree ensemble if warranted;
5. sequence/deep models only if simpler models plateau and evidence justifies added complexity.

Every complex model must beat simpler baselines out-of-sample.

---

# 11. Feature groups

Candidate feature families:

## BTC price / momentum

- 5s, 15s, 30s, 1m, 3m, 5m returns;
- acceleration;
- distance from local highs/lows;
- short-term trend consistency.

## Order flow

- aggressive buy/sell volume;
- trade count imbalance;
- CVD-style aggregates;
- trade velocity;
- large-trade counts.

## Order book

- top-of-book spread;
- microprice;
- depth imbalance;
- changes in imbalance;
- depth at multiple bands;
- liquidity removal/addition rates where reliably reconstructable.

## Derivatives

- perp vs spot basis;
- open-interest change;
- funding;
- liquidation imbalance;
- price/OI divergence.

## Cross-market

- BTC across multiple venues;
- ETH/BTC or ETH/SOL context if proven useful;
- cross-exchange lead/lag.

## Polymarket state

- executable Up bid/ask;
- executable Down bid/ask;
- spread;
- depth;
- last trade;
- market-implied probability;
- probability movement;
- time remaining;
- opening/reference distance;
- book imbalance.

## Regime/time

- realised volatility;
- volatility percentile;
- time-of-day;
- weekday;
- market-session proxies;
- feed-quality flags.

Features are candidates, not assumptions. Keep only those that survive validation.

---

# 12. Avoiding false accuracy

Forbidden evaluation shortcuts:

- random train/test shuffle of overlapping time-series rows;
- using future prices in feature calculations;
- using final market result before prediction time;
- selecting thresholds using the final test set;
- repeatedly tuning on the same “unseen” test data;
- reporting only the best day/week;
- ignoring spread, slippage, rejected orders, or unfilled trades;
- treating midpoint as the executable entry price;
- changing old predictions after the fact.

Use:

- chronological splits;
- purging/embargo where labels/features overlap;
- walk-forward validation;
- untouched final holdout;
- frozen model versions for live paper tests.

---

# 13. Probability calibration

A model saying “80%” should win close to 80% of the time across a large set of similar predictions.

Track calibration buckets such as:

- 50–55%;
- 55–60%;
- 60–65%;
- 65–70%;
- 70–75%;
- 75–80%;
- 80–85%;
- 85–90%;
- 90%+.

Do not call a model high-confidence merely because its raw classifier score is high.

Use proper calibration methods on validation data when justified.

---

# 14. Opportunity / edge engine

The model outputs probabilities. The edge engine decides whether a trade is worth considering.

Conceptual long-side gross edge:

```text
estimated_edge = model_probability - executable_ask_probability
```

But real trade logic must also include:

- spread;
- fees;
- slippage;
- order-book depth;
- uncertainty/calibration penalty;
- latency/staleness;
- time remaining;
- minimum expected-value threshold;
- risk limits.

The default action is **NO_TRADE** unless a configured edge threshold is passed.

---

# 15. Paper trading

Paper trading must use the market state that existed at decision time.

It should model:

- executable ask/bid rather than displayed midpoint;
- available depth;
- partial/non-fills;
- latency assumptions;
- fees;
- slippage;
- cancellation;
- market expiry.

Each paper trade is linked to an immutable prediction.

Dashboard must clearly distinguish:

- prediction accuracy;
- paper-trading results;
- live-trading results.

---

# 16. Risk engine

Before real-money trading, implement:

- global trading enable/disable switch;
- kill switch;
- max stake per trade;
- max total exposure;
- max daily loss;
- max consecutive-loss response;
- confidence/edge minimum;
- liquidity minimum;
- spread maximum;
- stale-data blocker;
- API health blocker;
- time-to-expiry constraints;
- duplicate-order protection;
- order reconciliation;
- configurable cooldown.

Position sizing should begin conservatively. No martingale.

---

# 17. Security

Mandatory:

- secrets only in environment/secret manager;
- no secrets committed to Git;
- no private keys in prompts/chat/files;
- dedicated trading wallet with limited funds;
- separate research and live environments;
- least-privilege credentials;
- encrypted server access;
- SSH keys rather than password login where possible;
- firewall;
- dependency pinning;
- audit logs;
- backups for critical database state.

For live trading, the signing path deserves its own security review.

---

# 18. Dashboard — minimum useful screens

## Overview

- current BTC reference price(s);
- active Polymarket BTC markets;
- Up/Down model probabilities;
- executable Polymarket prices;
- estimated edge;
- action;
- data/feed health.

## Performance

By horizon:

- total predictions;
- accuracy;
- confidence-bucket accuracy;
- calibration;
- coverage;
- simulated/live P&L;
- win rate;
- average entry price;
- average expected value;
- drawdown;
- losing streak.

## Predictions

Immutable list of:

- timestamp;
- market;
- probability;
- side;
- market price;
- decision;
- later outcome;
- P&L.

## System health

- collectors connected;
- latest timestamps;
- data gaps;
- database health;
- model version;
- server uptime;
- trading mode: OFF / PAPER / LIVE.

---

# 19. Observability and auditability

Every major process must emit structured logs.

Track:

- feed connections/reconnections;
- rate-limit events;
- dropped/stale messages;
- prediction generation;
- model/version loaded;
- order attempts;
- order responses;
- fills/cancels;
- reconciliation discrepancies;
- data-quality alerts.

The system must be able to answer:

> Exactly what information did the model have when it made this prediction?

---

# 20. Retraining

The system does **not** blindly retrain itself after each trade.

Retraining is controlled.

A candidate new model must:

1. be trained on an approved dataset snapshot;
2. pass automated data/leakage tests;
3. pass walk-forward evaluation;
4. be compared with the current champion model;
5. be versioned;
6. be promoted deliberately.

Live prediction/trading history becomes additional future training data, but only after validation.

---

# 21. Deployment modes

Three explicit modes:

```text
RESEARCH
PAPER
LIVE
```

## RESEARCH

- collect/backfill/train/backtest;
- cannot place real orders.

## PAPER

- real live feeds;
- real predictions;
- simulated execution;
- cannot place real orders.

## LIVE

- real order placement allowed;
- only after gate approval;
- still subject to risk engine.

A production build must make it difficult to accidentally switch from PAPER to LIVE.

---

# 22. Cost policy

Initial validation objective: **$0/month** wherever practical.

But zero cost must not be allowed to corrupt research quality.

If free infrastructure causes:

- persistent data gaps;
- clock/reliability failures;
- insufficient storage;
- recurrent downtime;
- inability to run necessary tests;

the project should document the limitation and recommend the smallest paid upgrade rather than silently accepting bad data.

No paid service is introduced without user approval.

---

# 23. Phase gates

## Phase 0 — Repository + source-of-truth freeze

Done when:

- repository exists;
- this source pack is committed;
- configuration strategy exists;
- tests/linting baseline exists;
- secrets policy exists.

## Phase 1 — Polymarket market discovery

Done when:

- live BTC Up/Down markets are discovered automatically;
- durations are parsed/configurable;
- token IDs and official rules are saved;
- 5m/15m examples are handled;
- unit tests cover market parsing.

## Phase 2 — 24/7 raw recorder

Done when:

- BTC feeds record continuously;
- Polymarket market WebSocket records continuously;
- reconnects and gaps are handled;
- timestamps/data health are monitored;
- a 24-hour soak test passes without unexplained gaps.

## Phase 3 — Historical backfill

Done when:

- available Polymarket historical prices/markets are downloaded;
- BTC historical data is downloaded;
- sources/timestamps are normalized;
- limitations of historical order-book coverage are documented.

## Phase 4 — Feature + label engine

Done when:

- deterministic feature generation exists;
- official outcomes are attached only after resolution;
- feature/label versioning exists;
- leakage tests pass.

## Phase 5 — Baseline models

Done when:

- naive/logistic baseline exists;
- boosted-tree model exists;
- time-ordered evaluation exists;
- calibration metrics exist;
- results are reproducible from a dataset snapshot.

## Phase 6 — Walk-forward backtester

Done when:

- purged/embargoed walk-forward evaluation exists where required;
- execution costs are modeled;
- results by regime/horizon/confidence are reported;
- untouched holdout remains untouched until designated evaluation.

## Phase 7 — Live prediction engine

Done when:

- predictions are generated before outcomes;
- predictions are immutable;
- outcomes are reconciled automatically;
- live accuracy/calibration dashboard works.

## Phase 8 — Paper trading

Done when:

- executable prices/depth are used;
- simulated orders/fills are modeled;
- P&L is recorded;
- risk rules run exactly as they would in live mode.

## Phase 9 — Model improvement

Done when:

- champion/challenger process exists;
- feature/model experiments are tracked;
- gains survive walk-forward and live paper data.

## Phase 10 — Live-trading readiness review

Done only when:

- all live-trading gate items in Section 4.3 pass;
- security review passes;
- geoblock/compliance check passes;
- trading wallet setup is secure;
- user explicitly approves live activation.

## Phase 11 — Controlled live trading

Start with deliberately small limits.

No automatic scale-up.

Scale only after real fills and live P&L behave close to paper expectations.

---

# 24. Testing strategy

Required test categories:

- unit tests;
- schema validation tests;
- API parser fixtures;
- reconnect/replay tests;
- data-gap tests;
- timestamp tests;
- feature determinism tests;
- leakage tests;
- model reproducibility tests;
- backtest execution tests;
- risk-engine tests;
- paper/live mode safety tests;
- order idempotency/reconciliation tests.

Any bug capable of changing historical labels, features, or P&L requires re-running affected evaluations.

---

# 25. Repository target structure

```text
btc-polymarket-engine/
├── AGENTS.md
├── README.md
├── PROJECT_STATE.json
├── .env.example
├── .gitignore
├── docker-compose.yml
├── pyproject.toml
├── apps/
│   └── dashboard/
├── src/
│   ├── config/
│   ├── collectors/
│   │   ├── btc/
│   │   └── polymarket/
│   ├── storage/
│   ├── features/
│   ├── labels/
│   ├── models/
│   ├── backtest/
│   ├── signals/
│   ├── execution/
│   ├── risk/
│   └── monitoring/
├── scripts/
│   ├── backfill/
│   ├── train/
│   └── maintenance/
├── tests/
├── migrations/
├── data/
│   └── .gitkeep
└── docs/
    ├── MASTER-SOURCE-OF-TRUTH.md
    ├── BUILD-ORDER.md
    ├── DECISION-LOG.md
    └── CHANGELOG.md
```

Large datasets must not be committed to Git.

---

# 26. Required configuration

At minimum:

```yaml
mode: research

horizons:
  active: [5m, 15m]
  optional: [10m]

prediction:
  min_probability: null
  min_edge: null

risk:
  live_trading_enabled: false
  max_trade_size_usd: 0
  max_daily_loss_usd: 0

data:
  timezone: UTC
  polymarket_enabled: true
  btc_primary_exchange: TBD
  btc_secondary_exchange: TBD
```

Values marked TBD are deliberately unresolved until implementation/benchmarking.

---

# 27. Open research questions

These must be answered with evidence rather than guesses:

1. How much historical short-duration Polymarket BTC market data can be backfilled reliably?
2. How much full historical order-book depth can be obtained versus only price history?
3. Which BTC exchange(s) provide the best free real-time feed quality for our location/server region?
4. Is a recurring 10m BTC Up/Down Polymarket market available at implementation time?
5. What prediction timestamp(s) within each market window create the best tradeable edge?
6. Does the model need one prediction per market, rolling predictions throughout the window, or both?
7. Which feature groups add stable out-of-sample value?
8. What confidence/edge threshold maximizes risk-adjusted return?
9. How much latency matters for these horizons?
10. What is the realistic fill/slippage profile of the target Polymarket markets?
11. Is 80% achievable on all signals, only a high-confidence subset, or neither?
12. What infrastructure is required once the private data history grows?

Do not answer these by intuition in the codebase. Test them.

---

# 28. Explicit non-goals for early phases

Do not spend early development time on:

- fancy dashboard animations;
- an LLM-based trading brain;
- dozens of exchanges;
- reinforcement learning;
- automatic self-modifying models;
- large neural networks before baselines;
- live capital;
- complex portfolio allocation;
- mobile apps.

First prove the data and predictive edge.

---

# 29. External references checked for v0.1.0

These references are not the source of truth for our internal decisions, but they support current external facts and must be rechecked when needed.

Polymarket:

- Market data overview: https://docs.polymarket.com/market-data/overview
- Historical prices: https://docs.polymarket.com/api-reference/markets/get-prices-history
- CLOB order book: https://docs.polymarket.com/api-reference/market-data/get-order-book
- Market WebSocket: https://docs.polymarket.com/api-reference/wss/market
- Trading overview: https://docs.polymarket.com/trading/overview
- Trading quickstart: https://docs.polymarket.com/trading/quickstart
- Geographic restrictions: https://docs.polymarket.com/api-reference/geoblock
- Rate limits: https://docs.polymarket.com/api-reference/rate-limits
- Verified 5m example: https://polymarket.com/event/btc-updown-5m-1785178800
- Verified 15m example: https://polymarket.com/event/btc-updown-15m-1785508200

Oracle:

- Always Free resources: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
- Ampere A1: https://docs.oracle.com/en-us/iaas/Content/Compute/References/arm.htm

Bybit documentation can be rechecked at:
- https://bybit-exchange.github.io/docs/

---

# 30. Current project state

As of **2 September 2026**:

- Phases 0 through 14 in the active repository build order have been implemented and closed through Phase 14 engineering readiness;
- the current machine-readable state is `PHASE_14_ENGINEERING_COMPLETE_LIVE_GATE_BLOCKED`;
- live prospective predictions and money-disabled paper execution are running from immutable evidence ledgers;
- the Phase 14 Master live gate remains `fail`;
- walk-forward stability, sufficiently large prospective paper evidence, and prospective calibration remain insufficient;
- positive after-cost profitability has not been established;
- geographic/compliance eligibility is not established for live launch;
- live trading remains disabled and real-money trade-size/daily-loss limits remain zero;
- Phase 14 Gate A timestamp-coherent V2 is production-accepted from forward epoch `2026-09-02T12:18:02Z`; the continuous V2 forward-coverage collector was subsequently deployed research-only/outcome-blind on production head `c29fe227f959305f67031e922ca659869a826c4f`, but its runtime state is not asserted while the 4 September storage recovery remains in progress;
- Phase 15 controlled live launch is not permitted.

`PROJECT_STATE.json` is the machine-readable record of the exact current phase, accepted evidence, and next actions.

---

# 31. Phase 14 prospective-evidence follow-up

The approved post-closeout evidence workflow is a separate **read-only reporter** over existing immutable paper settlements, prediction evaluations, and reconciliation evidence. It must not modify the paper worker, predictions, evaluations, execution ledgers, research records, or live-readiness records.

The reporter must surface, at minimum:

- settled prospective paper-trade sample size;
- prospective prediction-evaluation sample size;
- realized after-cost paper P&L;
- uncertainty/confidence interval for realized expectancy;
- raw and calibrated Brier/log-loss evidence;
- paper execution reconciliation status;
- the current Master live-gate snapshot.

Evidence gates emitted by this reporter are limited to `pass`, `fail`, or `insufficient_evidence`. The reporter must not invent a fixed minimum sample count because Section 4.3 explicitly requires uncertainty rather than a magic number. It must also not invent a numerical prospective-calibration acceptance threshold until such a threshold is deliberately approved and recorded.

This workflow cannot promote a model or activate live trading automatically. `automatic_promotion` remains false. It may run only while `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`. Any Phase 15 transition still requires the complete Master live gate to pass and separate explicit real-money authorization.

---

# 32. Phase 14 prospective outcome/evaluation sync follow-up

The 31 August 2026 prospective-evidence host report established that the reporting path itself was healthy while the prospective evidence sample remained empty: zero live prediction evaluations and zero paper settlements were present. Root-cause tracing showed that prospective paper settlement depends on an immutable live-prediction evaluation, that evaluation depends on the canonical `official-outcome-v1` label, and that label depends on a preserved resolved Polymarket Gamma snapshot. The production runtime did not have an always-on post-resolution snapshot-ingestion path for newly completed prospective predictions.

The approved follow-up is a separate **money-disabled prospective outcome sync** that closes only that evidence-ingestion gap. For ended immutable predictions that still lack evaluation, it may fetch the exact market by slug from official Polymarket Gamma. Missing or unresolved markets remain pending and produce no write. Before any resolved snapshot is stored, the returned condition ID, slug, horizon, market start/end timestamps, and Up/Down token IDs must match the immutable prediction exactly; any mismatch fails closed before persistence.

Resolved evidence must reuse the existing canonical chain rather than introduce a parallel outcome source:

1. store the official Gamma payload through the existing immutable historical market-snapshot repository and provenance contract;
2. run the existing `official-outcome-v1` canonical label generator under D-017;
3. append the existing immutable live-prediction evaluation;
4. allow the existing paper-execution worker to create any eligible paper settlement from that evaluation on its normal or explicitly bounded paper cycle.

The outcome sync must not rewrite predictions, labels, evaluations, paper orders/fills/settlements, historical snapshots, research records, or live-readiness evidence. Completed evaluations are idempotent and must not trigger repeated Gamma fetching. Historical snapshot digests may carry the established `sha256:` prefix; the evaluation boundary may normalize only that optional prefix while still requiring an exact 64-character lowercase hexadecimal digest. No hash tolerance or weakening is allowed.

The runtime is permitted only in `RESEARCH` with `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`. It exposes no wallet, signing, real-order, promotion, or live-enable path. Its exact-head host acceptance is deliberately non-deploying: it runs the candidate from a detached worktree, requires the existing paper worker to be active, records the existing live-predictor service state without requiring it to be active, requires that predictor state to be unchanged after acceptance, may append only canonical official-outcome/evaluation evidence and derivative paper settlements, verifies `/opt/bp` is unchanged, and performs no package installation, migration, service start/stop/restart, or daemon installation.

Host acceptance must also prove that this follow-up path was genuinely exercised rather than passing on an idle no-op. The bounded acceptance cycle therefore requires at least one ended unevaluated candidate, at least one resolved official market, complete pending/resolved candidate accounting, one snapshot-store result per resolved candidate, canonical label evidence covering the resolved candidates, and a newly appended immutable evaluation for every resolved candidate. These are acceptance-path exercise requirements only; they are not a minimum prospective paper sample, profitability threshold, calibration threshold, or live-trading promotion criterion.

On 31 August 2026, the first production-host acceptance attempt on candidate `c11000bf97bcfe93b91d17134c43bbd10a5791ef` failed closed before outcome processing with `REASON=predictor_service_not_active_before`. Investigation confirmed that Phase 10 host acceptance had created `bp-live-predictor.service` only as a temporary runtime unit under `/run/systemd/system` and removed it during cleanup; the canonical project record never established a permanent predictor installation. The failed attempt remains valid operational evidence. TDD corrected only this acceptance precondition: RED `040fc2b6a322abb58b1aa9e27025ad687b5502c5` produced exactly one intended deployment-contract failure with 784 existing tests passing, and GREEN `8d04a38c366370835a1c530c4aa542ed8521a3b2` passed all 785 tests plus deployment validation, health, dashboard checks, Historical Backfill Smoke #486, Live Recorder Smoke #593, and Recorder Short Soak #558.

On 31 August 2026, corrected exact-head production-host acceptance on candidate `94afff004fcbc2ed37af0297d37c51ab50ba7098` returned `PROSPECTIVE_OUTCOME_SYNC_HOST_ACCEPTANCE=PASS`. All 54 ended unevaluated candidates resolved through official Gamma; the bounded cycle appended 54 immutable Gamma snapshots, 54 canonical `official-outcome-v1` labels, and 54 immutable live-prediction evaluations, with zero pending markets. The deployed `/opt/bp` checkout remained unchanged at `0189ff70fc628c71ab7c503bac369c34bf5ce8bc`, the paper worker remained active, the predictor remained inactive before and after acceptance, `MODE=research`, `LIVE_TRADING_ENABLED=false`, and both real-money limits remained zero. The bounded paper cycle observed four existing settlements and created none during that explicit pass; economic and calibration interpretation is intentionally deferred to the separate read-only prospective-evidence reporter. Sanitized acceptance evidence is stored at `docs/evidence/phase-14-prospective-outcome-sync-host-acceptance-20260831.json`.

A passing non-deploying production-host acceptance of the outcome/evaluation sync was established at this checkpoint. At that time, the prospective-evidence reporter rerun and permanent installation of the long-running research-only live predictor and prospective outcome-sync daemons had **not yet been established**; Sections 33 and 34 record those later results. The Master live-gate matrix remained unchanged: overall status `fail`, Phase 15 blocked, and any future controlled live launch still requires every Section 4.3 gate to pass plus separate explicit real-money authorization.

---

# 33. Phase 14 post-outcome-sync prospective evidence result

On 31 August 2026, the read-only prospective-evidence reporter was rerun on exact candidate `de907d324c7ee4ec46e2dfef1eb516dbb3fa8348` after the accepted outcome sync had populated the immutable evaluation ledger. The report returned `PROSPECTIVE_EVIDENCE_HOST_REPORT=PASS`; the deployed `/opt/bp` checkout remained unchanged at `0189ff70fc628c71ab7c503bac369c34bf5ce8bc`, the paper service remained active, `LIVE_TRADING_ENABLED=false`, and both real-money limits remained zero.

The rerun observed 54 immutable prediction evaluations and two settled prospective paper trades. Realized after-cost P&L was `-7.792422663291` USD total and `-3.8962113316455` USD mean per settled trade. The deterministic 10,000-resample bootstrap 95% interval for mean realized P&L was `[-4.285508316075, -3.506914347216]`, entirely below zero. The reporter therefore classifies `positive_after_cost_profitability=fail`. This is direct prospective negative economic evidence; it must not be hidden by the larger evaluation count or by post-hoc threshold retuning.

Across all 54 evaluated predictions, raw Brier/log-loss means were `0.11328198148148148` / `0.3669084283864382` and calibrated Brier/log-loss means were `0.10868378084722523` / `0.35286272448721295`. Because no approved prospective numerical calibration acceptance threshold exists, `calibration_acceptable` remains `insufficient_evidence`. Because Section 4.3 deliberately defines no fixed prospective sample count, `sufficiently_large_live_paper_sample_with_uncertainty` also remains `insufficient_evidence`; the observed size and uncertainty must be reported rather than converted into an invented pass/fail sample threshold.

Paper reconciliation remained `OK` with zero violations across three paper orders, three trade signals, and 51 no-trade signals, so `order_execution_and_reconciliation_tested=pass`. `automatic_promotion=false`. The Master live gate remains `fail`: prospective profitability fails, sample/calibration and walk-forward stability remain insufficient, geographic eligibility fails, and explicit real-money authorization is absent. Phase 15 remains blocked. Sanitized evidence is stored at `docs/evidence/phase-14-prospective-evidence-host-report-post-outcome-sync-20260831.json`.

This negative prospective result does not prevent the separate research-only permanent installation of `bp-live-predictor.service` and `bp-prospective-outcomes.service` for continued immutable evidence collection. Such installation is an operational continuity step only; it cannot promote a model, change an evidence gate, or authorize live trading.

---

# 34. Phase 14 permanent prospective research runtime installation

On 31 August 2026, the separate research-only permanent runtime rollout authorized by D-030/D-031 was completed for `bp-live-predictor.service` and `bp-prospective-outcomes.service`. This rollout exists only to continue immutable prospective prediction, official-outcome, evaluation, and money-disabled paper evidence collection. It does not constitute economic validation, model promotion, live-gate progress, or real-money authorization.

The first production install attempt on exact candidate `196519555bed8f68d37654bd171dac23f681fd52` failed closed before any checkout, unit, or service mutation with `REASON=deployed_checkout_not_clean`. Read-only host inspection established that `/opt/bp` contained the dashboard build/runtime residue produced by the already-established dashboard deployment: modified tracked `apps/dashboard/next-env.d.ts` and `apps/dashboard/tsconfig.json`, plus untracked `.node/`, `apps/dashboard/.next/`, `apps/dashboard/node_modules/`, and `apps/dashboard/tsconfig.tsbuildinfo`. The correction did not delete or reset those artifacts. Instead, a test-first installer change permits only that explicit generated residue, rejects every other tracked or untracked checkout status entry, rejects candidate commits that collide with the preserved runtime paths, and preserves/restores the two tolerated tracked generated files during rollback.

Corrected exact-head pre-host verification passed on candidate `d2b2d515a4b982c691360fa1c6c46a461a665ff9`: CI #1661 / run `33394458434`, Historical Backfill Smoke #528 / run `33394458466`, Live Recorder Smoke #635 / run `33394458523`, and Recorder Short Soak #600 / run `33394458454` all succeeded. The associated residue-regression RED checkpoint was `d731d2896e476ee082e6d39d47305fe08ecc97b3`; the final corrected candidate also includes the follow-up status-classification and documentation consistency fixes.

The corrected production-host run returned `PHASE14_PROSPECTIVE_RUNTIME_INSTALL=PASS`. `/opt/bp` moved from `0189ff70fc628c71ab7c503bac369c34bf5ce8bc` to exact candidate `d2b2d515a4b982c691360fa1c6c46a461a665ff9`. `bp-live-predictor.service` is active and enabled, `bp-prospective-outcomes.service` is active and enabled, and all five established core services remained active: recorder, PostgreSQL, dashboard API, dashboard web, and paper execution. The root-controlled safety file is `/etc/bp/bp-prospective-runtime-safety.env`; its previous state was absent. Effective safety remained `MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`. Sanitized repository evidence is `docs/evidence/phase-14-prospective-runtime-install-host-acceptance-20260831.json`; the host-local evidence file is `/var/lib/bp/evidence/phase14-prospective-runtime-install-20260831T131003Z.txt`.

This operational PASS does not alter Section 4.3. Prospective after-cost profitability remains `fail`; prospective sample sufficiency and calibration remain `insufficient_evidence`; geographic/compliance eligibility remains `fail`; explicit real-money authorization remains `fail`; `automatic_promotion=false`; the overall Master live gate remains `fail`; and Phase 15 remains blocked.

---

# 35. Phase 14 V1 market-price timestamp-coherence defect and approved V2 research boundary

On 2 September 2026, read-only attribution of the growing prospective 5m paper sample established a cross-source timestamp-coherence defect in the accepted V1 research path. The V1 raw probability is the newest first-party Polymarket CLOB `/prices-history` Up-token point satisfying `observed_at <= scheduled_at`, obtained with one-minute fidelity. The edge engine compares the calibrated result with a separately observed selected-side WebSocket best ask whose compact state is subject to the existing 10-second freshness contract.

A transaction-level timing probe over 27 settled paper trades found that every probability observation was 33–51 seconds old at `scheduled_at`, while every selected-side book was approximately 0–1 second old. This makes it possible for V1 to interpret ordinary market movement between two materially different effective timestamps as very large apparent executable edge. The finding is consistent with the historical contract: `core-v1` recorded Polymarket token-price staleness but did not impose a token-price freshness gate; the accepted `market_price` champion consumed `pm_up_price`; Phase 8/9 selected timing/calibration/edge policy under that asynchronous source contract; and Phase 10 faithfully materialized the same meaning prospectively.

Existing `live-prediction-v1` predictions, evaluations, paper orders, fills, settlements, reconciliation, and P&L remain immutable evidence of the deployed V1 pipeline. They must not be rewritten, erased, or post-hoc reclassified. They remain valid evidence that V1 as deployed loses money after costs. They must not, however, be blended into a future corrected V2 profitability epoch or used to select V2 freshness, calibration, minimum-edge, or model parameters.

The approved V2 research direction is a new versioned market-price input built from first-party Polymarket WebSocket `last_trade_price` evidence with a dedicated trade timestamp and receipt timestamp preserved as part of provenance. The generic compact-state `last_event_at` cannot stand in for last-trade freshness because later book or price-change events may refresh state while the stored last trade remains old. An untimestamped REST `last-trade-price` response, midpoint, selected ask, opposite-token transform, or other synthesized value must not silently substitute for the V2 probability input.

Missing or stale timestamped last-trade evidence must fail closed to no-trade. The existing 10-second selected-book freshness threshold remains frozen and must not be loosened from the same prospective sample. No numerical probability/last-trade freshness threshold may be chosen by inspecting the 27 V1 failures. Any V2 source-freshness rule must be derived and frozen independently under a new versioned research contract.

The V1 calibration fit and validation-selected minimum-edge threshold cannot be carried forward automatically, because both were selected under the asynchronous V1 eligibility contract. V2 must rerun the leakage-safe chronological research chain under its own source semantics, with train/validation/test/holdout boundaries preserved. If independent historical timestamped last-trade evidence is insufficient to validate a V2 policy, the correct policy is `no_trade` while a separate prospective shadow-evidence epoch is collected. `automatic_promotion=false` remains mandatory.

This V2 work is a Phase 14 research correction, not Phase 15. It does not authorize real orders, increase risk limits, alter geographic/compliance requirements, or count as live-gate progress by itself. `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0` remain mandatory. The complete Section 4.3 Master live gate and separate explicit real-money authorization remain prerequisites for any future controlled live launch.

---

# 36. Phase 14 Gate A production acceptance and continuous V2 forward-coverage boundary

On 2 September 2026, the separately authorized research-only Gate A production rollout passed and advanced the deployed checkout from `be1f82f65d15b2e172495e6ae934ec9a78648c32` to `d077e45f24704e6038c947169c84527e954de975`. The canonical V2 forward epoch is `2026-09-02T12:18:02Z`. All seven established research services remained active and effective safety remained `MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`.

Host acceptance proved dedicated first-party Polymarket WebSocket last-trade provenance using provider source timestamp, BP receipt timestamp, price, and dedupe identity, and proved that unrelated later market activity did not refresh the dedicated last-trade timestamp. One fully completed post-epoch 5m market then produced exactly four immutable `core-v2-last-trade` feature rows at the frozen 60/120/180/240-second offsets, with zero future-source-cutoff violations. The outcome-blind coverage report observed one market/four rows, emitted `policy_selected=false` and `automatic_promotion=false`, and recorded coverage input SHA-256 `44592883ca47d18337b4c4385f3e34badc00bc30e5a124a02c0c4c99cccf6891`. Sanitized evidence is `docs/evidence/phase-14-v2-gate-a-rollout-20260902.json`.

That one-market operational sample is not a V2 timing, freshness, calibration, model, edge, profitability, or promotion result. Observed selected-book ages were about 0.4–1.1 seconds while last-trade source ages reached about 18–20 seconds, reinforcing that policy selection must remain deferred and independent of both the V1 failure sample and this single acceptance market. Selected-book freshness remains frozen at 10 seconds, and no V2 last-trade freshness threshold is selected.

The approved next operational package is a continuous **outcome-blind V2 forward-coverage collector**. It is limited to completed 5m markets starting at or after the canonical forward epoch, exactly the four approved feature offsets, immutable/preserve-existing semantics, descriptive coverage reporting, structured logging, and restart-safe reconciliation using database natural keys rather than a mutable cursor. It must not read labels/outcomes, compute accuracy/P&L/calibration metrics, choose a V2 policy, create V2 predictions or paper orders, mutate V1 evidence, alter the selected-book freshness rule, enable live trading, or begin Phase 15.

The implementation package adds `bp_engine.features.v2_forward`, a thin research-zero-money CLI/script, hardened `bp-v2-forward-coverage.service` and persistent one-minute `bp-v2-forward-coverage.timer`, plus an exact-head rollback-capable rollout helper. The oneshot may connect only to the local PostgreSQL service over localhost/Unix networking; non-loopback IP traffic is denied by systemd. Rollback may restore checkout and unit state but must never delete, truncate, or rewrite immutable `market_features` or any other research ledger.

Pre-packaging exact-head CI #1978 (`33639062997`) passed all 860 Python tests, Ruff, deployment validation, health checks, dashboard tests/typecheck/build, Python wrapper compilation, and rollout-helper Bash syntax. Full source-diff review against deployed head `d077e45f24704e6038c947169c84527e954de975` leaves the frozen V1 feature service and the complete `live_prediction`, `calibration`, and `execution` paths unchanged, with no migration, live activation, wallet/secret path, risk-limit increase, geographic bypass, V2 economic policy, or Phase 15 implementation.

The continuous V2 forward-coverage collector was subsequently deployed as a research-only, outcome-blind runtime before the 4 September storage incident; `PROJECT_STATE.json` records production rollout performed on head `c29fe227f959305f67031e922ca659869a826c4f`. The later recorder-reliability diagnosis also records the collector producing missing/stale Polymarket coverage evidence during production operation. Because storage recovery is now in progress and the recorder remains stopped, the collector's current active/enabled runtime state is deliberately **not asserted**. This operational history does not authorize Gate B, policy selection, promotion, paper/live execution changes, or real-money trading. The Section 4.3 Master live gate remains `fail`, `automatic_promotion=false`, and Phase 15 remains blocked.
