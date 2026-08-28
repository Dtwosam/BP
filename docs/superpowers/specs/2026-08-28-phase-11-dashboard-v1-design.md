# Phase 11 Dashboard V1 Design

## Goal

Build a read-only operator dashboard that makes the accepted Phase 10 prediction evidence and current system health understandable without direct PostgreSQL access.

## Non-negotiable boundaries

- Current mode remains `RESEARCH` and live trading remains disabled.
- The dashboard has no order-placement, paper-fill, wallet, signing, or execution path.
- Paper P&L is shown as unavailable/pending until Phase 12 produces actual paper-fill evidence.
- Accuracy and calibration are computed only from append-only `live_prediction_evaluations` joined to immutable `live_predictions`.
- Historical hypothetical P&L fields in Phase 10 evaluations are not presented as paper P&L.
- Missing data is explicit; the UI must not silently replace missing prices, outcomes, or health with zeros.
- No database credentials are exposed to browser code.

## Architecture

Phase 11 adds two replaceable layers.

1. `bp_engine.dashboard`: a Python read model over existing PostgreSQL tables. It owns all SQL and returns one JSON-safe snapshot containing current mode, active markets/latest predictions, feed health, prediction history, and evaluated performance by horizon.
2. `apps/dashboard`: a Next.js + TypeScript UI. A server-only Next.js route calls the Python dashboard API on localhost. Browser code calls only the same-origin Next.js route, so database/API internals are never exposed.

The Python API binds to `127.0.0.1` by default. The Next.js service also binds locally by default; remote access for host acceptance is through an SSH tunnel unless/until a separately approved public ingress is introduced.

## Data contract

`GET /api/v1/snapshot` returns:

- `generated_at`
- `mode`: `trading_mode`, `live_trading_enabled`, `execution_available`, `paper_execution_available`
- `active_markets`: market identity/window plus latest model probability, observed market probability, executable bid/ask, edge, decision, and prediction timestamps when available
- `feed_health`: one row per `feed_status` source/stream with status and recency
- `performance`: evaluated sample count, accuracy, calibrated Brier score, calibrated log loss, coverage, and calibration buckets by horizon
- `prediction_history`: newest immutable predictions plus later official outcome/evaluation when available
- `paper_pnl`: `{status: "UNAVAILABLE_UNTIL_PHASE_12", value: null}`

## Performance semantics

For each horizon:

- `total_predictions` counts immutable live predictions.
- `evaluated_predictions` counts rows with an official-outcome evaluation.
- `coverage = evaluated_predictions / total_predictions` when total predictions > 0.
- `accuracy` is the mean of `correct` over evaluated rows only.
- Brier/log-loss are means of the stored calibrated metrics over evaluated rows only.
- Calibration buckets use the stored calibrated probability and official target; bucket accuracy is the observed Up rate, not classifier correctness.
- If there are no evaluated rows, accuracy/calibration metrics are `null` and buckets are empty.

## Active-market semantics

A market is displayed as active only when `polymarket_markets.active = TRUE`, `closed = FALSE`, and `end_at > now()`. The latest prediction is selected by `(condition_id, scheduled_at DESC, recorded_at DESC)`.

## Feed-health semantics

`feed_status` is the source of truth. The API reports the stored status plus `age_seconds` from `last_received_at`. UI severity is derived from status and recency without rewriting source status.

## UI

Single responsive operator page:

- persistent mode/safety banner;
- KPI strip for active markets, feeds healthy, evaluated predictions, and paper P&L status;
- active-market cards/table with model vs market probability and decision/edge;
- feed-health table;
- performance-by-horizon cards including calibration buckets when available;
- immutable prediction-history table with later official result;
- explicit Phase 12 boundary copy where paper P&L would otherwise appear.

The UI auto-refreshes the snapshot, shows the snapshot timestamp, keeps the previous successful snapshot if a refresh fails, and visibly marks the data stale/error state.

## Deployment

- `bp-dashboard-api.service`: Python localhost read API.
- `bp-dashboard-web.service`: Next.js localhost web process.
- Environment examples contain no secrets; database URL remains in host environment files already protected by deployment policy.
- Host acceptance verifies API schema, UI HTTP response, current RESEARCH mode, no execution controls/routes, feed-health visibility, prediction visibility, official-evaluation-only performance, and explicit paper-P&L unavailability.

## Acceptance

Phase 11 is complete only when CI is green and production-host acceptance demonstrates that an operator can understand system health and live prediction performance without opening PostgreSQL directly. Phase 12 remains blocked until that evidence is recorded.