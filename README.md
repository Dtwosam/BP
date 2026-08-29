# BTC Polymarket Prediction Engine (BP)

Research-first system for estimating short-duration BTC Polymarket Up/Down probabilities, measuring whether those probabilities provide a tradeable edge, and eventually progressing from research to paper trading and only then controlled live trading.

## Current status

**Phases 0–13:** complete.  
**Current phase:** Phase 14 — Live Readiness.  
**Trading mode:** `RESEARCH`. Live trading is disabled.

Phase 13 — Improvement Loop V1 — is production-host accepted on exact operational candidate `4dcdf8955b2c79ea9f130fec5a0dcceef915a678`. The project now has immutable experiment/evaluation/decision records, explicit evidence-role and provenance validation, deterministic paired economic uncertainty, calibration guardrails, a network-free research CLI, and deliberate champion/challenger decisions. The first 5m spread/abstention challenger did **not** earn promotion: it exactly matched the accepted Phase 9 champion on reused ordinary-OOS evidence and had no independent fresh confirmation, so the immutable decision was `keep_champion`. The ~80% accuracy discussed for this project remains a research target, not an assumed or guaranteed capability.

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
- Phase 12 Paper Execution remains money-disabled; paper fills must remain causal and reconciled to immutable signals.
- Phase 13 Improvement Loop is accepted; promotion requires frozen hypotheses plus permitted evidence, economic uncertainty, calibration guardrails, and deliberate decisions.
- Phase 14 Live Readiness must fail closed: live mode remains OFF, real-money limits remain zero, and no real order may be placed without the complete live gate and explicit authorization.
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

## Accepted Phase 13 proof

Fresh exact-head CI passed on `4dcdf8955b2c79ea9f130fec5a0dcceef915a678` (run #1387): 637 Python tests passed, Ruff/deployment validation/health passed, and the dashboard test/typecheck/Next.js production build lane passed.

Production-host acceptance returned `PHASE13_HOST_ACCEPTANCE=PASS`. Experiment registration and challenger evaluation were idempotent; the immutable Phase 9 → Phase 8 → Phase 7 source chain matched exactly; an ineligible promotion attempt was rejected; all five production services remained active; and paper reconciliation remained `OK` with zero violations.

Experiment `phase13-exp-0c6f77ab575fdc75d517480285574ff8` evaluated challenger `phase13-spread-55b1f388b3df86b83124d6f289cdd625`. Champion and challenger each recorded 3 ordinary-OOS trades, `+0.148014` assumed-cost P&L, calibrated log loss `0.3349722232`, and calibrated Brier `0.1064723920`. The paired 10,000-resample bootstrap delta was exactly `0.0` with interval `[0.0, 0.0]`, and no independent fresh confirmation was present. Evaluation `phase13-eval-4c7c0457409f7e29687c5b75139cd405` was therefore not promotion-eligible for reasons `economic_uncertainty_not_positive` and `independent_confirmation_missing`; decision `phase13-decision-8e32d904a1169e10bed2eb8f7a375637` kept the accepted Phase 9 champion.

Sanitized evidence is in `docs/evidence/phase-13-closeout-20260829.json`. The next task is Phase 14 Live Readiness. Live trading remains disabled, real execution remains unavailable, and explicit authorization is still required before any controlled live launch.
