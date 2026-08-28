# BTC Polymarket Prediction Engine (BP)

Research-first system for estimating short-duration BTC Polymarket Up/Down probabilities, measuring whether those probabilities provide a tradeable edge, and eventually progressing from research to paper trading and only then controlled live trading.

## Current status

**Phases 0–12:** complete.
**Current phase:** Phase 13 — Improvement Loop.
**Trading mode:** `RESEARCH`. Live trading is disabled.

Phase 12 is production-host accepted and permanently installed on exact operational candidate `159ce77af9a51ae208511d216bee52d5732cee3b`. The production system now has deterministic money-disabled paper execution reconciled to immutable 5m/15m signals, while real execution remains unavailable. Accepted paper evidence includes 3 orders, 2 fills, nonnegative cash, and reconciliation `OK` with zero violations. These are simulated results, not a live-profitability claim. Phase 13 now focuses on disciplined hypothesis-driven improvement; the ~80% accuracy discussed for this project remains a research target, not an assumed or guaranteed capability.

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
- Phase 12 Paper Execution is accepted and permanently installed; paper fills must remain causal and reconciled to immutable signals.
- Phase 13 is the current Improvement Loop; promote changes only through explicit hypotheses and repeated out-of-sample champion/challenger evidence.
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

## Accepted Phase 12 proof

Fresh exact-head CI passed on `159ce77af9a51ae208511d216bee52d5732cee3b` (run #1328): 564 Python tests passed, Ruff/deployment validation/health passed, and the dashboard test/typecheck/Next.js production build lane passed.

Isolated production-host acceptance returned `PHASE12_HOST_ACCEPTANCE=PASS` with a genuine prospective trade, causal paper evidence, reconciliation `OK`, and an idempotent rerun. Permanent installation returned `PHASE12_INSTALL=PASS` on the same SHA. `bp-paper-execution`, recorder, PostgreSQL, dashboard API, and dashboard web were active; paper execution was available, real execution unavailable, current cash was `92.207577336709`, and reconciliation had zero violations.

Sanitized evidence is in `docs/evidence/phase-12-closeout-20260828.json`. The next task is Phase 13 Improvement Loop. Live trading remains disabled and still requires Phase 14 readiness plus explicit authorization.
