# BTC Polymarket Prediction Engine (BP)

Research-first system for estimating short-duration BTC Polymarket Up/Down probabilities, measuring whether those probabilities provide a tradeable edge, and eventually progressing from research to paper trading and only then controlled live trading.

## Current status

**Phase 0:** complete.  
**Phase 1:** complete and live-verified against Polymarket Gamma.  
**Current phase:** Phase 2 — 24/7 BTC + Polymarket raw recorder.  
**Trading mode:** `RESEARCH`. Live trading is disabled.

The ~80% accuracy discussed for this project is a research target, not an assumed or proven capability.

## Read before working

Use this order in every new chat or development session:

1. `docs/MASTER-SOURCE-OF-TRUTH.md`
2. `PROJECT_STATE.json`
3. `docs/BUILD-ORDER.md`
4. `docs/DECISION-LOG.md`
5. `docs/CHANGELOG.md`
6. `AGENTS.md`

The Master Source of Truth wins if anything conflicts.

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
- Real-money trading is not implemented or authorized at this stage.
- A 10-minute recurring Polymarket BTC market is not assumed to exist; horizons remain configurable.

## Repository layout

```text
apps/dashboard/          Future Next.js dashboard
src/bp_engine/           Python engine package
tests/                   Automated tests
scripts/                 Backfill/training/maintenance entrypoints
migrations/              Future database migrations
data/                     Local/generated data (ignored)
docs/                     Source of truth, build order, decisions, plans
```

## Phase 1 live verification

The local sandbox used for this build cannot resolve external DNS, so Phase 1 used a GitHub Actions workflow (`Live Gamma Smoke`) for the external check. That workflow successfully queried current public Gamma 5m/15m BTC markets and committed an authentic response capture into `tests/fixtures/polymarket/live/`. Focused parser fixtures now use authentic captured IDs and token metadata.
