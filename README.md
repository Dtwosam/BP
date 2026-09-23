# BTC Polymarket Prediction Engine (BP)

Research-first system for estimating short-duration BTC Polymarket Up/Down probabilities, measuring whether those probabilities provide a tradeable edge, and eventually progressing from research to paper trading and only then controlled live trading.

## Current status

**Phases 0–14 engineering:** complete.  
**Current phase:** Phase 15 — controlled frozen-V3 ten-dollar canary, code-ready and not deployed.  
**Master live gate:** `PASS`.  
**Trading state:** live trading remains disabled until an explicit operator activation boundary is crossed.

Phase 14 — Live Readiness V1 — passed non-spending production host acceptance on exact candidate `5854e3003aa3340ce3733bf4532e204c1ec55836`. The accepted path imports the official `polymarket-client`, enforces fail-closed activation/geoblock/kill-switch/risk interlocks, reconciles synthetic/live-readiness state, exposes read-only diagnostics, and proved `REAL_ORDER_SIDE_EFFECTS=0` with real-money limits still zero.

The Master live gate is now **satisfied for the exact frozen V3**. The accelerated prospective audit passed walk-forward stability, sample sufficiency with uncertainty, profitability, calibration, and execution/reconciliation. The user's ordinary network returned `blocked=false` in `NG/LA`, and the dedicated Johannesburg execution host returned `blocked=false` in `ZA/GP`. Durable host evidence is `docs/evidence/phase-15-v3-canary-execution-host-geoblock-20260923.json`.

Phase 15 is limited to a one-shot canary contract: at most one live submission intent, $10 maximum fee-inclusive total cost, $10 maximum concurrent exposure, $10 maximum daily loss, and one consecutive loss. The frozen model, timing, calibration, and `min_edge=0.075` remain unchanged. Credential provisioning and real-money activation are not automated in the repository.

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
- The Master gate permits only the exact ten-dollar frozen-V3 canary contract; broad live trading is not authorized.
- Phase 12 Paper Execution remains money-disabled; paper fills must remain causal and reconciled to immutable signals.
- Phase 13 Improvement Loop remains accepted; promotion requires frozen hypotheses plus permitted evidence, economic uncertainty, calibration guardrails, and deliberate decisions.
- Phase 14 Live Readiness engineering is accepted and the Master live gate now passes for the exact frozen-V3 ten-dollar canary.
- Repository automation remains non-spending: wallet/private-key provisioning and the final real-money activation are explicit operator boundaries outside GitHub automation.
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

The original Master gate matrix is stored in `docs/evidence/phase-14-closeout-20260830.json`. The latest frozen-V3 reassessment is `docs/evidence/phase-14-v3-live-gate-reassessment-production-20260923.json`: profitability, execution/reconciliation, and explicit user authorization are now `pass`; walk-forward stability, paper-sample sufficiency, and calibration acceptance remain `insufficient_evidence`; geographic eligibility remains `fail`; overall live gate remains `fail`.

The accelerated `phase15-v3-canary-readiness-v1` audit has now passed in production read-only mode. Walk-forward stability, sample sufficiency with uncertainty, profitability, calibration, execution/reconciliation, and explicit user authorization are all `pass`. The user’s ordinary physical-network geoblock check is also unblocked (`NG/LA`). The remaining blocker is execution-host geography: the current US host is blocked. The next authorized mutation is a dedicated Johannesburg (`africa-south1-a`) execution-only VM probe using the official direct Polymarket geoblock endpoint. No trading software or wallet material may be installed until that probe passes.
