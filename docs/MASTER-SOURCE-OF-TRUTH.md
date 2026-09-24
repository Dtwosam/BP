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

### 4.3.1 Frozen V3 controlled-live authorization — 23 September 2026

The user has explicitly authorized **pursuing** a controlled real-money transition for the exact frozen V3 while V4 research collection continues in parallel. This satisfies only the explicit-human-authorization requirement; it does not itself enable trading or override any other live-gate requirement.

Before any real order is allowed, a fresh V3-specific reassessment must isolate `v3-frozen-paper-v1` / `paper-execution-v3-frozen-v1` and report paper-sample uncertainty, after-cost profitability robustness including largest-winner sensitivity, calibration, drawdown/losing streak, and reconciliation. Current direct Polymarket geographic eligibility must also pass. Any failed or insufficient row keeps Phase 15 blocked. No proxy/VPN/restriction bypass is permitted.

Until the complete gate passes, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0` remain mandatory. The V3 model artifact, calibration, 240-second timing, `min_edge=0.075`, and paper sizing remain frozen; this authorization is not permission to tune them from paper results.

### 4.3.2 Frozen V3 read-only live-gate reassessment result — 23 September 2026

The one-shot production reassessment completed read-only on exact main `ba98b3871e03895d04bb2b06d4be5350f6c17491`. Sanitized evidence is `docs/evidence/phase-14-v3-live-gate-reassessment-production-20260923.json`.

The frozen V3 paper sample contained 76 settled trades (47 wins, 29 losses), realized after-cost P&L `682.252111761097` USD, mean P&L `8.97700147054075` USD, profit factor `6.262572772140266`, maximum drawdown `27.974134608836` USD, maximum losing streak 4, and P&L `450.802786408456` USD after removing the largest winner. The deterministic 10,000-resample bootstrap 95% interval for mean realized P&L was `[1.6407501892525114, 18.945890261783955]`, entirely above zero. Under the already-approved profitability rule, `positive_after_cost_profitability=pass`. Reconciliation remained `OK` with zero violations, so `order_execution_and_reconciliation_tested=pass`. Explicit user live authorization is `pass`.

Across all 519 evaluated frozen-V3 predictions, raw/calibrated Brier mean was `0.0999937890122578` and raw/calibrated log-loss mean was `0.3173973846097945`. No approved numerical prospective calibration acceptance threshold exists, so `calibration_acceptable=insufficient_evidence`. Section 4.3 still defines no fixed prospective sample-size threshold, so `sufficiently_large_live_paper_sample_with_uncertainty=insufficient_evidence`. `walk_forward_results_stable_enough` also remains `insufficient_evidence`.

The direct official Polymarket geoblock request from the production VM returned `blocked=true`, `country=US`, `region=SC` at `2026-09-23T09:43:05.145648Z`. Therefore `geographic_compliance_eligible=fail`. The overall Master live gate remains `fail`; Phase 15 remains blocked; live trading remains disabled; real-money limits remain zero. This result must not be bypassed with VPNs, proxies, tunnels, or relocation tricks. Preserve the one-shot evidence and do not rerun the reassessment absent a separately versioned reason.


### 4.3.3 Accelerated frozen-V3 canary-readiness mapping — 23 September 2026

The user has requested the shortest compliant path to a controlled live canary. The separately versioned `phase15-v3-canary-readiness-v1` audit may reassess only the three remaining statistical rows; it cannot enable trading.

The sample-sufficiency rule reuses the Phase 13 principle that there is no magic count: prospective evidence is sufficient only when the deterministic 95% lower confidence bound for mean realized after-cost P&L is strictly above zero. No post-hoc round-number minimum is introduced.

Walk-forward stability is mapped from three already-separated layers: the pre-registered five-fold V3 ordinary validation economics gate, the untouched V3 final holdout, and the prospective paper uncertainty interval. The frozen V3 implementation forces `no_trade` whenever the ordinary validation economics gate fails; because the immutable selected policy is `trade_threshold` at `min_edge=0.075`, that pre-registered ordinary economics gate necessarily passed.

For calibration, the new reliability acceptance rule is frozen **before** calibration intercept/slope diagnostics are read. Prospective calibrated Brier and log loss must be no worse than the frozen V3 final-holdout values, and a deterministic bootstrap 95% interval for calibration intercept must contain 0 while the corresponding calibration-slope interval must contain 1. Ten-bin ECE is descriptive only. If this new audit fails, the rule may not be weakened after seeing the result.

Geography remains independent and mandatory. A statistical PASS cannot override a blocked physical location or blocked execution host. Before any real order, the user's ordinary physical-network check with VPN/proxy disabled and the eventual execution host's direct official Polymarket geoblock check must both be unblocked. Infrastructure must not be used to disguise a restricted user location.

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

For the current Phase 14 production storage architecture, physical raw retirement uses verified hourly partitions. Normal retirement requires compact state beyond the full hourly interval. A separately gated stopped-recorder recovery may retire only the terminal partially populated partition at its last retained raw timestamp when there are no later raw rows and every required compact feed is strictly beyond that timestamp; this recovery exception must be explicit and auditable and must not alter steady-state retention semantics.

Steady-state PostgreSQL retirement must preserve that archive/parity contract without allowing attached-child physical removal to block the active recorder indefinitely. Under D-055, an eligible verified hourly child is detached with PostgreSQL concurrent partition detach, its standalone physical row count is reverified against the same manifest, the detached table is physically dropped, and dedupe-ledger cleanup follows only after that physical raw retirement. Interrupted or already-completed detach state remains visible to retention health and is resumed fail-closed; the existing maintenance timeout and storage-health thresholds are not relaxed.

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
- Phase 14 Gate A timestamp-coherent V2 is production-accepted from forward epoch `2026-09-02T12:18:02Z`; the continuous V2 forward-coverage collector was subsequently deployed research-only/outcome-blind on production head `c29fe227f959305f67031e922ca659869a826c4f`. The partitioned-storage migration is production-accepted on head `895c6bd2f9409f16bf5d544b26b30e20ecbfe43a`, and the separate recorder restart/reliability gate passed on 9 September 2026 with `RECORDER_WRITER_WORKERS=4`, all four required feeds flowing, zero soak-window incidents/backpressure, and storage health still `ok`. Subsequent read-only diagnostics found that the 15:00 and 16:00 UTC steady-state maintenance cycles deadlocked under active recorder load on repeated dedupe-parent index DDL; the resulting stale maintenance heartbeat correctly triggered the existing fail-closed storage-health chain and stopped `bp-recorder.service` at 16:05 UTC. Disk capacity was healthy, and with the recorder stopped the 17:00 maintenance cycle completed at 17:03:27Z with all composite storage guards `true` and status `ok`. The recorder was intentionally inactive after that fail-closed stop until the dedicated recovery passed in production at 2026-09-09T18:55:51Z; it is now active again with the accepted four-worker configuration. PR #160 merged the parent/sequence/index DDL removal to `main` as `a903cf4bb1cb2e2a24e80a8b00f0eecb68b3e15a`, replacing the already-partitioned runtime path with read-only schema validation plus current + two future raw-hour provisioning; that runtime fix is now production-deployed through exact minimal candidate `e9c7afc1536880e4612cb6e3d1a7282fa37c69f5` rather than by advancing production across the later main history. Production must not jump across the 153 later commits from accepted head `895c6bd2f9409f16bf5d544b26b30e20ecbfe43a` merely to obtain this fix. The exact minimal recovery candidate `e9c7afc1536880e4612cb6e3d1a7282fa37c69f5` on `ops/phase14-storage-deadlock-recovery-candidate` changes only the exact-main `src/bp_engine/storage/partitioned_raw.py` bytes and its PostgreSQL regression test; verification-only PR #161 CI passed 998 tests plus Ruff, deployment validation, research-mode health, and dashboard checks. PR #162 merged the dedicated fail-closed recovery gate to `main` as `63a0eb8b52f736db4b3e329adcdec06ae2ae02f9`. Final PR head `8416ac4242382284f1d7312b445df64465a26135` passed CI with 1,028 tests plus Historical Backfill Smoke, Live Recorder Smoke, and Recorder Short Soak; post-merge main CI `34388363196` also passed 1,028 tests. The gate requires the existing accepted four-worker configuration, proves one maintenance cycle succeeds under active recorder load without a MainPID/restart-count change, fails closed if any holdout artifact exists without reading it, and rolls back to the accepted checkout with the recorder stopped on failure. Verification-only PR #161 was closed without merge after the exact minimal production-shaped candidate passed its independent 998-test CI. The explicitly authorized recovery then passed in production using helper head `d217e20befd98d935138673ba1da17a62e895d44`: both prestart and active-recorder maintenance cycles succeeded, the recorder remained active at `RECORDER_WRITER_WORKERS=4` with MainPID `3300693` and zero restarts, all four required feeds flowed during the natural-load soak with zero failures/backpressure, storage remained `ok` with all three guards true and zero retention lag, and the storage-maintenance, disk-health, and V2 forward-coverage timers were restored active. Host evidence is `/var/lib/bp/evidence/phase14-recorder-deadlock-recovery-20260909T185551Z.json`, sanitized evidence is `docs/evidence/phase-14-recorder-deadlock-recovery-20260909.json`, and the Cloud Shell transcript SHA-256 is `f8929882c2502fbab6f0de6d37acf5abe31ded82bab7fb5373174e25855bf4b5`. Gate B actions were not performed, the final holdout remained unread/untouched, `automatic_promotion=false`, live trading remains disabled, and nonzero money limits remain unauthorized. The V2 forward-coverage collector/timer runtime state remains a separate research-only activation/verification boundary and is not implied by the recorder PASS. A dedicated restore-only gate is merged to `main` and has now passed in production. The existing collector timer is active+enabled on the accepted production checkout. A subsequent read-only coverage report over 426 markets / 1,704 immutable `core-v2-last-trade` rows found complete 60/120/180/240-second offset coverage, zero future-cutoff violations, zero invalid non-finite values, and canonical coverage hash `aab75574aa7faf18e65358353403e5ec1a2b89dd42424eb7b0e3329bf683b099`. Before any labels/outcomes were joined, the finite `max_last_trade_age_seconds` candidate grid was frozen as exactly **1, 2, 5, 10 seconds plus explicit `no_trade`**, with 10 seconds remaining the hard selected-book freshness ceiling. The separate Gate B research package is merged and engineering-ready: an unlabeled feature-only plan freezes chronological whole-market partitions, embargoes, the final holdout, and the V2 research search configuration before labels are queried; labeled preparation is restricted to non-holdout condition IDs; the final holdout is a separate hash-bound no-clobber evaluation; the V2 source is `pm_up_last_trade_price` with no V1 `pm_up_price` or training-prior fallback; and missing/stale/malformed dedicated last-trade provenance is explicit no-trade. Its first production-evidence attempt on 9 September 2026 using helper head `a395a069c1a95f5b3968ea913c08b5a2bb1385a6` stopped during feature-only planning with `test requires at least 6 markets; found 4` and partial directory `/var/lib/bp/evidence/phase14-v2-gate-b-20260909T142914Z`. Because the plan command failed before artifact write, no `plan.json`, `selection.json`, or `holdout.json` was created and the final holdout remained unread. The Phase 8 minimum of six test markets is unchanged. Recovery therefore does not weaken or skip a fold: the feature-only planner now advances the analysis epoch start in the existing two-hour cadence and selects the earliest start whose **entire remaining contiguous walk-forward schedule** satisfies all unchanged Phase 8 minimums. The selected epoch start, excluded prefix, rejected candidate starts, and reasons are included in the hashed plan. RED head `275009017a4ce8522156f1cac905a0f24bdc8f1b` reproduced the sparse-prefix failure with 1,014 other tests passing in CI `34364344481`; implementation GREEN head `7b433a4435cc80e14f74bbce0f83ce973754e2d2` passed CI `34364553297` with 1,015 tests. PR #156 then merged the full recovery as `184b725724d46d7ed757dd715a88dafddbdd45e1` after final branch head `393effa09fbb7a686cce9db2031822d3c3c40a1b` passed push CI, PR CI, Historical Backfill Smoke, Live Recorder Smoke, and Recorder Short Soak; post-merge CI `34366077039` passed 1,015 tests plus Ruff, deployment validation, research-mode health, and dashboard checks. The helper also now prints explicit plan/selection/holdout/summary presence and `HOLDOUT_TOUCHED` on failure. The exact-main production retry was then attempted on `184b725724d46d7ed757dd715a88dafddbdd45e1` and failed safely again in the feature-only `plan` stage with `no contiguous Gate B epoch satisfies the frozen walk-forward minimums; last rejection: train requires at least 24 markets; found 0`. The partial directory `/var/lib/bp/evidence/phase14-v2-gate-b-20260909T150136Z` contains no plan, selection, holdout, or summary artifact and reports `HOLDOUT_TOUCHED=false`. This is now classified as insufficient contiguous V2 feature evidence, not a reason to weaken the accepted research geometry. The 8h/2h/2h walk-forward with 2h steps and a 2h final holdout requires at least 18 hours from a viable analysis epoch to support three ordinary folds plus the final holdout, subject to all unchanged market-count minimums. Further Gate B retries are blocked until feature-only chronology can satisfy that contract; the final holdout remains unread. A dedicated repeatable readiness checker is merged to `main` by PR #158 as `bcaee9343c6ec616d0a7d50479429344ec294c41`: it executes only the same unlabeled planner contract, never calls `prepare` or `evaluate-holdout`, writes no Gate B artifact, returns `READY=true/false`, records the derived 18-hour minimum contiguous epoch, and exposes every rejected candidate start/reason plus the earliest viable analysis start when one exists. Final branch head `30e0ebebd339b1086ab8a31c83c5f16138a47435` passed push CI, PR CI, Historical Backfill Smoke, Live Recorder Smoke, and Recorder Short Soak; post-merge CI `34371290608` passed 1,021 tests plus Ruff, deployment validation, research-mode health, and dashboard checks. `READY=false` requires continued evidence collection; `READY=true` is only a prerequisite for one future Gate B evidence run and is not Gate B authorization. After the recorder deadlock recovery passed, the helper was rerun at `2026-09-09T20:13:53Z` from exact main `223bd255c8001149eba51d3f5dfb767cb4c2461b` against deployed head `e9c7afc1536880e4612cb6e3d1a7282fa37c69f5` and returned `READY=false` with 496 markets, 81 rejected candidate starts, and last rejection `train requires at least 24 markets; found 19` at analysis start `2026-09-09T04:20:00Z`. The run read no labels, wrote no Gate B artifacts, and preserved `HOLDOUT_TOUCHED=false`. Candidate starts advance only by the frozen two-hour step. The first candidate grid start strictly after the recovery is `2026-09-09T20:20:00Z`; a wholly post-recovery 18-hour candidate cannot be fully evaluable before `2026-09-10T14:20:00Z`, which is a lower bound rather than a readiness prediction. A fresh exact-main feature-only readiness check then completed at `2026-09-10T09:07:50Z` on helper head `26e91498672618b5cafb7f2d901994103c5554b8` against deployed head `e9c7afc1536880e4612cb6e3d1a7282fa37c69f5` and returned `READY=true`: 651 markets, accepted analysis start `2026-09-09T10:20:00Z`, 5 eligible folds, and 24 feature-only final-holdout markets. The run preserved `labels_read=false`, wrote no plan/selection/holdout artifact, and kept `HOLDOUT_TOUCHED=false`; the final holdout remains unread. `READY=true` ends the evidence-collection readiness blocker but does not authorize Gate B. Gate B one-shot research execution is now explicitly authorized for the existing exact-main `plan` -> `prepare` -> `evaluate-holdout` sequence only. This permits the frozen final holdout to be read and evaluated exactly once after validation selection is frozen; it does not itself accept a V2 policy or authorize prospective V2 activation, automatic promotion, Phase 15, geographic bypass, live trading, or nonzero money. `automatic_promotion=false` and all research/zero-money controls remain mandatory. PR #167 subsequently merged an optional feature-only readiness watcher to `main` as `80bf77da54fd1a2c89b4d43910457184bd06fead`. Final watcher head `0513770c824baf91f82de74e2af6b4789ccaa7d9` passed CI `34410923330` with 1,039 tests plus Historical Backfill Smoke `34410923248`, Live Recorder Smoke `34410923329`, and Recorder Short Soak `34410923305`; post-merge CI `34411184671` also passed 1,039 tests. The watcher is a versioned sidecar scheduled every two hours at `:30 UTC`, executes only feature-only readiness in a PostgreSQL read-only transaction, revalidates exact deployed head, four recorder workers, core services/timers and composite storage health before and after the database read, refuses to run if any Gate B artifact exists by pathname without reading artifact contents, and has no failure coupling to the recorder. It does not modify `/opt/bp` or run `prepare`/`evaluate-holdout`. This package is merged engineering only: production installation is not authorized and has not occurred. Full contract is `docs/PHASE-14-V2-GATE-B-READINESS-WATCH.md`;
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

The continuous V2 forward-coverage collector was subsequently deployed as a research-only, outcome-blind runtime before the 4 September storage incident; `PROJECT_STATE.json` records production rollout performed on head `c29fe227f959305f67031e922ca659869a826c4f`. The later recorder-reliability diagnosis also records the collector producing missing/stale Polymarket coverage evidence during production operation. Partitioned-storage migration and storage health are accepted in production on head `895c6bd2f9409f16bf5d544b26b30e20ecbfe43a`. The separate post-storage recorder restart gate then passed in production on 9 September 2026 using helper head `a65f2a87d2575aa31e071b2df6d0670abe362bdb`: `RECORDER_WRITER_WORKERS=4` was parsed by the deployed application, `bp-recorder.service` remained active, all four required feeds emitted fresh events during the acceptance window, no soak-window incidents/backpressure were recorded, and partitioned-storage health remained `ok` with both storage timers active. The V2 forward-coverage collector/timer active/enabled state remains a separate research-only activation/verification boundary; the recorder PASS does not authorize or imply that activation. The dedicated restore-only helper `scripts/deploy/phase14_v2_forward_coverage_restore_gate_cloudshell.sh` is merged to `main` and engineering-complete for that boundary and is deliberately narrower than the original deployment helper: it binds the accepted production head/storage evidence, requires the recorder at four workers plus all research services and storage guards healthy, requires the installed V2 units to match the deployed checkout with no drop-ins, runs one bounded outcome-blind cycle, and activates only the existing timer. It performs no production checkout change, unit reinstall, storage-index installation, recorder/core-service restart, policy selection, promotion, or live/money change, and it restores the pre-run timer state on failure. The helper passed in production on 9 September 2026 using helper head `2f6e4e385bf70681f2d79d906d380dfbe88406bb` against deployed head `895c6bd2f9409f16bf5d544b26b30e20ecbfe43a`. The accepted runtime has `bp-v2-forward-coverage.timer` active+enabled; the acceptance report contained 420 markets / 1,680 V2 rows, zero future-cutoff violations, no newly eligible targets at the acceptance instant, `policy_selected=false`, and `automatic_promotion=false`, with host evidence `/var/lib/bp/evidence/phase14-v2-forward-coverage-restore-20260909T112436Z.json`.

A later read-only coverage-only report, still without any label/outcome/P&L/calibration access, observed **426 markets / 1,704 rows**, exactly 426 rows at each of the 60/120/180/240-second offsets, zero future-cutoff violations, zero invalid non-finite values, and canonical `coverage_input_sha256=aab75574aa7faf18e65358353403e5ec1a2b89dd42424eb7b0e3329bf683b099`. Last-trade evidence was available for 1,421 / 1,704 rows on each side; source-age medians were 10.875s Up and 10.976s Down, with p90 values 30.037s and 30.73s respectively. Selected-book age p90 remained 2.0s on both sides. Under D-033, and before any labels/outcomes are joined, `docs/evidence/phase-14-v2-freshness-preregistration-20260909.json` therefore freezes the finite candidate set `max_last_trade_age_seconds ∈ {1, 2, 5, 10}` plus explicit `no_trade`. The 10-second candidate is the hard ceiling already imposed on selected-book freshness; 2 seconds is anchored to observed book p90, 1 second is the strict low-latency candidate, and 5 seconds is a predeclared intermediate. This configuration may not be rewritten by test/final-holdout/prospective evidence. No winning threshold, timing, model, calibration, edge/min-edge policy, or promotion is selected by this preregistration. Gate B remains unauthorized until the separate chronological validation/holdout research package satisfies its full acceptance criteria. The Section 4.3 Master live gate remains `fail`, `automatic_promotion=false`, and Phase 15 remains blocked.


### Phase 14 V2 Gate B interrupted attempt and frozen-plan recovery boundary — 10 September 2026

The one-shot Gate B research authorization recorded under D-039 was exercised at `2026-09-10T10:27:33Z` from exact helper/main head `0941684676ea35949ac938a3bca33012b6ea0e19` against deployed production head `e9c7afc1536880e4612cb6e3d1a7282fa37c69f5`. Feature-only planning completed and wrote `plan.json` under `/var/lib/bp/evidence/phase14-v2-gate-b-20260910T102812Z`. `prepare` then failed on missing canonical non-holdout condition `0x15a96b013fa3009dc15f83df516116b583cce49325e313d578e51b8b5d9b70e8` before any selection or final-holdout evaluation. `selection.json`, `holdout.json`, and `summary.json` remained absent, `HOLDOUT_TOUCHED=false`, and Gate B performed no production database mutation. The original one-shot authorization is therefore consumed; it is not reusable.

The plan created by that attempt is now the only admissible Gate B plan for this attempt. Recovery must not replan, alter frozen walk-forward geometry/candidate grids, use final-holdout labels, or choose a different holdout. PR #172 merged the hardened frozen-plan recovery package to `main` as `3e00939f7f70453f9aa054e46c31c72600cdb83d` from final head `1f6709632b3a5546bdd158552d8670ca7603bd1b` after CI `34479665673`, Historical Backfill Smoke `34479665575`, Live Recorder Smoke `34479665644`, and Recorder Short Soak `34479665747` all passed; post-merge CI `34480403106` then passed 1,059 tests. The package adds a separate holdout-blind label audit/recovery path and a separate same-plan resume path. Audit is read-only. Label repair may append only canonical post-resolution Gamma -> `official-outcome-v1` evidence for frozen non-holdout condition IDs and requires fresh SHA-bound production authorization. Resume requires zero missing non-holdout labels, the exact same `plan.json` SHA-256, and its own fresh SHA-bound authorization; it cannot repair or replan and only then may run `prepare -> evaluate-holdout` once. Before that one-shot final-holdout evaluation, the resume helper exclusively creates and fsyncs `holdout-attempt.json`; any later failure reports `HOLDOUT_TOUCHED=true`, and a preexisting attempt marker blocks another evaluation. Recovery provenance now timestamps each successful Gamma response immediately after receipt before storing the canonical snapshot and deriving its label.

Historical boundary note: the no-repair/no-resume state described above applied before the separately authorized recovery and resume and is superseded by the 11 September 2026 consumed-final-holdout closeout below. The later label recovery and same-plan resume were performed under their separate SHA-bound approvals; `holdout-attempt.json` was durably created before final-holdout evaluation, so the final holdout is consumed and the frozen plan must not be reused. Gate B did not pass. `MODE=research`, `LIVE_TRADING_ENABLED=false`, both money limits remain zero, and `automatic_promotion=false`. Canonical sanitized pre-resume attempt evidence remains `docs/evidence/phase-14-v2-gate-b-attempt-20260910T102733Z.json`.

### Phase 14 Gate B audit storage-health interruption and dedupe cleanup fix — 10 September 2026

The first holdout-blind audit of the frozen failed Gate B plan after the recovery package merged was attempted read-only against production. It was bound to `/var/lib/bp/evidence/phase14-v2-gate-b-20260910T102812Z/plan.json` with SHA-256 `8f2a756161bb0d85e6020d6ff0d6f4f3540eb28caf133870a4926f65ac7d2fea` and stopped before any label inspection because composite partitioned-storage health was not `ok`. The final holdout remained unread/untouched, `selection.json`, `holdout.json`, and `summary.json` remained absent, and the audit performed no production database mutation.

Read-only production diagnostics established a new steady-state maintenance performance defect: the bounded dedupe-ledger cleanup used a parent-table delete whose PostgreSQL plan rebuilt a hash/scan over approximately 24.4 million rows across all 16 dedupe hash children for each 50,000-row batch. The 13:00 UTC maintenance cycle exceeded its 55-minute timeout and the following cycle ran long enough to expire the two-hour maintenance-freshness guard. Filesystem free space was still above the 15 GiB critical reserve; composite health failed because the maintenance heartbeat became stale. This is distinct from the earlier parent-DDL deadlock fixed by PR #160.

PR #174 fixes this path by discovering the dedupe child partitions and deleting expired interval rows through bounded child-local `received_at` index scans and `ctid` deletes. Raw partition retirement still happens first, so any incomplete dedupe cleanup remains conservatively fail-safe. Final PR head `1e650786b3f8320d8406a84fb8e232bd7548a2ba` passed all four exact-head gates, merged to `main` as `ad71eb4d72948a023e57eca51c99ebce700df0a6`, and post-merge CI `34500104025` passed 1,060 tests plus Ruff, deployment validation, research-mode health, and dashboard checks.

The PR #174 fix is not yet deployed to production. Production storage health must be restored through a separate exact-SHA authorized deployment/recovery before the frozen-plan audit is attempted again. Do not weaken storage thresholds or bypass maintenance freshness. Gate B non-holdout label recovery, same-plan final-holdout resume, V2 acceptance/activation, Phase 15, geographic bypass, live trading, and nonzero money remain separate blocked boundaries; research/live-disabled/zero-money safety remains mandatory.

### Phase 14 dedupe cleanup exact-SHA production recovery boundary — 10 September 2026

The PR #174 maintenance fix now has an immutable production-shaped candidate, `71b33d3beaba4a11ef93e7c5bde1c517323f3440`, built directly from the currently deployed head `e9c7afc1536880e4612cb6e3d1a7282fa37c69f5`. Its scope is exactly `src/bp_engine/storage/maintenance.py` and `tests/storage/test_partitioned_raw_postgres.py`. Verification-only PR #176 passed CI `34504013943` and was then closed without merge, preserving the frozen deployed-base branch.

PR #177 merged the exact-SHA recovery gate as `a09837c0304325d783a7cc94f552b35665435640` from final head `75584201921a7b0b72a287ded310f4f3959d6647`. All four exact-head gates passed: CI `34504578757`, Historical Backfill Smoke `34504578825`, Live Recorder Smoke `34504578698`, and Recorder Short Soak `34504578864`. Post-merge CI `34504827764` passed 1,065 tests plus Ruff, deployment validation, research-mode health, and dashboard checks.

`scripts/deploy/phase14_dedupe_cleanup_recovery_gate_cloudshell.sh` fails locally before cloud contact unless its approval exactly binds the final helper/main SHA, deployed head `e9c7afc1536880e4612cb6e3d1a7282fa37c69f5`, immutable candidate `71b33d3beaba4a11ef93e7c5bde1c517323f3440`, and accepted storage evidence SHA-256 `f33a28f5306e46c509b0000a176d226c079aa2d160d595095ca228118542ce19`. It preserves research/live-disabled/zero-money safeguards, storage guards, four-writer validation, rollback, active-recorder maintenance acceptance, and Gate B holdout blindness.

No production recovery was performed by this engineering work. After separately authorized and accepted recovery, the next Gate B action is only the read-only frozen-plan label audit. Non-holdout label repair and same-plan final-holdout resume each retain their own fresh authorization boundary; V2 activation, Phase 15, geographic bypass, live trading, and nonzero money remain blocked.

### Phase 14 V2 Gate B consumed final-holdout closeout — 11 September 2026

After production storage recovery and the 100 GB -> 200 GB data-disk expansion, a holdout-blind audit of frozen plan SHA-256 `8f2a756161bb0d85e6020d6ff0d6f4f3540eb28caf133870a4926f65ac7d2fea` found 230 non-holdout conditions, 215 canonical labels present, and 15 missing while the final holdout remained untouched. The separately authorized non-holdout repair completed without reading final-holdout labels, and a fresh audit reached zero missing non-holdout labels.

The separately authorized same-plan resume then durably created and fsynced `holdout-attempt.json` before final-holdout evaluation. Final-holdout dataset loading failed closed because condition `0x2a760ccdb973c19ce13b6af86d752cf377790d5149a46b8498462904ece86efd` lacked canonical supervised input. From that marker onward `HOLDOUT_TOUCHED=true`: the final holdout and frozen plan are consumed, the frozen plan must not be reused, and Gate B was not accepted. No same-plan retry, holdout cherry-pick, or post-hoc replanning is admissible.

PR #179 merged the future feature-only outcome-label coverage fix as `13dee896a46ce38625159d5e587b68c08493a525`. At that point the fix was source-integrated only; that pre-rollout state is historical and was superseded by the accepted production rollout recorded below. A future Gate B attempt still requires a fresh statistically clean feature-only planning epoch and a new final holdout, with final-holdout access again protected by a new one-shot explicit authorization. V2 activation, automatic promotion, Phase 15, geographic bypass, live trading, and nonzero money remain blocked.

### Phase 14 V2 outcome-label coverage rollout PASS closeout — 11 September 2026

The Phase 14 V2 outcome-label coverage rollout passed in production at `2026-09-11T12:48:01Z`. PR #182 merge `f3ef5717390d0dd8d577bdc73143baabd12ff5ce` remains the provenance of the read-only preflight implementation, and the passing preflight ran from exact helper/main `17833c64ba7b3836ad4f047167d7d2d861fd2bc0` against previously deployed `71b33d3beaba4a11ef93e7c5bde1c517323f3440`. The separately authorized rollout then moved `/opt/bp` to exact immutable candidate `7c3af78da1922a0e5187c24b799951130cc98887`, whose runtime blob is `00e15c067aa4d47176b530c3150f2da9400f5207` and regression-test blob is `59800179c88743a65f5f26483c7af82fa9aced88`.

Only `bp-prospective-outcomes.service` was restarted; its PID changed from `3889510` to `3985114`. Unrelated long-running service PIDs remained unchanged, composite storage status remained `ok`, and the effective safety boundary remained `MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, `MAX_DAILY_LOSS_USD=0`, and `automatic_promotion=false`. The rollout performed no holdout access and no Gate B action. Durable production evidence is `/var/lib/bp/evidence/phase14-v2-outcome-label-rollout-20260911T124801Z.json`.

This rollout closes only the future outcome-label coverage gap. Gate B remains unaccepted. Frozen plan SHA-256 `8f2a756161bb0d85e6020d6ff0d6f4f3540eb28caf133870a4926f65ac7d2fea` and its consumed final holdout must never be reused. Any future Gate B attempt requires a fresh statistically clean, feature-only planning epoch and a new final holdout; access to that new final holdout remains a separate one-shot explicit authorization boundary. Phase 15, live trading, automatic promotion, geographic bypass, and nonzero money remain blocked.

### Phase 14 adaptive learning research contract — 12 September 2026

The first adaptive-learning cycle has an immutable bootstrap boundary of `2026-09-12T17:21:13Z`. This is the exact merge timestamp at which the adaptive subsystem became canonical on `main`; the first readiness count must not include resolved eligible markets whose learning availability predates this boundary. After the first completed adaptive cycle, subsequent `since_at` values come only from the prior cycle cutoff and this bootstrap boundary must not be reset.

The fresh timestamp-coherent V2 Gate B evidence package from 12 September 2026 is complete but **not accepted as a tradable V2 policy**. The frozen final selection is `no_trade` with validation reason `no_validation_edge_candidate_profitable`; the one-shot final holdout was touched and is permanently consumed. The runner's `PASS` verdict confirms evidence integrity and execution of the frozen procedure only; it does not establish economic edge, authorize Gate B acceptance, or permit V2 paper activation. The consumed final holdout must never be reused, retuned against, or used to select a replacement policy.

The project therefore continues its original supervised-learning objective through a research-only adaptive cycle. A learning cycle becomes ready after **50 newly resolved eligible markets** for the exact horizon, feature version, and official label version. Eligibility requires an immutable official resolved outcome plus matching frozen pre-resolution features. Executed trades are not required: `NO_TRADE` markets remain supervised learning examples because their resolved outcome can still teach the probability model.

When readiness passes, the adaptive cycle may retrain the existing deterministic baseline/model ladder over the accumulated eligible training window and record an immutable challenger artifact plus append-only cycle evidence. This does not create a new model family, does not bypass chronology or leakage controls, and does not weaken existing calibration, out-of-sample, economic, or champion/challenger requirements.

Before production migration 0015 creates `adaptive_learning_cycles`, first-cycle `adaptive-readiness` may use the explicit frozen bootstrap boundary in a strictly read-only path. It must not create schema, write cycle evidence, train a model, or infer a prior cycle. If the ledger table is absent and the bootstrap boundary is omitted, readiness fails closed. Once the ledger exists, the stored prior-cycle cutoff is authoritative. `adaptive-train` continues to require the adaptive cycle ledger and therefore remains blocked until migration 0015 is separately authorized and applied.

The adaptive subsystem is repository research infrastructure only. It must preserve `automatic_promotion=false`, must not access any final holdout, must not activate a paper model, and must not enable live trading. Phase 14 remains `PHASE_14_ENGINEERING_COMPLETE_LIVE_GATE_BLOCKED`; `MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0` remain mandatory. Any future paper-model activation, new final-holdout access, Phase 15 progression, geographic bypass, live trading, or nonzero money remains a separate explicit authorization boundary.

### Phase 14 BTC-first V3 Gate A repository checkpoint — 13 September 2026

The separate immutable V3 feature family is `core-v3-btc-native`, with `official-outcome-v1` as the eventual supervised label, 5-minute horizon only, and fixed feature offsets 60, 120, 180, and 240 seconds after market start. Forecast predictors are BTC-native Coinbase spot, Bybit spot, and Bybit linear state only. Polymarket price/book fields are not V3 forecast predictors; they remain downstream executable benchmark/evaluation inputs. Missing or stale BTC evidence remains explicit, and future rows must not change historical features.

Repository Gate A implementation is present on PR #192. The audited pre-handoff implementation checkpoint is `e09e9834260996553fa3cff7c18bf5269e48f4f0` and GitHub Actions CI run `34757241403` passed. Gate A includes deterministic as-of BTC state reads, BTC return/cross-venue calculators, immutable V3 feature materialization, a future-data perturbation regression, and outcome-blind coverage reporting that selects no policy and performs no training. Existing V1/V2 semantics and evidence remain immutable.

This is **repository implementation evidence, not Gate A acceptance**. No production rollout was performed, no production migration/restart occurred, no V2 or V3 training was run, no model or paper activation occurred, no final holdout was accessed, and automatic promotion remains false. The current V2 adaptive training path remains paused even though its historical readiness evidence remains valid. Phase 14 stays in RESEARCH mode with live trading disabled and both money limits at zero.

The immediate research action is to collect and inspect outcome-blind `core-v3-btc-native` coverage and then make a separate Gate A acceptance decision. Training or activation requires a later explicitly authorized plan after Gate A acceptance.


## Phase 14 BTC-first V3 Gate A production acceptance + Gate B preregistration — 13 September 2026

This section supersedes the earlier Phase 14 V3 wording that left Gate A awaiting production coverage acceptance. The repository implementation remains `core-v3-btc-native` with `official-outcome-v1`, a 300-second horizon, and feature offsets 60/120/180/240 seconds.

**Gate A production coverage: PASS.** The accepted evidence is `docs/evidence/phase-14-v3-gate-a-production-20260913.json`: 17 markets / 68 feature rows from `2026-09-13T13:45:00Z` through `2026-09-13T15:10:00Z`, coverage input SHA-256 `32c283a7769681ebe5b2e0d1fe255ad6c38aa5b0301303f8fe86f4e7b2278ffb`, zero future-cutoff violations, zero Polymarket predictor keys, and complete current-state availability for Coinbase spot, Bybit spot, and Bybit linear. The accepted step performed no model training, model activation, paper activation, final-holdout access, automatic promotion, or live-trading change.

**Gate B preregistration is frozen.** The approved design is commit `c9e179c91ea990ca4a25a13f69fc5932811fb32a` and implementation follow-up is Issue #193. The audited implementation checkpoint is `39a887e138398215ac97dc45f8099e3515a90fe2`; CI run `34775202056` passed the full Python suite, lint, deployment-asset validation, health check, dashboard test/typecheck/build, and the PR smoke workflows. The future search contract is frozen and hash-bound, including the predictor family, forecast candidates, validation selection/tie-break rules, calibration candidates, offset candidates, fee/slippage assumptions, edge grid/no-trade candidate, freshness limit, and validation trade/PnL gates.

The two historical contamination sets remain required as hash-bound `diagnosis` and `consumed_v2_final_holdout` exclusion manifests before any labeled research work. The readiness implementation rejects any supplied exclusion condition ID that occurs inside the frozen prospective V3 epoch, preventing post-hoc removal of prospective markets. No historical cohort IDs are invented by this handoff.

The prospective epoch is fixed at `2026-09-13T13:45:00Z` through `2026-09-16T13:45:00Z`. After the epoch completes, the only authorized next sequence is **outcome-blind readiness → fixed five-fold feature-only plan**. No labeled modeling, model fitting, final-holdout label access/evaluation, model activation, automatic promotion, production migration, paper activation, or live-trading change is authorized by this handoff.

## Phase 14 BTC-first V3 Gate B successor runtime implementation — 14 September 2026

This section supersedes the **execution order** in the 13 September V3 Gate B v1 preregistration section while preserving that historical record unchanged. Gate A coverage for `core-v3-btc-native` remains accepted PASS under SHA-256 `32c283a7769681ebe5b2e0d1fe255ad6c38aa5b0301303f8fe86f4e7b2278ffb`, and the historical preregistration design remains `c9e179c91ea990ca4a25a13f69fc5932811fb32a`.

Historical `v3-gate-b-preregister-v1` is retired and non-executable for model/policy selection. Its prospective epoch was `2026-09-13T13:45:00Z` through `2026-09-16T13:45:00Z`. The exact 84-trade diagnosis identities were not durably frozen before that epoch began, so they must never be reconstructed from current database state, later settlements/outcomes, inferred timestamps, approximate windows, or synthetic/empty manifests. The Sep13–16 epoch is engineering, source-availability, leakage, and coverage evidence only. The complete 48 unique consumed-V2 final-holdout condition IDs remain permanently non-reusable historical contamination evidence.

The active successor is `v3-gate-b-preregister-v2` with dataset `supervised-core-v3-btc-native-v1`, feature version `core-v3-btc-native`, label version `official-outcome-v1`, and frozen epoch `[2026-09-16T13:45:00Z, 2026-09-19T13:45:00Z)`. Eligibility is exactly `market_start_at >= epoch_start AND market_start_at < epoch_end`; every pre-epoch market is structurally ineligible for train, validation, test, or final-holdout membership. Historical diagnosis and consumed-V2 identities remain evidence and are not runtime selection inputs. The successor otherwise preserves the v1 predictor set, five candidates, calibration rules, fee/slippage and edge grid, selected-book freshness limit, validation-economics gates, 24h/6h/6h/6h geometry, five ordinary folds, one-market embargo, and 12-hour final reserve.

The repository runtime implementation checkpoint is `659d9524fe8bfeba182b7cf7c8d9b664280f7562` and exact-head CI `34841954823` passed. The runtime exposes outcome-blind readiness and feature-only planning without historical manifest arguments, keeps PostgreSQL transactions read-only, and preserves exclusive/no-clobber plan output. This is repository engineering only: successor readiness has not been run, the feature-only plan has not been written against the prospective epoch, and no outcome/label inspection, model fitting, final-holdout access/evaluation, model/paper activation, production deployment/restart/migration, live trading, automatic promotion, geographic bypass, or money-limit change occurred.

**Current order:** continue prospective `core-v3-btc-native` source/feature collection through `2026-09-19T13:45:00Z`. Do not run successor readiness or planning before epoch close. After close, run only the read-only outcome-blind readiness check; if and only if ready, write the fixed five-fold feature-only no-clobber plan, then stop for a separate review/authorization boundary. No model fitting and no final-holdout access are authorized.

# Current research amendment — 20 September 2026

The one-shot `v3-gate-b-preregister-v2` final holdout has been evaluated and is permanently consumed. Frozen V3 selection remained `single_feature_btc_logistic` at 240 seconds with a 0.075 minimum cost-adjusted edge. The 144-market holdout recorded 84.03% overall accuracy, log loss 0.35419, Brier 0.10944, 20 trades, and +1.654224 one-share-equivalent P&L after the frozen assumed costs. No refit, automatic promotion, model activation, or live trading occurred.

The trade ledger was asymmetric by side: UP trades were 7/10 with +2.636956 P&L, while DOWN trades were 2/10 with -0.982732 P&L. This is diagnostic evidence only. It does not prove a permanent UP advantage, and the consumed V3 holdout must never be used to tune a DOWN veto, confidence threshold, edge threshold, regime rule, model, or calibration.

The active next research challenger is separately versioned `core-v4-regime-aware`. It remains BTC-native and adds timestamp-coherent 5m, 15m, and 60m market context from Coinbase spot, Bybit spot, and Bybit linear. V4 must report model and economic performance separately across bull, bear, sideways/mixed, and unknown regimes. Any V4 Gate B model/policy selection must use a new prospective cohort frozen after the V4 implementation/collection boundary.

Repository implementation does not authorize production V4 materialization, deployment/restart, model training, paper activation, automatic promotion, Phase 15, live trading, geographic bypass, or nonzero money limits.

## Phase 14 V4 prospective feature collection authorization — 20 September 2026

Production materialization and continuous collection of `core-v4-regime-aware` features is authorized as a research-only data operation.

The immutable prospective collection boundary is `2026-09-20T12:40:53Z`. The collector may only process completed 5-minute markets whose `market_start_at` is at or after that timestamp, and it may only create/preserve the fixed 60/120/180/240-second V4 feature rows.

The collector is outcome-blind: it does not read outcome labels for feature construction or selection, does not train/calibrate a model, does not choose thresholds or policy, and does not access any future final holdout. Polymarket prices/books remain excluded from the V4 forecast features.

Production installation is intentionally isolated from the deployed application checkout. The V4 collector must run from a versioned runtime under `/var/lib/bp/runtime` using the existing production Python environment, leave `/opt/bp` unchanged, and not restart the recorder. Safety remains RESEARCH mode with live trading disabled and both money limits at zero.

This authorization ends after successful initial materialization, timer enablement, and durable rollout evidence. Model fitting and V4 Gate B preregistration remain later, separate research boundaries.

## Phase 14 V4 forward collector production PASS — 20 September 2026

The isolated `core-v4-regime-aware` forward collector is now production PASS and active in RESEARCH mode. Candidate head `36b02d0687194173ab5d3862d3b88c6c90607574` runs from `/var/lib/bp/runtime/v4-forward-36b02d0687194173ab5d3862d3b88c6c90607574`; the deployed `/opt/bp` checkout remained unchanged at `7c3af78da1922a0e5187c24b799951130cc98887`, and the recorder was not restarted.

The accepted first cycle at `2026-09-20T13:35:12.316825Z` processed 9 prospective completed 5-minute markets and inserted 36 immutable feature rows. Regime coverage was 2 bull, 0 bear, 7 sideways/mixed, and 0 unknown. Future-cutoff violations, Polymarket predictor keys, and regime-invariant violations were all zero. No training, policy selection, automatic promotion, activation, live trading, or nonzero money occurred.

This is collection/operational evidence only. It does not establish V4 forecast quality or profitability. Continue prospective collection until the cohort has meaningful bull, bear, and sideways/mixed representation. Before any model fitting, freeze a new V4 Gate B preregistration and untouched final holdout.

## Phase 14 frozen V3 zero-real-money paper authorization — 20 September 2026

The user explicitly authorized prospective paper activation of the already-frozen V3 Gate B successor. The authorized artifact is SHA-256 `124627e15cab3997b8abe54ec5237450d976ab5682953f45a1399a76b6dae0e7`, selected under `v3-gate-b-preregister-v2` as `single_feature_btc_logistic` at 240 seconds. Its economic policy remains `trade_threshold` with minimum cost-adjusted edge 0.075, fee coefficient 0.07, slippage buffer 0.01, and selected-book freshness limit 10 seconds.

This authorization does not reopen the consumed V3 holdout and does not permit any refit, recalibration, threshold tuning, side filter, or policy modification. Polymarket remains execution-only for V3: BTC-native features produce the forecast; the recorded selected-side book determines executable price and edge.

The paper epoch is prospective from its production activation timestamp. Pre-activation markets cannot be backfilled into it.

V3 paper evidence is isolated from legacy paper evidence using prediction version `v3-frozen-paper-v1` and execution version `paper-execution-v3-frozen-v1`. The legacy paper worker must exclude the V3 version while preserving its prior behavior for other research prediction versions. V3 virtual cash is derived only from V3 execution-version fills/settlements.

The paper simulation uses the established Phase 12 defaults: $100.00 virtual starting cash, $5.00 virtual target notional per trade signal, 250ms simulated latency, and 2000ms order TTL. These are paper execution assumptions, not V3 model-selection parameters.

Real money remains exactly zero. Production controls remain RESEARCH mode, live trading disabled, maximum real trade size zero, and maximum real daily loss zero. No wallet/signing/order-placement path is authorized. V4 regime-aware prospective feature collection continues in parallel.

## Phase 14 frozen V3 paper production PASS — 20 September 2026

The exact frozen V3 Gate B successor was accepted for prospective isolated paper activation. Production activation passed at `2026-09-20T15:39:45Z` on candidate `9d52eb753355365848a637ffa6663928664bf770`. That activation PASS remains historical evidence; this section does not assert current service liveness. Operational runtime state is governed by `PROJECT_STATE.json`. After the 21 September storage fail-closed event, recorder/frozen-V3 must be treated stopped until a later accepted recovery gate proves otherwise.

The accepted model artifact SHA-256 remains `124627e15cab3997b8abe54ec5237450d976ab5682953f45a1399a76b6dae0e7`. Prediction identity is `v3-frozen-paper-v1`; execution identity is `paper-execution-v3-frozen-v1`. The frozen strategy remains `single_feature_btc_logistic` at 240 seconds with `trade_threshold`, minimum cost-adjusted edge 0.075, fee coefficient 0.07, slippage buffer 0.01, and selected-book freshness limit 10 seconds.

Paper execution assumptions remain frozen at $100.00 virtual starting cash, $5.00 virtual target notional, 250ms simulated latency, 2000ms order TTL, and 6-decimal share precision. Real money is exactly $0.00.

Activation verification found zero pre-activation V3 predictions and zero invalid V3 order sources. Initial V3 prediction/order/fill counts were all zero because activation completed before the first eligible post-activation decision point. The deployed `/opt/bp` checkout remained unchanged at `7c3af78da1922a0e5187c24b799951130cc98887`, the recorder PID remained `2113524`, and the recorder was not restarted.

The V3 predictor, V3 paper executor, legacy paper service, prospective-outcome service, and V4 forward collector were all active at acceptance. V4 regime-aware collection continues in parallel with V3 paper observation.

This paper epoch is observational evidence only. It must not be used to refit V3, recalibrate it, tune thresholds, alter paper sizing, or justify automatic promotion. Live-order access, live trading, geographic bypass, Phase 15, and nonzero real-money limits remain blocked.

Durable repository evidence: `docs/evidence/phase-14-v3-frozen-paper-production-20260920.json`.
Host evidence: `/var/lib/bp/evidence/phase14-v3-frozen-paper-9d52eb753355-20260920T154016Z.json`.

## Phase 14 V4 comprehensive successor scope — 20 September 2026

V4 is not limited to regime classification. It is the comprehensive successor research program for the weaknesses documented in V3.

The future V4 Gate B preregistration must explicitly address:

- robustness across bull, bear, and sideways/mixed regimes;
- UP/DOWN side asymmetry;
- V3's selection of a single-feature logistic model despite a broader BTC-native feature set;
- calibration robustness overall and by regime/side;
- whether 60/120/180/240-second decision timing should remain or change;
- the trade-quality versus coverage frontier rather than maximizing trade count;
- loss, drawdown, losing-streak, and profit-factor robustness;
- execution availability/freshness separately from forecast quality.

The existing V4 collector remains unchanged because it already preserves the V3 BTC-native short-horizon family and adds 5m/15m/60m context and regime fields. Changing the collector mid-epoch is not required for these objectives and would weaken comparability.

V4 Gate B preregistration v1 is now frozen before any V4 selection labels are read. The wholly future selection epoch is `[2026-09-23T00:00:00Z, 2026-09-30T00:00:00Z)`, so all earlier V4 rows — including the 22 September 373-market / 1,492-row observation cohort — remain engineering/source-availability/regime-coverage evidence only and are structurally ineligible for model or policy selection. The frozen contract is `docs/superpowers/specs/2026-09-22-phase-14-v4-gate-b-preregistration.md` with durable evidence at `docs/evidence/phase-14-v4-gate-b-preregistration-20260922.json`. It preregisters the simple baseline, short-context and full multivariate BTC-native logistic candidates, full V4 XGBoost nonlinear challenger, identity/Platt calibration, all four timing offsets, the existing finite edge grid plus `no_trade`, seven chronological ordinary folds, mandatory regime/side/risk/execution reporting, and a final untouched 24-hour holdout. Only outcome-blind readiness and feature-only no-clobber planning are implemented/authorized; no labeled preparation, model fitting, policy selection, final-holdout access, activation, automatic promotion, Phase 15, live trading, or nonzero money is authorized.

The consumed V3 final holdout is motivation only. Its numerical results must not be used to set V4 thresholds, side filters, model hyperparameters, calibration values, or acceptance criteria.



## 23 Sep 2026 — Same-day frozen-V3 canary-readiness contract

The project now has a separately versioned `phase15-v3-canary-readiness-v1` read-only statistical audit. Its rules were frozen before any new prospective calibration intercept/slope diagnostics were read. It does not mutate V3, does not authorize live trading, and does not alter the V4 future Gate B collection boundary.

For **live-paper sample sufficiency**, retain the accepted Phase 13 principle that no fixed magic market/trade count is required. The row may pass only when the deterministic prospective frozen-V3 mean realized after-cost paper P&L 95% lower confidence bound is strictly positive.

For **walk-forward stability**, the evidence stack is the pre-registered five-fold V3 Gate B ordinary validation economics gate, the untouched V3 final holdout, and the later prospective paper cohort. The V3 Gate B v2 contract required at least 8 validation trades per ordinary fold, at least 4/5 nonnegative validation folds, and positive aggregate validation P&L. Its implementation replaces the final edge policy with `no_trade` whenever that ordinary economics gate fails. The immutable frozen selection is instead `trade_threshold`, `min_edge=0.075`; therefore the pre-registered ordinary validation economics gate passed. The Master row may pass only if that fact, positive untouched-holdout after-cost P&L, and positive prospective mean-P&L uncertainty all hold.

For **calibration acceptance**, the new reliability rule is frozen before reading the relevant diagnostics. Prospective calibrated Brier mean must be no worse than the frozen final-holdout Brier `0.10943703117284813`; prospective calibrated log-loss mean must be no worse than the frozen final-holdout log loss `0.35419212970900277`; and deterministic 2,000-resample, seed-15 bootstrap 95% intervals for calibration intercept and slope must contain `0` and `1` respectively. Ten-bin ECE is descriptive only. If this audit fails, do not weaken the rule.

The production runner is `bash scripts/deploy/phase15_v3_accelerated_readiness_cloudshell.sh`. It is a PostgreSQL read-only observation bridge only. It creates no production file, changes no checkout, changes no service/timer, reads no wallet/signing material, creates no authenticated client, submits no order, and leaves `LIVE_TRADING_ENABLED=false` with real-money limits zero.

Geographic eligibility is still a separate hard gate. User-provided direct official geoblock evidence from the ordinary physical network on 23 Sep 2026 reports `blocked=false`, country `NG`, region `LA`; no IP address is persisted in source truth. The latest production execution host direct check remains blocked from `US/SC`. Therefore geographic status is **partial pass only**. No VPN/proxy/tunnel or other location-circumvention path is permitted. Before any live canary, both the user's ordinary physical connection and the actual execution host must be directly unblocked.

Until a fresh accelerated audit passes and execution-host geography is independently unblocked, the complete Master live gate remains blocked, Phase 15 production activation remains prohibited, and real-money limits remain zero.


## 23 Sep 2026 — Accelerated frozen-V3 statistical gate PASS; execution-host geography remains

The read-only `phase15-v3-canary-readiness-v1` audit completed on exact main `ceeec0bded4bb6ae60291ee8f5f60db214eceb98`. Durable evidence is `docs/evidence/phase-15-v3-accelerated-readiness-production-20260923.json`.

The predeclared statistical mapping passed all targeted rows:

- `walk_forward_results_stable_enough=pass`;
- `sufficiently_large_live_paper_sample_with_uncertainty=pass`;
- `positive_after_cost_profitability=pass`;
- `calibration_acceptable=pass`;
- `order_execution_and_reconciliation_tested=pass`.

The run observed 77 settled frozen-V3 paper trades (48 wins / 29 losses), realized after-cost paper P&L `687.612927164917` USD, profit factor `6.303923587614901`, and a deterministic bootstrap 95% interval for mean realized P&L of `[1.746626049710839, 18.521917124071315]` USD. The calibration audit covered 533 prospective evaluations; Brier/log loss remained below the frozen holdout reference, the calibration-intercept 95% interval `[-0.35298851177376006, 0.19023844953087868]` contains 0, and the slope interval `[0.8813085772634305, 1.2937796253992961]` contains 1.

Explicit user live authorization remains `pass`. The user’s ordinary physical-network official geoblock check is `blocked=false`, `NG/LA`; the IP address is not persisted. The current execution host remains `blocked=true`, `US/SC`. Therefore `geographic_compliance_eligible=fail`, overall live gate remains `fail`, Phase 15 production activation remains prohibited, and real-money limits remain zero.

The next authorized mutation is only the source-truth-bound Johannesburg candidate-host probe in `scripts/deploy/phase15_v3_canary_host_probe_cloudshell.sh`. It requires explicit billable-VM acknowledgement, may create only `bp-v3-canary-exec` in `africa-south1-a` as `e2-micro`, installs no trading software or wallet/signing material, and performs only the direct official Polymarket geoblock check. A blocked/error response requires deletion. An unblocked PASS is evidence for a later canary-deployment decision, not automatic live activation.


## 23 Sep 2026 — Master live gate PASS and one-order frozen-V3 canary authorization

The dedicated execution candidate `bp-v3-canary-exec` in GCP `africa-south1-a` completed the direct official Polymarket geoblock probe with `blocked=false`, country `ZA`, region `GP`. Durable sanitized evidence is `docs/evidence/phase-15-v3-canary-host-geoblock-20260923.json`. The user's ordinary physical-network check separately remains `blocked=false`, `NG/LA`. No VPN, proxy, tunnel, or other geographic-circumvention path is permitted.

The accelerated frozen-V3 statistical audit had already passed walk-forward stability, sample sufficiency with uncertainty, positive after-cost profitability, prospective calibration, and execution/reconciliation under rules frozen before the relevant diagnostics were read. Historical reproducibility, leakage controls, chronological splits, risk/kill-switch engineering, and explicit user authorization also pass. Therefore every Master live-gate row is now `pass`, and Phase 15 is permitted **only within the canary contract below**.

### Frozen canary sizing and risk

The user explicitly authorized risk of up to **$10 per market**. Treat `$10` as a hard ceiling, not a target. The exact frozen-V3 strategy keeps its existing `$5` target notional for the first canary.

```text
policy_version = v3-live-canary-v1
strategy_target_notional_usd = 5
max_trade_size_usd = 10
max_total_exposure_usd = 10
max_daily_loss_usd = 10
max_consecutive_losses = 1
max_accepted_orders = 1
max_submission_attempts = 1
min_edge = 0.075
min_liquidity_usd = 5
max_spread = 0.10
max_prediction_age_seconds = 30
min_time_to_expiry_seconds = 15
order_ttl_seconds = 2
```

This authorization does not change V3's model artifact, feature set, calibration fit, 240-second timing, `min_edge=0.075`, or paper sizing. It does not change V4.

### Execution isolation

The existing US BP host remains the recorder, database, prediction, paper-execution, risk, and audit host. It must never store the Polymarket private key and must never make an authenticated Polymarket order submission.

The Johannesburg VM is execution-only. Wallet/signing material may exist only there as root-only runtime configuration and must never be committed, echoed, pasted into chat, or copied to the US host.

Only NEW frozen-V3 paper orders created after a canary activation timestamp may be considered. Historical paper trades may not be replayed into real money.

### Manual one-shot launch boundary

Real-money submission is deliberately not automated.

To avoid losing the short frozen-V3 armable window to chat latency, the supported operator fast path is `scripts/deploy/phase15_v3_canary_interactive_operator_cloudshell.sh`. It runs only from an interactive Cloud Shell terminal and composes the existing safety helpers without changing their gates. The operator must type `RECONCILE`, `START`, `ARM`, and `SUBMIT` at the applicable exact steps; there is no unattended arm or submission. Before the one executor submission call it creates a local per-intent attempt marker, and after the call it re-engages the kill switch. Missing, malformed, ambiguous, or unbound submission output stops the workflow with `DO_NOT_RETRY=true`; only a valid bound result is sent to the existing record helper. This fast path does not change the frozen V3 model, target, risk limits, TTL, one-network-attempt rule, or prohibition on a second order.

1. Wallet bootstrap may install the pinned official SDK and signer material on Johannesburg, but leaves `/etc/bp-canary/KILL` engaged and submits no order.
2. Canary preparation first requires a fresh official-account preflight from Johannesburg: zero official open orders and at least $5 collateral. It reuses the exact frozen $5 paper request, requires at least $5 of fresh displayed selected-side ask liquidity, applies the existing live-risk engine, creates an initial zero-order reconciliation baseline only after the official account is verified clean, persists risk evidence and the live intent, and submits no order. With separate explicit authorization, the polling portion of this prepare step may run for at most two hours as a prepare-only sidecar on the existing US recorder. That sidecar must run in research/live-disabled/zero-money mode, contain no wallet material, use localhost-only network access, remain disabled across VM reboot, and have no arm or submission path. It may write only durable prepare-watch state plus the same prepared payload. A Cloud Shell status helper may materialize that payload for manual arm only while at least 20 seconds remain to market end and the watcher-start commit is still binding-equivalent to current `main` across the prepare runner, current live-risk/canary modules, sidecar unit, arm helper, and executor. Documentation/evidence-only commits may advance `main` without invalidating the watcher. Any change to those bound runtime/execution files fails closed and requires the normal closed-before-submission reconciliation.
3. Explicit arm requires `PHASE15_ACCEPT_REAL_MONEY=yes`, validates the prepared $5 request against the $10 ceilings and Master gate, and binds a short-lived activation manifest (at most 45 seconds) to the exact intent ID, prediction ID, paper-order ID, canonical request SHA-256, and executor SHA-256. It rechecks official-account cleanliness/collateral before removing the kill switch. Arming submits no order.
4. The user manually sends only the prepared payload to the Johannesburg executor. The executor rechecks direct geoblock, exact activation/request/executor binding, zero official open orders, and at least $5 collateral. It independently rejects a target other than the frozen $5 canary target or gross notional above $10 and **atomically re-engages the kill switch before the SDK submission attempt**, consuming the arm. It uses the pinned SDK's direct `post_order()` path, not the higher-level allowance-recovery placement helper, so the canary performs only one submission POST. It then attempts cancellation after two seconds.
5. The sanitized result may be recorded only when its intent, authorization, request SHA-256, executor SHA-256, ZA geoblock evidence, and official-account preflight match the prepared contract. Stop after the first network submission attempt, whether accepted, rejected, or ambiguous.

Exactly one network submission attempt is authorized. No second order or retry is authorized. Official order/fill reconciliation is mandatory before a later decision can authorize another live action. Missing, malformed, or ambiguous submission/cancellation evidence fails closed and must never trigger a retry.

A profitable first canary does not authorize automatic promotion, stake growth, V3 tuning, or any V4 change. V3 paper observation and V4 Gate B collection continue in parallel.


### 24 Sep 2026 — Phase 15 transient live-risk retry correction (production active)

Investigation of the paper-vs-live canary path found a separate retry-semantics defect in the current Phase 15 prepare logic. A new frozen-V3 paper order is evaluated against current live liquidity. That live liquidity is time-varying, but the current candidate selector treats any prior `v3-live-canary-v1` risk decision for a prediction as terminal. Therefore a first-poll `liquidity_missing` or `liquidity_below_minimum` result can permanently blacklist that otherwise valid paper trade even if fresh eligible depth appears a few seconds later while the prediction remains within the 30-second live freshness window.

The engineering correction permits re-evaluation only when **every prior risk decision for that prediction failed solely for transient reasons**: `liquidity_missing`, `liquidity_below_minimum`, or `api_unhealthy`. Every risk decision remains append-only and auditable. Any eligible decision or any non-transient failure — including stale prediction, spread, expiry, cooldown, exposure/loss, reconciliation, interlock, signal, or immutable source-request failure — remains terminal and fail-closed. The live intent natural key remains one intent per prediction/policy, the $5 frozen request and 0.075 edge threshold remain unchanged, and the one-network-attempt authorization remains unchanged.

The user explicitly authorized production rollout of main `562cb0eacae283a2916cbb9201d0bbead272d684` and restart of the prepare-only watcher. The production start passed under run `phase15-prepare-watch-20260924T112126Z-562cb0ea`; the service is active on `bp-recorder` with helper head `562cb0eacae283a2916cbb9201d0bbead272d684`, research mode/live-trading-disabled safety remains in force, arm automation and submission automation remain disabled, no real order has been submitted, and the single network submission attempt remains unused. Evidence: `docs/evidence/phase-15-transient-risk-retry-rollout-production-20260924.json`.

The previously authorized watcher subsequently caught a genuine frozen-V3 $5 paper order and produced an armable prepared intent for prediction `a2d00304828642b6b879728df183f8834127972d820845cf3c903d95c458ddec`. No real order was submitted and no submission attempt was consumed. The market expired before manual arm/submission, after which the intent was reconciled as `closed_before_submission` under reconciliation `live-reconciliation-d47db6426a7af07b8873aefb70696509` with the kill switch engaged. That watcher is now inactive and its persisted intent is closed; the single authorized network submission attempt remains unused. Evidence: `docs/evidence/phase-15-v3-canary-third-unsubmitted-reconciliation-20260924.json`.

The corrected production watcher later produced another valid $5 frozen-V3 prepared intent, `live-intent-58b4e879fea6d65863692d12015daa3d`, for prediction `7875873c17857b4b9b8d3a9de1e6c9a2198b996ae7e362f273358946cdfea81a` with side `down`, limit `0.55`, and requested shares `8.81329`. It was armable when captured with about 46 seconds remaining, but no arm occurred and the market expired before manual submission. The intent was then reconciled as `closed_before_submission` under `live-reconciliation-ca7771fe214d2e8c96caa3e9af22b371`; the kill switch remained engaged, no real order was submitted, and the one authorized network submission attempt remains unused. The watcher run is inactive/reconciled. Evidence: `docs/evidence/phase-15-v3-canary-fourth-unsubmitted-reconciliation-20260924.json`.

A later prepare-only watcher run, `phase15-prepare-watch-20260924T123032Z-3f93b23c`, produced another valid $5 frozen-V3 prepared intent, `live-intent-53c441103093808cb9e17a1009474cc5`, for prediction `31bb75d702285ccdf5f1967c91a6d44ceab17c297d70fbce586c790ef0d0732b` with side `up`, limit `0.70`, and requested shares `6.995942`. It was armable when captured with about 42 seconds remaining, but no arm occurred and the market expired before manual submission. The first authorized reconciliation completed before Cloud Shell disconnected. After reconnect, rerunning the same idempotent reconciliation helper returned `already_reconciled` for `closed_before_submission`, with the kill switch engaged, no live order submitted, and the authorized network submission attempt still unused. The original reconciliation ID was not observable after reconnect and is deliberately not invented. Evidence: `docs/evidence/phase-15-v3-canary-fifth-unsubmitted-reconciliation-20260924.json`.

The repeated ~42–46 second prepared windows are consistent with the frozen V3 decision schedule rather than a missing early signal: V3 is fixed at 240 seconds into a 300-second market, so the scheduled prediction itself occurs only 60 seconds before market end. The timing-latency hardening is now production-active under explicit user authorization. It preserves that exact 240-second strategy timing while reducing avoidable operational polling: frozen-V3 paper execution from 5 seconds to 1 second and the persistent prepare-only watcher default from 2 seconds to 0.5 seconds. Prepared reports also expose timing diagnostics from scheduled prediction through canary preparation: prediction lateness, combined post-prediction-to-prepare latency, scheduled-window consumption, and time remaining to market end. Existing paper-order timestamps are semantic signal timestamps rather than physical insert timestamps, so the paper-executor persistence and watcher-pickup portions are deliberately not claimed as separately measured. No model, calibration, edge, sizing, live-risk, arm, kill-switch, or submission rule changes. Any production rollout of this hardening remains a separate explicit authorization boundary. The production path for the paper-executor portion is the exact-SHA helper `scripts/deploy/phase15_v3_timing_latency_rollout_cloudshell.sh`, which derives from the accepted hotpath runtime, replaces only the V3 paper-execution runner, restarts only that service, preserves recorder/predictor PIDs, validates zero-money safety and cycle timing, and rolls back on failure. The prepare-only watcher is then started separately under its own existing authorization gate.

Production confirmation on 24 September 2026 shows the 1-second paper-executor runtime `/var/lib/bp/runtime/v3-paper-timing-c1e8166c463dd0111098ca29e43cd8a106d11939` already current and the separately authorized 0.5-second prepare-only watcher run `phase15-prepare-watch-20260924T153537Z-aede7c4b` active on `bp-recorder`. Live trading remains disabled on the watcher, no arm has been attempted, no real order has been submitted, and the single authorized network submission attempt remains unused. Evidence: `docs/evidence/phase-15-timing-latency-production-confirmation-20260924.json`.

A timing-hardened watcher run, `phase15-prepare-watch-20260924T153537Z-aede7c4b`, subsequently prepared fresh frozen-V3 intent `live-intent-5c67f093c59fd096d82e8669f9ac8ede` for prediction `78a3e47ad16c647f5142a299d332ddc8bfd06e18328c3780176b3e963e104c9b` and paper order `d85457eed5aea90d2f21ddb7ff9cff8ac786ad525c87933a864f2d8588806fcf`. The candidate was side `down`, limit `0.38`, requested shares `12.610594`, and was prepared with `56.701654` seconds remaining—only `3.298346` seconds after the scheduled 240-second decision point. The later operator check occurred after expiry, so the intent was not armed. Its persisted prepared payload was recovered read-only from the recorder, validated against the exact IDs, and reconciled as `closed_before_submission` under `live-reconciliation-10f13c5449219daaf7934630bcc2818a`. The kill switch remained engaged, no live order was submitted, and the single authorized network submission attempt remains unused. Evidence: `docs/evidence/phase-15-v3-canary-sixth-unsubmitted-reconciliation-20260924.json`.
