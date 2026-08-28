# Phase 11 Dashboard V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only Next.js/TypeScript operator dashboard backed by a safe Python snapshot API over accepted live-prediction evidence.

**Architecture:** PostgreSQL stays behind a Python read-model/API bound to localhost. Next.js consumes the API server-side and exposes only a same-origin snapshot route to browser code. There are no execution endpoints or controls, and paper P&L remains unavailable until Phase 12.

**Tech Stack:** Python 3.12+, psycopg/SQLAlchemy already in the project, stdlib HTTP server, Next.js, TypeScript, React, Node.js.

**Spec:** `docs/superpowers/specs/2026-08-28-phase-11-dashboard-v1-design.md`

## Global Constraints

- Trading mode remains `RESEARCH`.
- `live_trading_enabled` remains false.
- No order, wallet, signing, paper-fill, or execution path may be added.
- Paper P&L must be explicit `UNAVAILABLE_UNTIL_PHASE_12` with null value.
- Accuracy/calibration may use only append-only official-outcome `live_prediction_evaluations`.
- Missing values stay missing; do not zero-fill.
- Browser code never receives database credentials.
- Next.js + TypeScript + simple responsive UI.

---

### Task 1: Read-model contract and metrics

**Files:**
- Create: `tests/dashboard/test_service.py`
- Create: `src/bp_engine/dashboard/__init__.py`
- Create: `src/bp_engine/dashboard/models.py`
- Create: `src/bp_engine/dashboard/service.py`

**Interfaces:**
- Produces: `build_dashboard_snapshot(repository, *, now)` and `summarize_performance(predictions, evaluations)` returning JSON-safe dictionaries.

- [ ] **Step 1: Write failing tests** covering RESEARCH safety flags, Phase-12 paper-P&L sentinel, evaluated-only accuracy/calibration, empty-evaluation null semantics, and calibration bucket math.
- [ ] **Step 2: Run** `pytest tests/dashboard/test_service.py -v`; expected failure is missing `bp_engine.dashboard`.
- [ ] **Step 3: Implement minimal models/service** to satisfy the tests without SQL.
- [ ] **Step 4: Run** `pytest tests/dashboard/test_service.py -v`; expected PASS.
- [ ] **Step 5: Commit** `feat: add dashboard read model`.

### Task 2: PostgreSQL dashboard repository

**Files:**
- Create: `tests/dashboard/test_repository_postgres.py`
- Create: `src/bp_engine/dashboard/repository.py`

**Interfaces:**
- Produces: `PostgresDashboardRepository(database_url)` with `list_active_markets(now)`, `list_feed_health(now)`, `list_predictions(limit)`, and `list_evaluations()`.

- [ ] **Step 1: Write failing PostgreSQL tests** that apply migrations, insert minimal market/prediction/evaluation/feed rows, and assert latest-prediction selection, active-window filtering, feed age, immutable history ordering, and official evaluation retrieval.
- [ ] **Step 2: Run** `pytest tests/dashboard/test_repository_postgres.py -v`; expected failure is missing repository.
- [ ] **Step 3: Implement parameterized read-only SQL**. No insert/update/delete methods exist in this repository.
- [ ] **Step 4: Run** PostgreSQL test and full dashboard tests; expected PASS.
- [ ] **Step 5: Commit** `feat: add dashboard postgres read repository`.

### Task 3: Localhost snapshot API

**Files:**
- Create: `tests/dashboard/test_api.py`
- Create: `src/bp_engine/dashboard/api.py`
- Create: `src/bp_engine/dashboard/__main__.py`
- Create: `scripts/run_dashboard_api.py`

**Interfaces:**
- `GET /health` -> JSON liveness/readiness response.
- `GET /api/v1/snapshot` -> dashboard snapshot.
- All non-GET methods and unknown routes -> no mutation surface.

- [ ] **Step 1: Write failing handler tests** for snapshot JSON, health, 404, and POST rejection.
- [ ] **Step 2: Run** `pytest tests/dashboard/test_api.py -v`; expected missing API failure.
- [ ] **Step 3: Implement stdlib HTTP server** defaulting to `127.0.0.1:8787`, constructing repository/service per request and emitting `Cache-Control: no-store`.
- [ ] **Step 4: Run dashboard tests and `python -m py_compile scripts/run_dashboard_api.py`; expected PASS.
- [ ] **Step 5: Commit** `feat: serve dashboard snapshot api`.

### Task 4: Next.js operator UI

**Files:**
- Delete: `apps/dashboard/.gitkeep`
- Create: `apps/dashboard/package.json`
- Create: `apps/dashboard/package-lock.json` after install
- Create: `apps/dashboard/next.config.ts`
- Create: `apps/dashboard/tsconfig.json`
- Create: `apps/dashboard/app/layout.tsx`
- Create: `apps/dashboard/app/page.tsx`
- Create: `apps/dashboard/app/globals.css`
- Create: `apps/dashboard/app/api/snapshot/route.ts`
- Create: `apps/dashboard/components/dashboard-client.tsx`
- Create: `apps/dashboard/lib/contracts.ts`
- Create: `apps/dashboard/lib/format.ts`

**Interfaces:**
- Browser fetches only `/api/snapshot`.
- Server route fetches `BP_DASHBOARD_API_URL` default `http://127.0.0.1:8787/api/v1/snapshot`.

- [ ] **Step 1: Add frontend contract tests/build gate** by extending CI with Node setup, `npm ci`, `npm run lint`, `npm run typecheck`, `npm run build`.
- [ ] **Step 2: Push tests/gate without frontend implementation** and confirm CI fails on missing package/app.
- [ ] **Step 3: Implement the responsive dashboard** with mode banner, KPIs, active markets, feed health, performance/calibration, immutable history, auto-refresh, stale/error state, and explicit Phase 12 P&L boundary.
- [ ] **Step 4: Run frontend lint/typecheck/build in CI; expected PASS.
- [ ] **Step 5: Commit** `feat: add Phase 11 Next.js dashboard`.

### Task 5: Deployment and host acceptance

**Files:**
- Create: `deploy/systemd/bp-dashboard-api.service`
- Create: `deploy/systemd/bp-dashboard-web.service`
- Modify: `deploy/bp.env.example`
- Create: `scripts/deploy/phase11_host_acceptance.sh`
- Create: `scripts/deploy/phase11_cloudshell_accept.sh`
- Create: `docs/PHASE-11-DEPLOYMENT.md`
- Create: `tests/dashboard/test_phase11_contract.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Services bind localhost only.
- Host acceptance emits `PHASE11_HOST_ACCEPTANCE=PASS` only after verifying both services, snapshot semantics, visible UI, RESEARCH mode, no execution path, and Phase-12 P&L sentinel.

- [ ] **Step 1: Write failing contract/deployment tests** asserting service hardening, localhost bindings, acceptance checks, and absence of execution terms/routes.
- [ ] **Step 2: Run** dashboard contract tests; expected failure on missing deployment assets.
- [ ] **Step 3: Implement deployment assets and acceptance scripts**; add syntax/compile/frontend gates to CI.
- [ ] **Step 4: Run full CI and smoke workflows; expected PASS before host rollout.
- [ ] **Step 5: Commit** `ops: package Phase 11 dashboard deployment`.

### Task 6: Production-host acceptance and closeout

**Files:**
- Create after evidence exists: `docs/evidence/phase-11-closeout-20260828.json`
- Modify after evidence exists: `PROJECT_STATE.json`
- Modify after evidence exists: `docs/CHANGELOG.md`
- Modify after evidence exists: `START-HERE.md`
- Modify after evidence exists: `docs/BUILD-ORDER.md`

- [ ] **Step 1: Deploy the exact CI-green candidate to the production research host.**
- [ ] **Step 2: Run `phase11_host_acceptance.sh` and record sanitized evidence.**
- [ ] **Step 3: Re-run CI/smokes for the exact closeout candidate.**
- [ ] **Step 4: Close Phase 11 only when host evidence passes; otherwise leave current phase at 11 with the concrete blocker recorded.**
- [ ] **Step 5: Advance immediate next action to Phase 12 Paper Execution only after Phase 11 closeout, while live trading remains disabled.**