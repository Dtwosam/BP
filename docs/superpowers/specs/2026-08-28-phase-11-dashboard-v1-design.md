# Phase 11 Dashboard V1 Design

**Date:** 28 August 2026  
**Status:** Design approved; awaiting written-spec review  
**Phase:** 11 — Dashboard V1  
**Trading mode at Phase 11 acceptance:** research only; live trading disabled

## 1. Goal

Build an operator dashboard that makes the BTC Polymarket engine understandable without opening PostgreSQL.

Dashboard V1 must expose the existing system truth for the verified 5m and 15m horizons:

- active markets;
- model probabilities;
- current market prices and prediction-time executable prices;
- edge/action;
- feed health;
- immutable prediction history;
- evaluated accuracy and calibration when official labels exist;
- paper P&L status;
- current mode and live-trading state.

The dashboard is an observability phase, not an execution phase. It does not create predictions, labels, paper fills, orders, positions, wallets, approvals, or trading side effects.

Phase 12 owns paper execution. Phase 11 therefore must not fabricate paper P&L. Until real paper fills exist, the Paper P&L panel is explicitly unavailable and points to Phase 12.

## 2. Architecture decision

Use two narrowly scoped services:

1. **Next.js operator UI** in `apps/dashboard`.
2. **Read-only Python dashboard API** in the existing `bp_engine` package.

The browser never receives database credentials and never queries PostgreSQL directly. The Python service owns database/query semantics and exposes a small versioned JSON contract. The Next.js service renders that contract into a dense operator view.

This is preferred over direct Next.js-to-PostgreSQL access because the engine's storage and domain semantics already live in Python. It avoids reimplementing prediction/evaluation meaning in TypeScript and gives Phase 12 a stable UI-facing API without coupling browser code to the database schema.

It is preferred over a Python-only rendered dashboard because `apps/dashboard` is already the intended application boundary and a dedicated frontend can evolve without turning the engine package into a UI framework.

No dashboard-specific database tables or migrations are added in V1.

## 3. Safety and trust boundary

Dashboard V1 is strictly read-only.

Rules:

- API routes are GET-only, plus framework-level OPTIONS/HEAD behavior where applicable.
- SQLAlchemy sessions used by the dashboard run with PostgreSQL transaction read-only semantics.
- dashboard repository/query modules expose no insert, update, delete, DDL, order, wallet, signing, allowance, or execution methods;
- the dashboard package does not import trading/auth/order modules;
- no private key, wallet secret, Polymarket trading credential, or signing material is added;
- browser code receives only dashboard JSON/HTML, never `DATABASE_URL` or database credentials;
- Phase 11 host acceptance requires `mode=research` and `live_trading_enabled=false`;
- existing `bp-recorder` and `bp-live-predictor` remain separate services and must not be restarted or replaced merely to serve the dashboard.

The first production deployment is operator-private. Both dashboard services bind to loopback only. Operator access is through the existing trusted host access path, such as an SSH tunnel. Public internet exposure and public-user authentication are out of scope for V1.

## 4. Source-of-truth mapping

The dashboard is a projection of existing immutable/current stores. It does not create a parallel state model.

### 4.1 Active markets

Source: `polymarket_markets`.

Use only the verified horizons:

- 300 seconds / 5m;
- 900 seconds / 15m.

An active-market row is based on current market metadata such as condition id, slug/question, start/end time, active/closed/accepting-orders state, and token ids.

### 4.2 Current market prices

Source: the latest available `market_state_1s` rows for each active market's Up and Down token ids.

Current price fields must be labeled as **current book state** and must never be presented as the values used by a historical prediction unless their stored provenance is the same.

Show current best bid/ask and data age when available. Missing or stale book data is shown as missing/stale; the dashboard does not synthesize midpoint, opposite-token transforms, or replacement prices.

### 4.3 Model probability, prediction-time price, edge, and action

Source: `live_predictions`.

Use the immutable stored values exactly as written by Phase 10, including:

- scheduled/recorded timestamps and lateness;
- raw and calibrated probability;
- predicted side;
- prediction-time Up/Down bid/ask;
- selected ask/bid/spread;
- raw and cost-adjusted edge;
- minimum edge;
- executable/trade flags;
- decision reason;
- source policy/calibration/backtest/training provenance.

Decimal database values are serialized losslessly as decimal strings in the API contract. The UI may format them for display but does not alter or recompute the stored research decision.

### 4.4 Feed health

Sources: `feed_status` and `feed_incidents`.

For each feed/stream show:

- reported status;
- last received timestamp;
- age since last received event;
- last source timestamp when available;
- recent incident count and most recent incident time/type;
- source/stream identity.

Freshness presentation is descriptive. V1 must not silently invent a new trading eligibility rule from dashboard colors.

### 4.5 Prediction history

Sources: `live_predictions` with optional left join to `live_prediction_evaluations`.

History is ordered newest first and supports horizon and evaluation-state filters.

The original prediction row and later evaluation are presented as separate concepts. A later outcome must never make the UI imply that outcome data existed at prediction time.

### 4.6 Accuracy and calibration

Source: `live_prediction_evaluations` joined to its immutable `live_predictions` parent.

When one or more official evaluations exist, show at minimum:

- evaluated sample count;
- accuracy;
- mean calibrated Brier score;
- mean calibrated log loss;
- per-horizon breakdown;
- a reliability/calibration table or chart using fixed probability buckets with count, mean predicted probability, and observed Up frequency.

Raw probability metrics may be shown alongside calibrated metrics where useful, but calibrated metrics are primary because Phase 9 selected the deployed calibration policy.

Every metric visibly includes its sample count. With zero evaluations, the performance section is `pending`; it does not display zero accuracy, zero Brier, or an inferred label.

### 4.7 P&L truthfulness

Phase 11 distinguishes three concepts:

- **Research hypothetical P&L**: may be summarized from Phase 10 evaluation fields when present and is labeled hypothetical/assumed-cost research evidence.
- **Paper P&L**: `unavailable_until_phase_12` until the paper execution ledger exists.
- **Live P&L**: unavailable; live trading is disabled.

No hypothetical Phase 10 field may be relabeled as paper P&L.

### 4.8 Current mode

Source: engine settings/configuration used by the dashboard API process.

The header must always display:

- current mode;
- `live_trading_enabled`;
- active horizons;
- API/database health timestamp.

A research-mode dashboard should make `RESEARCH / LIVE TRADING OFF` visually unmistakable.

## 5. API contract

Version the API under `/api/v1`.

V1 endpoints:

### `GET /health`

Returns process/database health and generated timestamp. No secrets or raw connection strings.

### `GET /api/v1/overview`

Returns the compact dashboard header and summary:

- generated timestamp;
- mode/live-trading flag;
- verified horizons;
- database status;
- feed summary;
- active market count;
- recent prediction counts by horizon;
- evaluated/pending counts;
- paper P&L availability state.

### `GET /api/v1/markets`

Returns active/recent verified market rows with current book state and, when present, their immutable Phase 10 prediction summary.

Query parameters are bounded to simple filters such as horizon and limit. Limits have conservative server-side maxima.

### `GET /api/v1/predictions`

Returns paginated newest-first immutable prediction history with optional evaluation child summary.

Supported filters:

- horizon;
- evaluated/pending;
- decision/trade flag;
- bounded page size/cursor.

### `GET /api/v1/performance`

Returns evaluated performance aggregates and calibration buckets, plus explicit data-availability metadata.

The response separates:

- calibrated prediction quality;
- optional raw prediction quality;
- research hypothetical P&L;
- paper P&L availability.

No mutation endpoint exists in V1.

## 6. API implementation boundaries

Add a focused package such as `bp_engine.dashboard` containing:

- immutable response models;
- read-only query/repository functions;
- metric aggregation functions;
- HTTP application wiring;
- CLI/service entry point.

Business meaning belongs in Python functions that can be unit-tested without HTTP. HTTP handlers should validate query parameters, call those functions, and serialize stable response models.

The dashboard query layer uses parameterized SQLAlchemy statements only. It does not reuse a repository method that can mutate state.

Database errors produce a sanitized `503` response. Invalid filters produce `400`. Unexpected failures are logged server-side with request correlation data but do not expose stack traces, SQL, credentials, or secrets to the browser.

## 7. Frontend information architecture

V1 is one operator dashboard, not a consumer product.

### Header

Always visible:

- `BTC Polymarket Engine`;
- current mode badge;
- live-trading state;
- last refresh timestamp;
- API/database health.

### Top summary row

Cards for:

- active 5m markets;
- active 15m markets;
- recent predictions;
- evaluated predictions;
- feed health;
- Paper P&L status.

### Active markets table

Each row shows:

- horizon;
- market question/slug;
- time remaining/end time;
- accepting-orders/current state;
- current Up bid/ask;
- current Down bid/ask;
- current book age;
- stored calibrated probability if a Phase 10 prediction exists;
- stored predicted side;
- stored cost-adjusted edge and decision;
- prediction scheduled/recorded timing.

Current-book values and prediction-time values must be visually distinguished.

### Performance section

Show:

- evaluation count;
- accuracy;
- calibrated Brier;
- calibrated log loss;
- 5m/15m breakdown;
- reliability/calibration visualization;
- research hypothetical assumed-cost P&L when available;
- Paper P&L unavailable state until Phase 12.

When labels are pending, this section displays a truthful pending state rather than empty charts that imply zero performance.

### Feed health section

Show one row per feed/stream with status, last-event age, and recent incidents. Stale/error feeds are visually prominent.

### Prediction history

Newest-first table with filters and bounded pagination. It exposes timing/provenance fields needed to confirm that predictions were recorded prospectively.

## 8. Frontend behavior and dependencies

`apps/dashboard` becomes an independently buildable Next.js application with TypeScript.

Implementation requirements:

- exact frontend dependency versions are pinned in the package lockfile during implementation;
- no browser-side database library;
- no global state framework for V1;
- no heavy charting framework unless the calibration visualization cannot be implemented clearly with simple HTML/SVG/CSS;
- server-side data fetches go through the read-only dashboard API;
- a small client refresh controller may periodically refresh the server-rendered view;
- automatic refresh failures preserve the last successful data while marking it stale;
- all timestamps are displayed in UTC with explicit `UTC` labeling;
- probability/price/edge formatting never changes the underlying API value;
- responsive behavior must keep the dashboard usable on a laptop-sized operator screen; mobile optimization is secondary.

The visual style should be dense, calm, and operational: prioritize scanability, stale/error visibility, and exact numbers over decoration.

## 9. Refresh and load policy

V1 avoids websockets and a new streaming subsystem.

Recommended polling cadence:

- overview/active markets/feed health: about every 5 seconds;
- prediction history: about every 15 seconds;
- aggregate performance/calibration: about every 60 seconds.

The API may use very short in-process caching for expensive aggregate queries, but no Redis or new persistence layer is introduced.

All list endpoints are bounded. No endpoint may accidentally request an unbounded prediction/event history scan.

## 10. Deployment

Add two host services or equivalent deployment assets:

- `bp-dashboard-api` — Python read-only dashboard API;
- `bp-dashboard-web` — built Next.js application.

Both run as an unprivileged account and bind to `127.0.0.1` only.

Suggested loopback ports may be chosen in implementation without changing this design, provided they do not collide with existing services and remain non-public.

The web service reaches the API over loopback. The API reaches PostgreSQL using the existing host secret mechanism; database credentials are never baked into frontend assets.

Deployment must not change recorder/live-predictor trading configuration. Live trading remains false and zero trade/loss limits remain unchanged during Phase 11 acceptance.

## 11. Testing

TDD must cover at least the following.

### Read-only safety

- all dashboard API routes are read-only;
- mutation HTTP verbs do not create state;
- dashboard SQL sessions are transaction-read-only;
- dashboard modules do not import order/wallet/signing/trading-auth modules;
- browser/client bundles contain no database credentials or `DATABASE_URL`.

### Source semantics

- active market rows are limited to verified 5m/15m horizons;
- current compact book data is not mislabeled as prediction-time data;
- prediction fields come from immutable `live_predictions` values;
- evaluation fields are attached only from `live_prediction_evaluations`;
- no unresolved market is treated as an evaluation label;
- decimal fields preserve stored values through API serialization.

### Performance truthfulness

- zero evaluations returns `pending`, not zero-valued performance;
- accuracy/Brier/log-loss aggregates match fixture calculations;
- calibration bucket boundaries and counts are deterministic;
- 5m and 15m metrics are separated correctly;
- research hypothetical P&L is labeled separately from paper P&L;
- Paper P&L is unavailable until a Phase 12 paper ledger exists.

### Feed and stale data

- feed status age is computed from the response-generation timestamp;
- missing last-received timestamps remain explicit;
- recent incident aggregation is bounded;
- frontend shows stale/error states without replacing last known numbers with fabricated values.

### API and frontend

- endpoint filter/limit validation;
- sanitized 503 behavior when PostgreSQL is unavailable;
- stable response shape tests;
- frontend renders research/live-off state prominently;
- active markets, performance, feed health, and prediction history each render populated, empty, and error/pending states;
- frontend production build succeeds.

### Regression

- existing Python test suite stays green;
- Ruff stays green;
- Phase 10 prediction/evaluation integrity tests remain unchanged and green;
- deployment validation remains green;
- health output continues to report research mode and live trading disabled.

## 12. Phase 11 host acceptance

Acceptance must run on an exact candidate SHA and prove the dashboard is using genuine production-host data.

The gate must prove:

- exact candidate SHA;
- `mode=research`;
- `live_trading_enabled=false`;
- zero live trade/loss limits remain unchanged;
- recorder active before/after;
- live predictor active before/after;
- dashboard API and web services run unprivileged;
- both dashboard ports bind only to loopback;
- API database session rejects a write attempt because it is read-only;
- `/health` succeeds without leaking secrets;
- overview returns both verified horizons and truthful mode/trading state;
- active/recent market data can be read without direct database access;
- prediction history returns immutable Phase 10 rows when they exist;
- performance reports `pending` when no labels exist, or exact aggregates when labels exist;
- Paper P&L reports unavailable until Phase 12 rather than zero/fake profit;
- feed health is visible from the API/UI;
- a production frontend build serves the operator dashboard successfully;
- no order/wallet/signing/trading side effects occur;
- disk and existing recorder/predictor health remain acceptable after the run.

Phase 11 acceptance does not require profitable predictions or paper trades. It requires truthful operational visibility.

## 13. Phase boundary

Phase 11 closes when an operator can understand market state, prediction state, feed state, evaluation quality, mode, and P&L availability without opening PostgreSQL, with exact-host evidence that the dashboard is read-only and does not alter Phase 10 records.

After Phase 11 closes:

- Phase 12 may add the paper execution ledger and replace the Paper P&L unavailable panel with real simulated-fill accounting;
- Phase 13 remains the improvement loop;
- Phase 14 remains live readiness;
- Phase 15 remains controlled real-money launch and still requires the repository's live gate plus explicit user authorization.
