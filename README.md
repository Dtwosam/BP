# BTC Polymarket Prediction Engine (BP)

Research-first system for estimating short-duration BTC Polymarket Up/Down probabilities, measuring whether those probabilities provide a tradeable edge, and eventually progressing from research to paper trading and only then controlled live trading.

## Current status

**Phases 0–14:** complete.  
**Current phase:** Phase 15 controlled live launch.  
**Current state:** `PHASE_15_CANARY_AUTHORIZED_NOT_YET_SUBMITTED`.  
**Runtime trading mode:** still `RESEARCH`; no real order has been submitted yet.

The complete Master live gate now passes for the **one-order frozen-V3 canary**. Statistical readiness passed under predeclared rules, explicit user authorization is recorded, the user's ordinary physical-network direct Polymarket geoblock check is unblocked (`NG/LA`), and the dedicated Johannesburg execution host `bp-v3-canary-exec` is directly unblocked (`ZA/GP`).

The canary does **not** increase V3 strategy sizing. The first live order keeps the existing **$5 target notional**. The user's **$10 per-market authorization is a hard ceiling**, with $10 maximum total exposure, $10 daily-loss stop, one-consecutive-loss stop, and one accepted-order maximum.

Real-money submission is deliberately manual. Wallet bootstrap, canary preparation, and the arm step all submit **no order**. The Johannesburg kill switch is engaged by default; arming is short-lived and one-shot, and the executor re-engages the kill switch before its single SDK submission attempt. Reconciliation is mandatory before any second order.

V3 remains frozen. V4 Gate B collection continues unchanged through its preregistered future epoch.

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
- `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0` remain required until every live-gate row passes. Explicit frozen-V3 authorization was recorded on 23 September 2026, but all other gate rows still apply.
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

The original Master gate matrix remains historical evidence at `docs/evidence/phase-14-closeout-20260830.json`. The latest statistical readiness evidence is `docs/evidence/phase-15-v3-accelerated-readiness-production-20260923.json`, and the Johannesburg host PASS is `docs/evidence/phase-15-v3-canary-host-geoblock-20260923.json`.

The current operator contract is `docs/PHASE-15-V3-LIVE-CANARY.md`. No broad live rollout or automatic stake increase is authorized.
