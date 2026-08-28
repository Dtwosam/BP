# BTC Polymarket Prediction Engine (BP)

Research-first system for estimating short-duration BTC Polymarket Up/Down probabilities, measuring whether those probabilities provide a tradeable edge, and eventually progressing from research to paper trading and only then controlled live trading.

## Current status

**Phases 0–11:** complete.
**Current phase:** Phase 12 — Paper Execution.
**Trading mode:** `RESEARCH`. Live trading is disabled.

Phase 11 is production-host accepted and permanently installed on exact operational candidate `126959eaef973b061c3c7ea619b6d6313f3f4e4e`. The accepted dashboard is read-only and localhost-only, surfaces the immutable 5m/15m prediction system without direct database access, and keeps paper P&L unavailable until Phase 12 supplies real simulated-fill evidence. The ~80% accuracy discussed for this project remains a research target, not an assumed or proven capability, and Phase 11 acceptance is not a profitability claim.

## Read before working

Use this order in every new chat or development session:

1. `docs/MASTER-SOURCE-OF-TRUTH.md`
2. `PROJECT_STATE.json`
3. `docs/BUILD-ORDER.md`
4. `docs/DECISION-LOG.md`
5. `docs/CHANGELOG.md`
6. `AGENTS.md`

The Master Source of Truth wins if anything conflicts. `PROJECT_STATE.json` identifies the current build phase.

## Local setup

Requires Python 3.12+ and Docker for the local PostgreSQL service.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
python -m pip install -e ".[dev]"
cp .env.example .env

docker compose up -d postgres
pytest
ruff check .
python -m bp_engine.health
```

Expected health output includes:

```json
{"active_horizons":["5m","15m"],"live_trading_enabled":false,"mode":"research","optional_horizons":["10m"],"status":"ok","timezone":"UTC"}
```

## Safety

- Never commit `.env`, wallet keys, seed phrases, API secrets, or server secrets.
- Never paste a wallet private key or seed phrase into ChatGPT.
- Real-money trading is not authorized at this stage.
- Phase 11 Dashboard V1 is accepted and remains a read-only surface.
- Phase 12 is the current paper-execution phase; simulated fills must reconcile to immutable signals and must never place real orders.
- A 10-minute recurring Polymarket BTC market is not assumed to exist; horizons remain configurable.

## Repository layout

```text
apps/dashboard/          Dashboard V1 application surface
src/bp_engine/           Python engine package
tests/                   Automated tests
scripts/                 Backfill/training/maintenance/live-prediction entrypoints
migrations/              PostgreSQL migrations
data/                     Local/generated data (ignored)
docs/                     Source of truth, build order, decisions, plans, evidence
```

## Accepted Phase 11 proof

Fresh exact-head CI passed on `126959eaef973b061c3c7ea619b6d6313f3f4e4e` (run #1223): 511 Python tests passed, deployment validation and health passed, and the dashboard test/typecheck/Next.js production build lane passed. Isolated production-host acceptance returned `PHASE11_HOST_ACCEPTANCE=PASS` with 4 active markets, 4 feed rows, 2 performance rows, 26 prediction-history rows, localhost-only candidate listeners, and the recorder active.

Permanent installation returned `PHASE11_INSTALL=PASS` on the same exact operational SHA. `bp-recorder`, PostgreSQL, `bp-dashboard-api`, and `bp-dashboard-web` were active after install; permanent listeners were only `127.0.0.1:8787` and `127.0.0.1:3000`; API health reported `mode=RESEARCH` and `live_trading_enabled=false`; and POST to the snapshot endpoint returned HTTP 405.

Sanitized acceptance evidence is recorded in `docs/evidence/phase-11-closeout-20260828.json`. The next build-order task is Phase 12 Paper Execution: realistic simulated order/fill mechanics using the future live interface, reconciled against immutable signals. Live trading remains disabled.