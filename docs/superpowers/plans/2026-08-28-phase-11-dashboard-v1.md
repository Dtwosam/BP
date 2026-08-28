# Phase 11 Dashboard V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a loopback-only, read-only operator dashboard that exposes verified 5m/15m market state, Phase 10 predictions, feed health, evaluation quality, truthful P&L availability, and research/live-off status without opening PostgreSQL.

**Architecture:** Add a `bp_engine.dashboard` read-only Python API backed by SQLAlchemy Core and PostgreSQL transaction read-only semantics, then add a TypeScript/Next.js operator UI in `apps/dashboard` that consumes only that API. Keep the dashboard out of all prediction, label, paper execution, wallet, signing, order, and live-trading paths; use existing ledgers as the only source of truth.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x, Pydantic 2.x, FastAPI 0.141.1, Uvicorn 0.52.4, PostgreSQL 16, Next.js 16.3.3, React 19.2.8, React DOM 19.2.8, TypeScript 7.0.2, Node.js 22.16.0.

**Spec:** `docs/superpowers/specs/2026-08-28-phase-11-dashboard-v1-design.md`

## Global Constraints

- Phase 11 is observability only; it must not create predictions, labels, fills, orders, positions, wallets, approvals, or trading side effects.
- `mode=research`, `live_trading_enabled=false`, `max_trade_size_usd=0`, and `max_daily_loss_usd=0` remain mandatory for the dashboard API process.
- Verified horizons are exactly 300 seconds / 5m and 900 seconds / 15m; optional 10m is not a V1 dashboard market horizon.
- Current compact-book values must be labeled current; immutable prediction-time values must come only from `live_predictions`.
- Official evaluation values must come only from `live_prediction_evaluations`; unresolved markets are never treated as labels.
- Decimal database values cross the Python JSON boundary as strings; the frontend formats but never recomputes trading decisions.
- Paper P&L is `unavailable_until_phase_12` until a Phase 12 paper execution ledger exists; Phase 10 hypothetical research P&L is never relabeled as paper P&L.
- Both dashboard services bind to `127.0.0.1` only in V1.
- The browser never receives `DATABASE_URL`, credentials, private keys, wallet material, or trading credentials.
- No dashboard database migration is added in Phase 11.
- All list/history queries are bounded by server-side limits.
- Existing Phase 10 prediction/evaluation integrity and all prior CI/smoke tests must remain green.

---

## File Structure

### Python API

- Create `src/bp_engine/dashboard/__init__.py` — public dashboard package marker.
- Create `src/bp_engine/dashboard/models.py` — immutable Pydantic response models and decimal-string serialization.
- Create `src/bp_engine/dashboard/metrics.py` — pure accuracy/Brier/log-loss/calibration/hypothetical-P&L aggregation.
- Create `src/bp_engine/dashboard/queries.py` — bounded SQLAlchemy Core read-only queries over existing tables.
- Create `src/bp_engine/dashboard/service.py` — composition layer for overview, markets, prediction history, feed state, and performance.
- Create `src/bp_engine/dashboard/app.py` — FastAPI route wiring, filter validation, sanitized database failures.
- Create `src/bp_engine/dashboard/__main__.py` — safety interlock and Uvicorn loopback entry point.
- Create `scripts/run_dashboard_api.py` — deployment-friendly module launcher.
- Modify `src/bp_engine/config.py` — dashboard API port only; host remains hard-coded loopback.
- Modify `pyproject.toml` — FastAPI/Uvicorn dependencies.

### Python tests

- Create `tests/dashboard/test_metrics.py` — deterministic quality/calibration aggregation.
- Create `tests/dashboard/test_queries.py` — source semantics, bounded filters, decimal fidelity, read-only transaction behavior.
- Create `tests/dashboard/test_service.py` — overview/markets/history/performance truthfulness.
- Create `tests/dashboard/test_api.py` — route shapes, validation, 503 sanitization, GET-only behavior.
- Create `tests/dashboard/test_safety.py` — research/live-off/zero-limit interlock and forbidden-import checks.

### Next.js UI

- Delete `apps/dashboard/.gitkeep` once real files exist.
- Create `apps/dashboard/package.json` and generated `apps/dashboard/package-lock.json` — exact dependency pins.
- Create `apps/dashboard/next.config.ts`, `tsconfig.json`, `next-env.d.ts` — minimal Next.js configuration.
- Create `apps/dashboard/app/layout.tsx` — shell and global metadata.
- Create `apps/dashboard/app/page.tsx` — operator dashboard composition.
- Create `apps/dashboard/app/globals.css` — dense operational styling, responsive tables, state badges.
- Create `apps/dashboard/lib/api.ts` — server-side API client; loopback URL only from server env.
- Create `apps/dashboard/lib/types.ts` — dashboard API contract types.
- Create `apps/dashboard/lib/format.ts` — UTC/decimal/probability/age formatting only.
- Create `apps/dashboard/components/ModeBanner.tsx` — unmistakable research/live-off header.
- Create `apps/dashboard/components/SummaryCards.tsx` — active markets, recent/evaluated predictions, feeds, paper-P&L state.
- Create `apps/dashboard/components/MarketsTable.tsx` — current-book vs prediction-time data separation.
- Create `apps/dashboard/components/PerformancePanel.tsx` — pending/evaluated states and calibration table.
- Create `apps/dashboard/components/FeedHealthTable.tsx` — feed status/age/incidents.
- Create `apps/dashboard/components/PredictionHistory.tsx` — newest-first immutable history.
- Create `apps/dashboard/tests/format.test.ts` — pure display formatter checks using Node built-in test runner and TypeScript stripping.

### Deployment and CI

- Create `deploy/bp-dashboard-api.service` — unprivileged Python API, loopback only.
- Create `deploy/bp-dashboard-web.service` — unprivileged Next.js server, loopback only.
- Create `scripts/deploy/phase11_host_acceptance.sh` — exact-SHA host acceptance and read-only/write-rejection proof.
- Create `scripts/deploy/phase11_cloudshell_accept.sh` — exact candidate archive/stage wrapper following prior host-acceptance pattern.
- Modify `scripts/deploy/bootstrap_ubuntu.sh` — install Node 22 and dashboard services without changing trading configuration.
- Modify `.github/workflows/ci.yml` — set up Node, install locked frontend deps, run frontend tests/build, validate Phase 11 shell/service assets.
- Modify `deploy/bp.env.example` only if a dashboard API port variable is required; do not add secrets.

### Closeout after host acceptance

- Create `docs/evidence/phase-11-closeout-20260828.json` only from actual exact-SHA host evidence.
- Modify `PROJECT_STATE.json`, `README.md`, `START-HERE.md`, `docs/BUILD-ORDER.md`, and `docs/CHANGELOG.md` only after Phase 11 acceptance passes.

---

### Task 1: Add dashboard API dependencies, settings, and startup safety interlock

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/bp_engine/config.py`
- Create: `src/bp_engine/dashboard/__init__.py`
- Create: `src/bp_engine/dashboard/__main__.py`
- Create: `scripts/run_dashboard_api.py`
- Test: `tests/dashboard/test_safety.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: existing `Settings`, `TradingMode`, `get_settings` from `bp_engine.config`.
- Produces: `validate_dashboard_safety(settings: Settings) -> None`; `DASHBOARD_API_HOST = "127.0.0.1"`; `settings.dashboard_api_port: int` defaulting to `8787`.

- [ ] **Step 1: Write failing safety/config tests**

```python
from pathlib import Path

import pytest

from bp_engine.config import Settings, TradingMode
from bp_engine.dashboard.__main__ import DASHBOARD_API_HOST, validate_dashboard_safety


def test_dashboard_defaults_to_loopback_and_port_8787() -> None:
    settings = Settings(_env_file=None)
    assert DASHBOARD_API_HOST == "127.0.0.1"
    assert settings.dashboard_api_port == 8787


@pytest.mark.parametrize(
    "override",
    [
        {"mode": TradingMode.PAPER},
        {"mode": TradingMode.LIVE},
        {"live_trading_enabled": True},
        {"max_trade_size_usd": 1},
        {"max_daily_loss_usd": 1},
    ],
)
def test_dashboard_startup_rejects_non_research_safety_state(override: dict[str, object]) -> None:
    settings = Settings(_env_file=None, **override)
    with pytest.raises(RuntimeError, match="dashboard safety interlock"):
        validate_dashboard_safety(settings)


def test_dashboard_package_contains_no_trading_auth_imports() -> None:
    dashboard_root = Path("src/bp_engine/dashboard")
    forbidden = ("private_key", "wallet", "allowance", "place_order", "signing")
    text = "\n".join(path.read_text() for path in dashboard_root.glob("*.py"))
    assert not any(term in text.lower() for term in forbidden)
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `pytest tests/dashboard/test_safety.py tests/test_config.py -q`

Expected: failure because `bp_engine.dashboard`, `dashboard_api_port`, and the safety validator do not exist.

- [ ] **Step 3: Add exact backend dependencies and minimal startup code**

Add to `pyproject.toml` project dependencies:

```toml
  "fastapi==0.141.1",
  "uvicorn==0.52.4",
```

Add to `Settings`:

```python
    dashboard_api_port: int = 8787
```

Create `src/bp_engine/dashboard/__main__.py` with the exact safety shape:

```python
from __future__ import annotations

import uvicorn

from bp_engine.config import Settings, TradingMode, get_settings

DASHBOARD_API_HOST = "127.0.0.1"


def validate_dashboard_safety(settings: Settings) -> None:
    if (
        settings.mode is not TradingMode.RESEARCH
        or settings.live_trading_enabled
        or settings.max_trade_size_usd != 0
        or settings.max_daily_loss_usd != 0
    ):
        raise RuntimeError("dashboard safety interlock requires research/live-off/zero limits")


def main() -> None:
    settings = get_settings()
    validate_dashboard_safety(settings)
    uvicorn.run(
        "bp_engine.dashboard.app:create_default_app",
        factory=True,
        host=DASHBOARD_API_HOST,
        port=settings.dashboard_api_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
```

Create `scripts/run_dashboard_api.py` as a thin `main()` launcher only.

- [ ] **Step 4: Run focused tests and Ruff**

Run: `pytest tests/dashboard/test_safety.py tests/test_config.py -q && ruff check src/bp_engine/dashboard src/bp_engine/config.py tests/dashboard/test_safety.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/bp_engine/config.py src/bp_engine/dashboard scripts/run_dashboard_api.py tests/dashboard/test_safety.py tests/test_config.py
git commit -m "feat: add dashboard API safety boundary"
```

---

### Task 2: Implement immutable response models and deterministic performance aggregation

**Files:**
- Create: `src/bp_engine/dashboard/models.py`
- Create: `src/bp_engine/dashboard/metrics.py`
- Test: `tests/dashboard/test_metrics.py`

**Interfaces:**
- Consumes: Python `Decimal`, UTC datetimes, evaluation mappings containing `horizon_seconds`, `calibrated_probability`, `official_target`, `correct`, `calibrated_brier`, `calibrated_log_loss`, and `hypothetical_assumed_cost_pnl`.
- Produces: `PerformanceResponse`, `PerformanceSummary`, `HorizonPerformance`, `CalibrationBucket`; `build_performance(rows: Sequence[Mapping[str, object]]) -> PerformanceResponse`.

- [ ] **Step 1: Write failing metric tests for pending and evaluated states**

```python
from decimal import Decimal

from bp_engine.dashboard.metrics import build_performance


def test_zero_evaluations_is_pending_not_zero_performance() -> None:
    result = build_performance([])
    assert result.status == "pending"
    assert result.evaluated_count == 0
    assert result.accuracy is None
    assert result.calibrated_brier is None
    assert result.paper_pnl_status == "unavailable_until_phase_12"


def test_performance_aggregates_exact_evaluations_and_horizons() -> None:
    rows = [
        {
            "horizon_seconds": 300,
            "calibrated_probability": Decimal("0.800000000000000000"),
            "official_target": 1,
            "correct": True,
            "calibrated_brier": Decimal("0.040000000000000000"),
            "calibrated_log_loss": Decimal("0.223143551314209760"),
            "hypothetical_assumed_cost_pnl": Decimal("0.12"),
        },
        {
            "horizon_seconds": 900,
            "calibrated_probability": Decimal("0.300000000000000000"),
            "official_target": 0,
            "correct": True,
            "calibrated_brier": Decimal("0.090000000000000000"),
            "calibrated_log_loss": Decimal("0.356674943938732379"),
            "hypothetical_assumed_cost_pnl": None,
        },
    ]
    result = build_performance(rows)
    assert result.status == "evaluated"
    assert result.evaluated_count == 2
    assert result.accuracy == Decimal("1")
    assert result.calibrated_brier == Decimal("0.065000000000000000")
    assert result.horizons[0].horizon_seconds == 300
    assert result.horizons[1].horizon_seconds == 900
    assert result.research_hypothetical_assumed_cost_pnl == Decimal("0.12")
    assert result.paper_pnl_status == "unavailable_until_phase_12"
```

Add a calibration-bucket test proving `[0.0,0.1) ... [0.9,1.0]`, deterministic counts, mean prediction, and observed Up frequency.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `pytest tests/dashboard/test_metrics.py -q`

Expected: import failure because models/metrics do not exist.

- [ ] **Step 3: Implement immutable Pydantic models and pure aggregation**

Use frozen models and Decimal JSON serialization:

```python
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, field_serializer


class DashboardModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_decimal(self, value):
        if isinstance(value, Decimal):
            return str(value)
        return value
```

Implement `build_performance` with `Decimal` arithmetic, no float conversion, 10 fixed buckets, per-horizon summaries sorted 300 then 900, and explicit `pending`/paper-P&L states.

- [ ] **Step 4: Run metric tests and Ruff**

Run: `pytest tests/dashboard/test_metrics.py -q && ruff check src/bp_engine/dashboard/models.py src/bp_engine/dashboard/metrics.py tests/dashboard/test_metrics.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bp_engine/dashboard/models.py src/bp_engine/dashboard/metrics.py tests/dashboard/test_metrics.py
git commit -m "feat: add dashboard performance models"
```

---

### Task 3: Implement bounded read-only database queries

**Files:**
- Create: `src/bp_engine/dashboard/queries.py`
- Test: `tests/dashboard/test_queries.py`

**Interfaces:**
- Consumes: existing SQLAlchemy tables `polymarket_markets`, `market_state_1s`, `feed_status`, `feed_incidents`, `live_predictions`, `live_prediction_evaluations` from `bp_engine.storage.schema`.
- Produces: `DashboardQueries(engine: Engine)` with `health()`, `active_markets(now, horizon_seconds, limit)`, `feed_health(now, incident_since)`, `predictions(horizon_seconds, evaluation_state, trade, limit, before_recorded_at)`, and `evaluation_rows()`.

- [ ] **Step 1: Write failing query tests using the existing in-memory schema pattern**

The SQLite tests cover semantics and bounds; a PostgreSQL-only test covers transaction read-only enforcement.

```python
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, insert, text

from bp_engine.dashboard.queries import DashboardQueries
from bp_engine.storage.schema import metadata, polymarket_markets


def make_queries():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return engine, DashboardQueries(engine)


def test_active_markets_excludes_10m_and_honors_limit() -> None:
    engine, queries = make_queries()
    now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    with engine.begin() as connection:
        for index, horizon in enumerate((300, 900, 600)):
            connection.execute(
                insert(polymarket_markets).values(
                    gamma_market_id=f"g-{index}", condition_id=f"c-{index}", slug=f"s-{index}",
                    question=f"q-{index}", horizon_seconds=horizon,
                    start_at=now - timedelta(minutes=1), end_at=now + timedelta(minutes=10),
                    up_token_id=f"u-{index}", down_token_id=f"d-{index}",
                    resolution_source="rules", rules_text="rules", rules_hash=f"r-{index}",
                    active=True, closed=False, accepting_orders=True, resolved_outcome=None,
                    discovered_at=now, updated_at=now,
                )
            )
    rows = queries.active_markets(now=now, horizon_seconds=None, limit=10)
    assert [row["horizon_seconds"] for row in rows] == [300, 900]
```

Add tests that:

- `limit=0` and `limit>100` are rejected before SQL execution;
- latest `market_state_1s` is selected independently for Up and Down token ids;
- prediction-time bid/ask/edge fields are read from `live_predictions`, not current state;
- pending/evaluated history uses only the evaluation child join;
- Decimal values remain `Decimal` in query results;
- feed incident aggregation uses a bounded time window;
- unresolved `polymarket_markets.resolved_outcome` is never used as a prediction evaluation.

For PostgreSQL, use `BP_TEST_DATABASE_URL` and prove writes fail inside the dashboard transaction:

```python
with pytest.raises(Exception):
    with queries.read_only_connection() as connection:
        connection.execute(text("CREATE TEMP TABLE dashboard_write_probe(id int)"))
```

- [ ] **Step 2: Run query tests and confirm RED**

Run: `pytest tests/dashboard/test_queries.py -q`

Expected: import failure because `DashboardQueries` does not exist.

- [ ] **Step 3: Implement read-only connection and bounded SQLAlchemy Core statements**

Core transaction wrapper:

```python
from contextlib import contextmanager
from sqlalchemy.engine import Connection, Engine


class DashboardQueries:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @contextmanager
    def read_only_connection(self):
        with self._engine.connect() as connection:
            transaction = connection.begin()
            try:
                if connection.dialect.name == "postgresql":
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                yield connection
            finally:
                transaction.rollback()
```

All list methods validate `1 <= limit <= 100`; prediction history defaults to 50. Build current-book latest-row selection with a window/subquery ordered by `bucket_at DESC, id DESC`, keyed by exact token `asset_id`. Never infer prices when rows are absent.

- [ ] **Step 4: Run query tests, then full Python regression**

Run: `pytest tests/dashboard/test_queries.py -q && pytest -q`

Expected: dashboard tests pass and prior suite remains green.

- [ ] **Step 5: Commit**

```bash
git add src/bp_engine/dashboard/queries.py tests/dashboard/test_queries.py
git commit -m "feat: add read-only dashboard queries"
```

---

### Task 4: Build dashboard service composition and FastAPI contract

**Files:**
- Create: `src/bp_engine/dashboard/service.py`
- Create: `src/bp_engine/dashboard/app.py`
- Test: `tests/dashboard/test_service.py`
- Test: `tests/dashboard/test_api.py`

**Interfaces:**
- Consumes: `DashboardQueries`, `build_performance`, `Settings`.
- Produces: `DashboardService`; `create_app(service: DashboardService, settings: Settings) -> FastAPI`; `create_default_app() -> FastAPI`.

- [ ] **Step 1: Write failing service tests**

```python
from datetime import UTC, datetime

from bp_engine.config import Settings
from bp_engine.dashboard.service import DashboardService


def test_overview_reports_truthful_mode_and_paper_pnl_status(fake_queries) -> None:
    service = DashboardService(fake_queries, Settings(_env_file=None))
    overview = service.overview(now=datetime(2026, 8, 28, 12, 0, tzinfo=UTC))
    assert overview.mode == "research"
    assert overview.live_trading_enabled is False
    assert overview.verified_horizons_seconds == (300, 900)
    assert overview.paper_pnl_status == "unavailable_until_phase_12"
```

Add populated/empty/pending tests for markets, feeds, history, and performance.

- [ ] **Step 2: Write failing API tests**

Use `fastapi.testclient.TestClient` with a fake service.

```python
def test_api_is_get_only_and_validates_limits(client) -> None:
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/markets?limit=101").status_code == 422
    assert client.post("/api/v1/overview").status_code == 405


def test_database_failure_is_sanitized(client_with_database_failure) -> None:
    response = client_with_database_failure.get("/api/v1/overview")
    assert response.status_code == 503
    body = response.text.lower()
    assert "postgresql" not in body
    assert "password" not in body
    assert "database_url" not in body
```

- [ ] **Step 3: Run service/API tests and confirm RED**

Run: `pytest tests/dashboard/test_service.py tests/dashboard/test_api.py -q`

Expected: imports/routes fail because service/app do not exist.

- [ ] **Step 4: Implement service and API**

FastAPI route contract:

```python
@app.get("/health")
def health() -> HealthResponse: ...

@app.get("/api/v1/overview")
def overview() -> OverviewResponse: ...

@app.get("/api/v1/markets")
def markets(horizon_seconds: Literal[300, 900] | None = None, limit: int = Query(25, ge=1, le=100)): ...

@app.get("/api/v1/predictions")
def predictions(
    horizon_seconds: Literal[300, 900] | None = None,
    evaluation_state: Literal["evaluated", "pending"] | None = None,
    trade: bool | None = None,
    limit: int = Query(50, ge=1, le=100),
    before_recorded_at: datetime | None = None,
): ...

@app.get("/api/v1/performance")
def performance() -> PerformanceResponse: ...
```

`create_default_app()` uses `create_engine(settings.database_url, pool_pre_ping=True)` and the validated research-only settings. Catch `SQLAlchemyError` at the API boundary and return only `{"detail":"dashboard data unavailable"}` with 503.

- [ ] **Step 5: Run dashboard API tests and full regression**

Run: `pytest tests/dashboard -q && ruff check . && pytest -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/bp_engine/dashboard/service.py src/bp_engine/dashboard/app.py tests/dashboard/test_service.py tests/dashboard/test_api.py
git commit -m "feat: expose read-only dashboard API"
```

---

### Task 5: Build the Next.js operator dashboard with exact dependency pins

**Files:**
- Delete: `apps/dashboard/.gitkeep`
- Create: `apps/dashboard/package.json`
- Create: `apps/dashboard/package-lock.json`
- Create: `apps/dashboard/next.config.ts`
- Create: `apps/dashboard/tsconfig.json`
- Create: `apps/dashboard/next-env.d.ts`
- Create: `apps/dashboard/app/layout.tsx`
- Create: `apps/dashboard/app/page.tsx`
- Create: `apps/dashboard/app/globals.css`
- Create: `apps/dashboard/lib/api.ts`
- Create: `apps/dashboard/lib/types.ts`
- Create: `apps/dashboard/lib/format.ts`
- Create: `apps/dashboard/components/ModeBanner.tsx`
- Create: `apps/dashboard/components/SummaryCards.tsx`
- Create: `apps/dashboard/components/MarketsTable.tsx`
- Create: `apps/dashboard/components/PerformancePanel.tsx`
- Create: `apps/dashboard/components/FeedHealthTable.tsx`
- Create: `apps/dashboard/components/PredictionHistory.tsx`
- Create: `apps/dashboard/tests/format.test.ts`

**Interfaces:**
- Consumes: dashboard API `/api/v1/overview`, `/markets`, `/performance`, `/predictions` over server-side loopback.
- Produces: production Next.js server on loopback; pure formatting helpers `formatDecimal`, `formatProbability`, `formatUtc`, `formatAgeSeconds`.

- [ ] **Step 1: Create package manifest with exact current stable pins**

```json
{
  "name": "bp-dashboard",
  "private": true,
  "version": "0.1.0",
  "scripts": {
    "dev": "next dev -H 127.0.0.1 -p 3000",
    "build": "next build",
    "start": "next start -H 127.0.0.1 -p 3000",
    "test": "node --experimental-strip-types --test tests/*.test.ts"
  },
  "dependencies": {
    "next": "16.3.3",
    "react": "19.2.8",
    "react-dom": "19.2.8"
  },
  "devDependencies": {
    "@types/node": "26.4.0",
    "@types/react": "19.2.18",
    "@types/react-dom": "19.2.4",
    "typescript": "7.0.2"
  }
}
```

Generate and commit `package-lock.json` with `npm install --package-lock-only`; CI must use `npm ci`, never floating install.

- [ ] **Step 2: Write failing pure formatter tests**

```ts
import assert from "node:assert/strict";
import test from "node:test";
import { formatProbability, formatUtc } from "../lib/format.ts";

test("probabilities are formatted without changing the source value", () => {
  assert.equal(formatProbability("0.812345678901234567"), "81.23%");
});

test("timestamps are explicit UTC", () => {
  assert.match(formatUtc("2026-08-28T12:00:00Z"), /UTC$/);
});
```

Run: `cd apps/dashboard && npm test`

Expected: FAIL because formatter module does not exist.

- [ ] **Step 3: Implement API types/client and formatters**

`lib/api.ts` must read the API base URL only server-side:

```ts
const API_BASE = process.env.BP_DASHBOARD_API_URL ?? "http://127.0.0.1:8787";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`dashboard API ${response.status}`);
  return response.json() as Promise<T>;
}
```

Never prefix the API URL variable with `NEXT_PUBLIC_`.

- [ ] **Step 4: Implement operator components and truthful states**

`ModeBanner` must render a literal prominent state such as:

```tsx
<div className="mode-banner" data-live={overview.live_trading_enabled}>
  <strong>{overview.mode.toUpperCase()}</strong>
  <span>{overview.live_trading_enabled ? "LIVE TRADING ON" : "LIVE TRADING OFF"}</span>
</div>
```

`PerformancePanel` must branch on `status === "pending"` and show `Awaiting official evaluations`; it must render `Paper P&L: unavailable until Phase 12` from the API availability state. `MarketsTable` must place `Current book` and `Prediction-time` values in separately labeled column groups.

- [ ] **Step 5: Implement one-page dashboard composition**

`app/page.tsx` fetches overview, markets, performance, and recent predictions concurrently with `Promise.all`. On fetch error, render a visible stale/unavailable operator state without fake zero values.

- [ ] **Step 6: Run frontend test/build**

Run: `cd apps/dashboard && npm test && npm run build`

Expected: PASS with a production Next.js build.

- [ ] **Step 7: Commit**

```bash
git add apps/dashboard
git commit -m "feat: build Phase 11 operator dashboard"
```

---

### Task 6: Add loopback deployment assets and CI coverage

**Files:**
- Create: `deploy/bp-dashboard-api.service`
- Create: `deploy/bp-dashboard-web.service`
- Modify: `scripts/deploy/bootstrap_ubuntu.sh`
- Modify: `.github/workflows/ci.yml`
- Test: `tests/deploy/test_phase11_dashboard_assets.py`

**Interfaces:**
- Consumes: Python dashboard launcher; built Next.js app; existing `bp` service account and `/opt/bp` deployment layout.
- Produces: `bp-dashboard-api` on `127.0.0.1:8787`, `bp-dashboard-web` on `127.0.0.1:3000`, CI Python+frontend gates.

- [ ] **Step 1: Write failing deployment asset tests**

```python
from pathlib import Path


def test_dashboard_services_are_loopback_and_unprivileged() -> None:
    api = Path("deploy/bp-dashboard-api.service").read_text()
    web = Path("deploy/bp-dashboard-web.service").read_text()
    assert "User=bp" in api and "User=bp" in web
    assert "127.0.0.1" in api and "127.0.0.1" in web
    assert "LIVE_TRADING_ENABLED=true" not in api + web


def test_ci_builds_dashboard_with_locked_dependencies() -> None:
    ci = Path(".github/workflows/ci.yml").read_text()
    assert "actions/setup-node" in ci
    assert "npm ci" in ci
    assert "npm test" in ci
    assert "npm run build" in ci
```

- [ ] **Step 2: Run focused deployment tests and confirm RED**

Run: `pytest tests/deploy/test_phase11_dashboard_assets.py -q`

Expected: missing service files/CI commands.

- [ ] **Step 3: Add systemd units and bootstrap install**

API unit core:

```ini
[Service]
Type=simple
User=bp
Group=bp
WorkingDirectory=/opt/bp/current
EnvironmentFile=/etc/bp/bp.env
ExecStart=/opt/bp/.venv/bin/python /opt/bp/current/scripts/run_dashboard_api.py
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
```

Web unit core:

```ini
[Service]
Type=simple
User=bp
Group=bp
WorkingDirectory=/opt/bp/current/apps/dashboard
Environment=BP_DASHBOARD_API_URL=http://127.0.0.1:8787
ExecStart=/usr/bin/npm start
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
```

Keep `npm start` package script loopback-bound. Do not add a public listener.

- [ ] **Step 4: Extend CI**

After Python install, add Node 22.16.0 setup and locked frontend gates:

```yaml
      - name: Set up Node
        uses: actions/setup-node@v4
        with:
          node-version: "22.16.0"
          cache: npm
          cache-dependency-path: apps/dashboard/package-lock.json

      - name: Install dashboard
        working-directory: apps/dashboard
        run: npm ci

      - name: Test dashboard
        working-directory: apps/dashboard
        run: npm test

      - name: Build dashboard
        working-directory: apps/dashboard
        run: npm run build
```

Add `bash -n scripts/deploy/phase11_host_acceptance.sh` and `phase11_cloudshell_accept.sh` once those files exist in Task 7.

- [ ] **Step 5: Run deployment tests and syntax checks**

Run: `pytest tests/deploy/test_phase11_dashboard_assets.py -q && systemd-analyze verify deploy/bp-dashboard-api.service deploy/bp-dashboard-web.service || true`

Then run full Python and frontend gates.

- [ ] **Step 6: Commit**

```bash
git add deploy/bp-dashboard-api.service deploy/bp-dashboard-web.service scripts/deploy/bootstrap_ubuntu.sh .github/workflows/ci.yml tests/deploy/test_phase11_dashboard_assets.py
git commit -m "build: deploy Phase 11 dashboard services"
```

---

### Task 7: Add exact-SHA host acceptance and Cloud Shell wrapper

**Files:**
- Create: `scripts/deploy/phase11_host_acceptance.sh`
- Create: `scripts/deploy/phase11_cloudshell_accept.sh`
- Test: `tests/deploy/test_phase11_host_acceptance_assets.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: exact candidate SHA, existing `/opt/bp` deployment conventions, systemd services, PostgreSQL, loopback API/web endpoints.
- Produces: machine-readable Phase 11 acceptance output ending in `PHASE11_HOST_ACCEPTANCE=PASS` only when all safety/read-only/UI checks pass.

- [ ] **Step 1: Write failing acceptance-asset tests**

Assert scripts contain checks for exact SHA, research/live-off/zero limits, recorder/predictor active before/after, unprivileged dashboard users, loopback sockets, API write rejection, health/overview/markets/predictions/performance endpoints, Paper P&L unavailable semantics, frontend HTTP success, no order side effects, and disk health.

- [ ] **Step 2: Run focused test and confirm RED**

Run: `pytest tests/deploy/test_phase11_host_acceptance_assets.py -q`

Expected: missing scripts.

- [ ] **Step 3: Implement host acceptance fail-closed**

The host script must use `set -euo pipefail`, accept an expected SHA, and refuse a dirty or mismatched checkout. It must record before/after safety values and service states. The database write probe must connect using the dashboard API's read-only transaction path, not a privileged direct-write session, and report `READ_ONLY_WRITE_REJECTED=1` only when PostgreSQL rejects the attempted write.

Required terminal output keys include:

```text
MODE=research
LIVE_TRADING_ENABLED=false
MAX_TRADE_SIZE_USD=0
MAX_DAILY_LOSS_USD=0
DASHBOARD_API_USER=bp
DASHBOARD_WEB_USER=bp
DASHBOARD_API_LOOPBACK=1
DASHBOARD_WEB_LOOPBACK=1
READ_ONLY_WRITE_REJECTED=1
OVERVIEW_OK=1
MARKETS_OK=1
PREDICTIONS_OK=1
PERFORMANCE_OK=1
PAPER_PNL_STATUS=unavailable_until_phase_12
ORDER_SIDE_EFFECT_VIOLATIONS=0
PHASE11_HOST_ACCEPTANCE=PASS
```

Do not require evaluations or positive P&L.

- [ ] **Step 4: Implement exact-candidate Cloud Shell wrapper**

Follow the already-proven pattern: root-owned exact worktree -> `git archive` exact candidate -> temporary `bp`-owned source stage -> install/build candidate -> run host acceptance -> clean temporary stage. Do not mutate production prediction rows.

- [ ] **Step 5: Run shell syntax/tests and update CI validation list**

Run: `bash -n scripts/deploy/phase11_host_acceptance.sh scripts/deploy/phase11_cloudshell_accept.sh && pytest tests/deploy/test_phase11_host_acceptance_assets.py -q`

- [ ] **Step 6: Commit**

```bash
git add scripts/deploy/phase11_host_acceptance.sh scripts/deploy/phase11_cloudshell_accept.sh tests/deploy/test_phase11_host_acceptance_assets.py .github/workflows/ci.yml
git commit -m "test: add Phase 11 host acceptance gate"
```

---

### Task 8: Run exact-head CI/smokes, open the Phase 11 PR, and collect host evidence

**Files:**
- No code file required before evidence.

**Interfaces:**
- Consumes: exact branch head after Tasks 1-7.
- Produces: green CI and existing smoke workflows, draft PR, then exact production-host Phase 11 acceptance evidence.

- [ ] **Step 1: Run full repository gates on exact head**

Required evidence:

```text
ruff check .
pytest
apps/dashboard: npm ci && npm test && npm run build
bash syntax for all deployment scripts
existing docker compose config validation
health output: mode=research, live_trading_enabled=false
```

- [ ] **Step 2: Re-run existing operational smoke workflows**

Require Historical Backfill Smoke, Live Recorder Smoke, and Recorder Short Soak to stay green because Phase 11 must not regress the existing data pipeline.

- [ ] **Step 3: Open Phase 11 PR as draft**

PR body must identify the candidate SHA, design/plan paths, read-only architecture, frontend dependency pins, CI/smoke evidence, and explicitly state that host acceptance is still pending.

- [ ] **Step 4: Run exact-SHA production host acceptance**

Use `phase11_cloudshell_accept.sh <exact-candidate-sha>` and retain sanitized evidence. A PASS must demonstrate genuine production-host data through the API/UI and all safety keys from Task 7.

- [ ] **Step 5: If host acceptance fails, fix by TDD on the same Phase 11 branch**

Do not weaken the acceptance gate, remove read-only checks, invent paper P&L, or reinterpret missing data as success. Add a regression reproducing the observed failure, implement the smallest fix, rerun exact-head CI/smokes, then rerun host acceptance on the new exact SHA.

---

### Task 9: Close Phase 11 only from accepted evidence

**Files:**
- Create: `docs/evidence/phase-11-closeout-20260828.json`
- Modify: `PROJECT_STATE.json`
- Modify: `README.md`
- Modify: `START-HERE.md`
- Modify: `docs/BUILD-ORDER.md`
- Modify: `docs/CHANGELOG.md`

**Interfaces:**
- Consumes: exact accepted Phase 11 operational SHA and all host/CI evidence.
- Produces: Phase 11 complete / Phase 12 paper execution ready repository state; live trading remains disabled.

- [ ] **Step 1: Write sanitized closeout evidence**

Record exact candidate SHA, workflow run IDs, frontend build/test result, host service users/binds, read-only write rejection, endpoint status, production row/count snapshots, Paper P&L availability state, recorder/predictor before/after state, disk health, and `ORDER_SIDE_EFFECT_VIOLATIONS=0`.

- [ ] **Step 2: Advance project state to Phase 12 only after PASS**

Set current phase to 12 / Paper execution, preserve `trading_mode=RESEARCH` and `live_trading_enabled=false`, and retain Phase 11 checkpoint evidence. Do not mark paper trading complete merely because the dashboard displays its unavailable state.

- [ ] **Step 3: Update human-readable docs/changelog**

State clearly that Phase 11 is an observability milestone, not a profitability or execution claim. Paper execution remains Phase 12; live readiness remains Phase 14; controlled real-money launch remains Phase 15 with explicit authorization.

- [ ] **Step 4: Run final exact-head regression after documentation closeout**

Require full CI, frontend build, Historical Backfill Smoke, Live Recorder Smoke, and Recorder Short Soak green on the documentation/state head. Do not claim the documentation head itself was production-host-run; preserve the exact accepted operational SHA separately.

- [ ] **Step 5: Update PR body with closeout evidence and mark ready for integration**

Only after all final exact-head gates are green.

---

## Plan Self-Review

- **Spec coverage:** all design sections are mapped: source-of-truth tables in Tasks 2-4; read-only safety in Tasks 1, 3, 4, 6, 7; Next.js information architecture in Task 5; refresh/error truthfulness in Task 5; deployment in Task 6; exact-host acceptance in Task 7; evidence/phase boundary in Task 9.
- **No fabricated P&L:** the API and UI use `unavailable_until_phase_12` for paper P&L and keep Phase 10 hypothetical research P&L separately labeled.
- **No hidden mutation path:** API routes are GET-only, dashboard SQL transactions are read-only on PostgreSQL, and no dashboard migration or execution module is added.
- **Type consistency:** verified horizons are integers `300`/`900` through queries/API/frontend; decimals remain `Decimal` in Python and strings in JSON/TypeScript.
- **Bounded load:** all market/history endpoints enforce `1..100` limits; incident aggregation uses a bounded window; no raw event-history endpoint is added.
- **Dependency reproducibility:** backend versions are exact for FastAPI/Uvicorn; frontend versions are exact and locked by `package-lock.json`; CI uses `npm ci`.
- **Regression protection:** existing Python suite and existing recorder/backfill smokes remain required at candidate and final closeout heads.
