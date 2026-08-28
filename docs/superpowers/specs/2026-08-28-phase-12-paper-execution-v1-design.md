# Phase 12 Paper Execution V1 Design

## Goal

Add an auditable paper broker for the accepted 5m/15m live-prediction signals so the project can measure what would actually have filled, what it would have cost, and what the resulting paper P&L/drawdown would have been before any real-money path exists.

## Non-negotiable boundaries

- Current mode remains `RESEARCH`; `LIVE_TRADING_ENABLED=false` and real-money trade-size/daily-loss limits remain zero.
- Phase 12 contains no wallet, signer, allowance, credential, private-key, Polymarket order-placement, or real-order-cancellation code.
- The paper executor consumes only immutable `live_predictions`; it cannot create a paper order from a prediction whose stored `trade` flag is false or whose selected side/price evidence is unavailable.
- Every paper order, fill, terminal event, and settlement is append-only and cryptographically tied to its source prediction/config/evidence.
- Fill prices come from recorded executable Polymarket book levels at simulated arrival time. No midpoint fills, synthetic depth, or future-looking snapshots are allowed.
- Missing/ambiguous book evidence means no fill, not a guessed fill.
- Settlement uses only append-only official outcome evidence after resolution.
- Paper results are research evidence, not a profitability claim and not authorization to trade live.

## Architecture

Phase 12 introduces a generic execution contract plus a paper implementation:

1. `bp_engine.execution.models` defines immutable order/fill/terminal/settlement data contracts and execution request/result types that a later live adapter can implement without changing strategy callers.
2. `bp_engine.execution.protocol` defines the minimal execution gateway interface: submit an order request, cancel/expire an outstanding remainder, and observe a deterministic result. Phase 12 provides only `PaperExecutionGateway`; there is no live implementation.
3. `bp_engine.execution.book` reconstructs selected-token Polymarket books from immutable `raw_market_events`. It anchors on the latest full `book` event at or before the simulated arrival timestamp, then applies eligible `price_change` events in received-order up to that timestamp. It exposes ordered ask/bid levels plus exact raw-event provenance.
4. `bp_engine.execution.paper` simulates latency, limit-price protection, depth consumption, partial fills, fees, slippage, order expiry/cancellation, and cash constraints without mutating market evidence.
5. `bp_engine.execution.repository` stores append-only paper ledgers in PostgreSQL and enforces idempotent natural keys/semantic hashes.
6. `bp_engine.execution.service` polls immutable `live_predictions`, creates at most one paper order per eligible prediction, advances outstanding orders only with evidence available by each simulation timestamp, and settles filled positions only when an official evaluation/label exists.
7. The Phase 11 dashboard read model adds paper account, order, fill, open-position, settlement, P&L, and reconciliation data. Until the Phase 12 service has valid evidence, missing metrics remain explicit rather than zero-filled.

## Generic execution contract

The strategy/prediction layer hands the execution layer an `ExecutionOrderRequest` containing:

- immutable `prediction_id` and `prediction_semantic_sha256`;
- condition/market/token identity and selected side;
- action `BUY` in V1;
- requested shares and target notional;
- submitted timestamp;
- maximum acceptable price (`limit_price`);
- time-in-force/expiry timestamp;
- execution configuration version/hash.

The gateway returns evidence objects rather than mutating the signal. A future live gateway must implement the same request semantics, but Phase 12 deliberately ships no network/order methods capable of spending money.

V1 does not implement speculative early exits. Filled shares are held to official market resolution and settled to `$1` per winning share or `$0` per losing share. Early-exit hypotheses belong in Phase 13, where they can be compared without rewriting the Phase 12 baseline.

## Paper account and sizing scenario

V1 uses a simple, explicit scenario so execution quality and bankroll path can be measured without pretending Phase 14 risk management already exists:

- `PAPER_STARTING_CASH_USD=100.00` default;
- `PAPER_TARGET_NOTIONAL_USD=5.00` default per eligible prediction;
- never borrow paper cash;
- first compute the target shares as `target_notional / submitted selected ask`;
- then cap shares by `available_cash / worst_case_unit_cost`, where `worst_case_unit_cost = limit_price + fee_rate * limit_price * (1 - limit_price)`;
- round the final share quantity down deterministically to the configured share precision;
- if the resulting quantity is zero, record `INSUFFICIENT_PAPER_CASH` rather than creating a fill;
- no martingale, loss chasing, Kelly sizing, confidence scaling, or automatic stake increases in Phase 12.

Because the edge-engine fee formula is monotone in total unit cash cost over the allowed probability/fee range, this cap reserves enough cash for any fill price at or below the order limit and prevents negative paper cash by construction.

These values are paper-research scenario parameters only. They do not alter the real-money limits, which remain zero.

## Latency and order timing

For each eligible immutable prediction:

- `submitted_at` is the prediction's `recorded_at` (the earliest time the complete decision was actually persisted), never the earlier scheduled timestamp;
- `arrival_at = submitted_at + PAPER_LATENCY_MS`;
- V1 default `PAPER_LATENCY_MS=250` and records the exact configured value with every order;
- `expires_at` is the earliest of `arrival_at + PAPER_ORDER_TTL_MS` and the market end;
- V1 default `PAPER_ORDER_TTL_MS=2000`.

The simulator may use only raw recorder events with `received_at <=` the simulated observation time. This keeps the replay causal and reproducible.

## Limit price and slippage protection

The immutable live signal already contains the selected-side best ask and the Phase 9 slippage buffer. Phase 12 converts that into an explicit buy-limit cap:

`limit_price = min(1.0, selected_ask + slippage_buffer)`

At arrival, the order walks only recorded ask levels priced `<= limit_price`, from cheapest to most expensive. It never fills above the cap. The actual volume-weighted average fill price is derived from those consumed levels; the difference from the signal ask is measured as realized slippage rather than assumed slippage.

## Full-book reconstruction

Compact `market_state_1s` is insufficient for fill simulation because it stores best bid/ask and aggregate bid/ask depth rather than all price levels. The paper book reader therefore uses `raw_market_events`:

- locate the most recent full Polymarket `book` event for the selected token/condition whose `received_at` is not later than the requested replay time;
- initialize exact bid/ask level maps from that payload;
- apply later `price_change` rows in `(received_at, id)` order through the replay time, updating/removing levels according to recorded side/price/size;
- reject replay if no full-book anchor exists, payload semantics are invalid, token identity conflicts, or provenance ordering is ambiguous;
- return source event IDs/dedupe keys and replay cutoff so each fill can prove exactly which market evidence produced it.

Raw book history is used only within the recorder's verified retention/evidence window; unavailable history is reported as unavailable and never synthesized.

## Fill model

A paper order can produce zero, one, or multiple immutable fills.

For each eligible ask level at arrival:

- available shares are the recorded size at that level;
- fill shares are the smaller of remaining requested shares and available shares;
- the level is never allowed to contribute more than its recorded depth;
- multiple levels create separate fill rows so depth consumption is auditable;
- the simulated order does not modify the recorder's market book or pretend our paper order influenced later prices.

If the requested quantity is not fully filled at arrival, the remainder stays outstanding until the next eligible recorded book change, cancellation, TTL expiry, or market end. Each subsequent attempt uses only evidence available by that simulated timestamp and never reuses already-consumed depth from the same book state for the same order.

This is a conservative single-agent simulation, not a queue-position model. V1 assumes displayed depth at a reached level is available when observed, but records this assumption explicitly so Phase 13 can test harsher queue/fill models.

## Fees and costs

Phase 12 records two cost concepts separately:

1. **signal assumptions** — the stored Phase 9 `fee` and `slippage_buffer` used to decide whether the signal was worth trading;
2. **paper realized execution** — actual replayed fill price/slippage plus a deterministic paper fee computed from the frozen signal's `edge_config.fee_rate` using the same per-share fee formula used by the edge engine.

No current web fee schedule is silently substituted into old signals. If a future fee model changes, it receives a new execution-config version and is evaluated prospectively rather than rewriting prior paper fills.

## Immutable storage

Add four append-only ledgers:

### `paper_orders`

One natural order per eligible `prediction_id` and execution-config version. Stores order identity, source prediction hash, market/token/side, requested shares/notional, submitted/arrival/expiry timestamps, limit price, scenario config/provenance, and semantic hash.

### `paper_fills`

Zero or more rows per paper order. Stores fill timestamp, shares, price, gross cost, fee, total cost, signal-ask slippage, raw-book replay cutoff/provenance, and semantic hash. Fill natural keys prevent duplicate processing on retries.

### `paper_order_terminal_events`

At most one terminal row per paper order with `FILLED`, `CANCELLED`, `EXPIRED`, `MARKET_ENDED_UNFILLED`, or `INSUFFICIENT_PAPER_CASH`, remaining shares, event timestamp/reason, and semantic hash. Status is derived from immutable order + fills + terminal event rather than updated in place.

### `paper_settlements`

One settlement per filled paper order and official label version. Stores official outcome provenance, filled shares, total fill cost/fees, payout, realized P&L, settled timestamp, and semantic hash. It cannot exist before the official append-only outcome evidence.

All repositories are idempotent: a repeated identical write is accepted as existing; a natural-key collision with different semantic content fails closed.

## Reconciliation invariants

Host/CI acceptance must prove:

- every paper order references an existing immutable prediction with matching semantic SHA-256;
- every paper order originates from `trade=true`, `executable=true`, a selected side, and a valid selected ask;
- no prediction creates more than one order for the same execution-config version;
- fill quantities never exceed requested quantity or recorded level depth;
- fill prices never exceed the order limit;
- fill timestamps are not before simulated arrival and not after expiry/market end;
- raw-event provenance is at or before the fill/replay cutoff;
- total paper cash cannot become negative;
- settlement occurs only after official outcome evidence and payout matches the selected side outcome;
- paper ledger rows are never mutated after insertion;
- rerunning the service is idempotent and produces no duplicated fills/settlements;
- no network method capable of placing or cancelling a real order exists in the Phase 12 package.

## Dashboard contract

The snapshot API changes `paper_execution_available` to `true` only when Phase 12 schema/service support is installed. `execution_available` remains `false` for real money.

`paper_pnl` becomes an evidence object containing, where available:

- starting cash;
- current cash;
- capital tied in open filled positions;
- realized P&L;
- unrealized value only when supported by a fresh observed bid, otherwise `null`;
- settled trade count, open position count, fill count, no-fill/expired count;
- return on starting paper cash;
- maximum realized-equity drawdown;
- execution slippage and fee totals;
- reconciliation status.

The UI adds paper orders/fills/settlements and account diagnostics while retaining the persistent RESEARCH/live-disabled safety banner. It does not add buttons capable of submitting, cancelling, or changing orders.

## Runtime/deployment

Phase 12 adds a money-disabled `bp-paper-execution.service` that runs as the unprivileged `bp` user beside the recorder and dashboard. It reads the existing database/environment, writes only the Phase 12 append-only paper ledgers, and has no wallet/network trading credentials.

Host acceptance runs an isolated exact-head candidate first, verifies deterministic/idempotent paper processing against prospective immutable signals and recorder evidence, proves recorder/dashboard continuity, then installs the accepted worker only after the host gate passes.

The permanent dashboard remains localhost-only. No public ingress is added.

## Acceptance

Phase 12 is complete only when:

- CI and PostgreSQL integration tests cover order eligibility, book replay, latency, depth walking, partial fills, limits, fees, expiry/cancellation, cash constraints, idempotency, settlement, and immutable reconciliation;
- production-host acceptance observes prospective paper processing on accepted 5m/15m signals without any real order side effect;
- paper ledger reconciliation has zero integrity violations;
- the dashboard exposes evidence-backed paper execution/P&L without direct database access;
- rerunning the candidate produces no duplicate semantic events;
- `LIVE_TRADING_ENABLED=false`, real trade limits remain zero, and no wallet/order client exists.

Phase 13 may then test better execution assumptions, sizing, features, models, and exit hypotheses using these immutable paper results. Phase 14 remains the first phase allowed to add live-trading infrastructure, and actual live launch remains separately gated and explicitly authorized.
