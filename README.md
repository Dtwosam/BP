# BTC Polymarket Prediction Engine (BP)

Research-first system for estimating short-duration BTC Polymarket Up/Down probabilities, measuring whether those probabilities provide a tradeable edge, and eventually progressing from research to paper trading and only then controlled live trading.

## Current status

**Phases 0–14 engineering:** complete.  
**Current gate:** `PHASE_14_ENGINEERING_COMPLETE_LIVE_GATE_BLOCKED`.  
**Trading mode:** `RESEARCH`. Live trading is disabled.

Phase 14 — Live Readiness V1 — passed non-spending production host acceptance on exact candidate `5854e3003aa3340ce3733bf4532e204c1ec55836`. The accepted path imports the official `polymarket-client`, enforces fail-closed activation/geoblock/kill-switch/risk interlocks, reconciles synthetic/live-readiness state, exposes read-only diagnostics, and proved `REAL_ORDER_SIDE_EFFECTS=0` with real-money limits still zero.

The Master live gate is **not** satisfied. The production geoblock returned `GEOBLOCK_BLOCKED=true`; the prospective paper sample is still too small; positive after-cost profitability is not established; prospective calibration evidence is insufficient; and explicit real-money authorization has not been given. Phase 15 is therefore not permitted. The ~80% accuracy discussed for this project remains a research target, not an assumed or guaranteed capability.

## Read before working

Use this order in every new chat or development session:

1. `docs/MASTER-SOURCE-OF-TRUTH.md`
2. `PROJECT_STATE.json`
3. `docs/BUILD-ORDER.md`
4. `docs/DECISION-LOG.md`
5. `docs/CHANGELOG.md`
6. `AGENTS.md`

The Master Source of Truth wins if anything conflicts. `PROJECT_STATE.json` identifies the current build/gate state.

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
- Phase 13 Improvement Loop remains accepted; promotion requires frozen hypotheses plus permitted evidence, economic uncertainty, calibration guardrails, and deliberate decisions.
- Phase 14 Live Readiness engineering is accepted, but the Master live gate remains closed.
- `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0` remain required until every live-gate row passes and real-money authorization is explicit.
- Do not bypass geographic/service restrictions with proxies, VPNs, tunneling, or relocation tricks.
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

## Accepted Phase 14 engineering proof

Fresh exact-head gates on `5854e3003aa3340ce3733bf4532e204c1ec55836` passed the main test/dashboard lane, Live Recorder Smoke, Recorder Short Soak, and Historical Backfill Smoke before host acceptance.

Production host acceptance returned:

- `PHASE14_HOST_ACCEPTANCE=PASS`
- `SERVICES_ACTIVE=PASS`
- `SDK_IMPORT=PASS`
- `INTERLOCK_BLOCKS_SUBMISSION=PASS`
- `RISK_RULES=PASS`
- `RECONCILIATION=PASS`
- `REAL_ORDER_SIDE_EFFECTS=0`
- `LIVE_GATE_ELIGIBLE=false`
- `GEOBLOCK_BLOCKED=true`

The explicit Master gate matrix is stored in `docs/evidence/phase-14-closeout-20260830.json`. It records historical reproducibility, leakage controls, chronological splits, risk/kill-switch testing, and execution/reconciliation testing as passed; walk-forward stability, prospective paper sample, and prospective calibration as insufficient evidence; and after-cost profitability, geographic eligibility, and explicit real-money authorization as failed.

The next permitted work is continued money-disabled prospective paper evidence and gate reassessment. Phase 15 controlled live launch remains blocked.
