# Phase 0 Repository Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bootstrap a safe, testable Python repository foundation for the BTC Polymarket Prediction Engine without implementing market collection or prediction logic yet.

**Architecture:** A small `bp_engine` Python package owns configuration, structured logging, and a machine-readable health command. Docker Compose provides local PostgreSQL, while CI runs unit tests and linting. Source-of-truth documents and machine-readable project state live in the repository so every future chat can resume from the same state.

**Tech Stack:** Python 3.12+, Pydantic Settings, pytest, Ruff, PostgreSQL 16 via Docker Compose, GitHub Actions.

**Spec:** `docs/MASTER-SOURCE-OF-TRUTH.md`

## Global Constraints

- Initial trading mode is `RESEARCH`.
- Live trading remains disabled.
- Active horizons are `5m` and `15m`; `10m` is optional/unverified.
- Canonical timestamps are UTC.
- No secrets may be committed.
- The initial validation budget target is $0/month.
- Phase 0 must not add Polymarket collectors, BTC collectors, models, or live trading.

---

### Task 1: Safe runtime configuration

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `src/bp_engine/__init__.py`
- Create: `src/bp_engine/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `TradingMode`, `Settings`, and `get_settings()` for every later subsystem.

- [ ] Write tests proving safe defaults: research mode, live disabled, active horizons `5m/15m`, optional `10m`, UTC.
- [ ] Run `pytest tests/test_config.py -v` and verify failure because `bp_engine.config` does not exist.
- [ ] Implement the minimal Pydantic settings model and cached `get_settings()`.
- [ ] Run the test and verify it passes.
- [ ] Run Ruff on the touched Python files.

### Task 2: Machine-readable health command and structured logging

**Files:**
- Create: `src/bp_engine/logging.py`
- Create: `src/bp_engine/health.py`
- Test: `tests/test_health.py`

**Interfaces:**
- Consumes: `get_settings()` from Task 1.
- Produces: `build_health_payload(settings: Settings) -> dict[str, object]` and `python -m bp_engine.health`.

- [ ] Write tests proving health reports `ok`, current mode, live-trading state, configured horizons, and UTC.
- [ ] Run `pytest tests/test_health.py -v` and verify failure because health behavior is missing.
- [ ] Implement minimal JSON logging helpers and health payload/CLI.
- [ ] Run health tests and the full suite.
- [ ] Run `python -m bp_engine.health` and confirm valid JSON output.

### Task 3: Local PostgreSQL and repository safety scaffolding

**Files:**
- Create: `docker-compose.yml`
- Create: `.gitignore`
- Create: `data/.gitkeep`
- Create: `apps/dashboard/.gitkeep`
- Create: `scripts/.gitkeep`
- Create: `migrations/.gitkeep`

**Interfaces:**
- Produces: a local PostgreSQL 16 service reachable through `DATABASE_URL` from `.env.example`.

- [ ] Add PostgreSQL Compose service with healthcheck and persistent named volume.
- [ ] Add ignore rules for `.env`, virtualenvs, Python caches, test/lint caches, generated data, and local worktrees.
- [ ] Validate Compose syntax when Docker Compose is available; otherwise parse/review the YAML and record the environment limitation.

### Task 4: CI and contributor entrypoint

**Files:**
- Create: `.github/workflows/ci.yml`
- Replace: `README.md`

**Interfaces:**
- Produces: documented local commands and CI enforcing `pytest` + `ruff check` on Python 3.12.

- [ ] Add GitHub Actions workflow for checkout, Python 3.12, editable dev install, Ruff, and pytest.
- [ ] Write README with project purpose, source-of-truth reading order, Phase 0 commands, safety note, and current status.
- [ ] Verify editable installation, linting, tests, and health command locally.

### Task 5: Project memory and phase handoff

**Files:**
- Create: `docs/DECISION-LOG.md`
- Create: `docs/CHANGELOG.md`
- Modify: `PROJECT_STATE.json`

**Interfaces:**
- Produces: exact handoff state for the next chat/phase.

- [ ] Record initial decisions and Phase 0 implementation in the changelog.
- [ ] Update project state only after all Phase 0 verification passes.
- [ ] Set current phase to Phase 1 (`Polymarket market discovery`) and keep trading mode `RESEARCH`.
- [ ] Re-run full verification after state/document changes.
