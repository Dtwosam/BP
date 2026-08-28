# BTC Polymarket Prediction Engine (BP)

Research-first system for estimating short-duration BTC Polymarket Up/Down probabilities, measuring whether those probabilities provide a tradeable edge, and eventually progressing from research to paper trading and only then controlled live trading.

## Current status

**Phases 0–10:** complete.  
**Current phase:** Phase 11 — Dashboard V1.  
**Trading mode:** `RESEARCH`. Live trading is disabled.

Phase 10 is production-host accepted on exact operational candidate `39101a60cdf712650f57a833849015c49da24946`. The accepted service records prospective immutable 5m/15m predictions before outcome, keeps official-outcome evaluation append-only, and contains no order, wallet, signing, paper-fill, or position path. The ~80% accuracy discussed for this project remains a research target, not an assumed or proven capability, and Phase 10 acceptance is not a profitability claim.

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
- Phase 11 is a dashboard/read surface, not an execution phase.
- Paper execution is Phase 12 and must not be invented inside the dashboard.
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

## Accepted Phase 10 proof

Fresh exact-head gates passed on `39101a60cdf712650f57a833849015c49da24946`: CI #1130, Historical Backfill Smoke #439, Live Recorder Smoke #544, and Recorder Short Soak #510. Production acceptance returned both `VERDICT=PASS` and `PHASE10_HOST_ACCEPTANCE=PASS`, with prospective 5m/15m predictions, maximum lateness 5,563 ms, and zero pre-outcome, source-cutoff, semantic-hash, duplicate-key, mutation, or order-side-effect violations.

Sanitized acceptance evidence is recorded in `docs/evidence/phase-10-closeout-20260828.json`. The next build-order task is Dashboard V1 so system health and prediction performance can be understood without direct database access.
