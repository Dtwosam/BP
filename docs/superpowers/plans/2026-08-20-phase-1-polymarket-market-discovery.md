# Phase 1 Polymarket Market Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically discover configured BTC Up/Down Polymarket markets, parse their official Gamma metadata safely, and persist normalized market definitions before the recorder is built.

**Architecture:** Generate deterministic recurring-market slug candidates from UTC-aligned windows, resolve those slugs through Polymarket's official Gamma `GET /markets/slug/{slug}` endpoint, normalize the response into a strict domain model, then upsert it into `polymarket_markets`. The parser stores an exact rules fingerprint so changes in resolution rules remain visible and later training data can be segmented by rule version.

**Tech Stack:** Python 3.12+, Pydantic, httpx, SQLAlchemy 2, PostgreSQL 16, pytest.

**Spec:** `docs/MASTER-SOURCE-OF-TRUTH.md`

**Verification result:** GitHub Actions captured authentic live Gamma 5m/15m payloads on 20 August 2026. Focused fixtures were updated with authentic identifiers/token IDs/rules, and the full local Phase 0+1 suite passes (15 tests).

## Global Constraints

- Trading mode remains `RESEARCH`; no order placement code is added.
- Active configured horizons remain `5m` and `15m`; `10m` remains optional/unverified.
- BTC Up/Down market window timestamps are derived from the recurring slug epoch and configured horizon, not Gamma's market-creation timestamp.
- Outcomes and CLOB token IDs must be mapped by outcome label, never by assuming Up is always array index 0.
- Exact `resolutionSource` and rules text are stored and hashed.
- A market with malformed/missing binary Up/Down token metadata is rejected rather than guessed.
- Canonical timestamps are timezone-aware UTC.
- Current external rule details are factual inputs that can change; code must preserve the returned rule text rather than hard-code a trading interpretation.

---

### Task 1: Recurring market domain model and Gamma parser

**Files:**
- Create: `src/bp_engine/polymarket/__init__.py`
- Create: `src/bp_engine/polymarket/models.py`
- Create: `src/bp_engine/polymarket/parsing.py`
- Create: `tests/fixtures/polymarket/btc_updown_5m_gamma.json`
- Create: `tests/fixtures/polymarket/btc_updown_15m_gamma.json`
- Create: `tests/polymarket/test_parsing.py`

**Interfaces:**
- Produces: `PolymarketMarket` and `parse_gamma_market(payload: Mapping[str, Any]) -> PolymarketMarket`.
- Produces: `parse_horizon_slug(slug: str) -> tuple[int, datetime]`, where the datetime is the UTC market-window start.

- [x] Write parser tests for 5m and 15m fixtures, UTC window calculation, outcome-to-token mapping, rules hash, and rejection of mismatched token arrays.
- [x] Run parser tests and confirm failure because the parser does not exist.
- [x] Implement the strict domain model and parser with JSON-array decoding and SHA-256 rule fingerprinting.
- [x] Re-run parser tests and confirm they pass.

### Task 2: Gamma client and deterministic slug discovery

**Files:**
- Create: `src/bp_engine/polymarket/gamma.py`
- Create: `src/bp_engine/polymarket/discovery.py`
- Create: `tests/polymarket/test_gamma.py`
- Create: `tests/polymarket/test_discovery.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `GammaClient.get_market_by_slug(slug: str) -> dict[str, Any] | None`.
- Produces: `build_candidate_slugs(now: datetime, horizons: Sequence[str], offsets: Sequence[int] = (-1, 0, 1)) -> list[str]`.
- Produces: `discover_btc_markets(client: GammaClientProtocol, now: datetime, horizons: Sequence[str]) -> list[PolymarketMarket]`.

- [x] Write tests using `httpx.MockTransport` proving the official `/markets/slug/{slug}` path, 404 handling, candidate UTC flooring for both horizons, and de-duplication of discovered markets.
- [x] Run both new test files and verify they fail because client/discovery code is missing.
- [x] Add `httpx` as a runtime dependency and implement only the tested client/discovery behavior.
- [x] Re-run the tests and confirm they pass.

### Task 3: `polymarket_markets` storage and rule-change protection

**Files:**
- Create: `src/bp_engine/storage/__init__.py`
- Create: `src/bp_engine/storage/schema.py`
- Create: `src/bp_engine/storage/polymarket_markets.py`
- Create: `migrations/0001_polymarket_markets.sql`
- Create: `tests/storage/test_polymarket_markets.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: SQLAlchemy table `polymarket_markets`.
- Produces: `PolymarketMarketRepository.upsert(connection, market, observed_at) -> UpsertResult`.
- Raises: `RuleChangeDetected` when the same condition ID arrives later with a different rules fingerprint, preventing silent rule overwrite.

- [x] Write SQLite-backed repository tests for first insert, idempotent update, active/closed status refresh, and rule-change rejection.
- [x] Run the repository test and verify it fails because storage code is missing.
- [x] Add SQLAlchemy and PostgreSQL psycopg runtime dependencies; implement table, migration, repository, and rule-change guard.
- [x] Re-run repository tests and confirm they pass.

### Task 4: Discovery service orchestration

**Files:**
- Create: `src/bp_engine/polymarket/service.py`
- Create: `tests/polymarket/test_service.py`

**Interfaces:**
- Produces: `MarketDiscoveryService.discover_and_store(now: datetime) -> list[PolymarketMarket]`.
- Consumes: configured active horizons, a Gamma client, SQLAlchemy engine/repository.

- [x] Write a test proving only configured active horizons are queried and each discovered market is persisted exactly once.
- [x] Run the service test and verify it fails because orchestration is missing.
- [x] Implement the minimal service with dependency injection and one transaction per discovery run.
- [x] Re-run service and full test suite.

### Task 5: External-rule correction, documentation, and phase handoff

**Files:**
- Modify: `docs/MASTER-SOURCE-OF-TRUTH.md`
- Modify: `docs/DECISION-LOG.md`
- Modify: `docs/CHANGELOG.md`
- Modify: `PROJECT_STATE.json`
- Modify: `README.md`

- [x] Record the observed 2026 rule-version change: older short markets used the regular Chainlink BTC/USD stream, while current 5m/15m examples use the Chainlink BTC/USD 60-second TWAP stream.
- [x] Correct source-of-truth wording so official market outcome remains authoritative and returned rule text/fingerprint determines rule version.
- [x] Run GitHub Actions live Gamma smoke and capture authentic 5m/15m payloads.
- [x] Replace representative synthetic fixture identifiers with authentic captured values.
- [x] Update project state to Phase 2 only after all Phase 1 tests pass.
- [x] Run fresh verification: test suite, compile check, health check, JSON/config checks.
- [x] Publish Phase 1 on `build/phase-1-market-discovery` without enabling live trading.
