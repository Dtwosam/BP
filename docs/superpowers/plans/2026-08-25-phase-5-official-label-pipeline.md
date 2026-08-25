# Phase 5 Official Outcome/Label Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build deterministic, immutable, leakage-safe official Polymarket outcome labels from Phase 4 Gamma snapshots, with an offline CLI and PostgreSQL acceptance proof.

**Architecture:** Add an additive `market_labels` table keyed by `(condition_id, label_version)`. A pure label-selection service parses stored Gamma snapshots with the existing parser, rejects pre-end/conflicting resolution evidence, chooses the earliest eligible resolved snapshot, and persists the semantic label immutably. Generation is offline and network-free.

**Tech Stack:** Python 3.12, SQLAlchemy Core, PostgreSQL 16, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-25-phase-5-official-label-pipeline-design.md`

## Global Constraints

- Authoritative target is the official Polymarket resolved outcome only.
- V1 label version is `official-outcome-v1`; source is `polymarket_gamma_snapshot`.
- Official start/end reference prices remain NULL unless independently verified from first-party resolution data.
- A resolved snapshot observed before market end is a hard leakage/data-integrity failure.
- Identical reruns are no-ops; changed semantic labels at the same natural key fail closed.
- Phase 6 feature engineering, models, backtests, predictions, execution, and live trading remain out of scope.

---

### Task 1: Add the label storage contract

**Files:**
- Create: `migrations/0005_market_labels.sql`
- Modify: `src/bp_engine/storage/schema.py`
- Create: `tests/labels/test_label_schema.py`

**Interfaces:**
- Produces table `market_labels` with unique `(condition_id, label_version)` and provenance columns required by the spec.

- [ ] **Step 1: Write failing schema tests**

Assert that `market_labels` exists in SQLAlchemy metadata, includes `condition_id`, `official_outcome`, `label_version`, `source_snapshot_sha256`, `source_observed_at`, nullable `start_reference`/`end_reference`, and has a unique constraint on `(condition_id, label_version)`.

- [ ] **Step 2: Run RED**

Run: `pytest tests/labels/test_label_schema.py -v`
Expected: FAIL because `market_labels` and migration 0005 do not exist.

- [ ] **Step 3: Implement minimal additive schema**

Create the table with immutable semantic fields and no destructive migration statements. Use timezone-aware timestamps and numeric nullable reference columns.

- [ ] **Step 4: Run GREEN**

Run: `pytest tests/labels/test_label_schema.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: add immutable market label schema`

---

### Task 2: Add immutable label repository semantics

**Files:**
- Create: `src/bp_engine/labels/models.py`
- Create: `src/bp_engine/labels/repository.py`
- Create: `src/bp_engine/labels/__init__.py`
- Create: `tests/labels/test_label_repository.py`

**Interfaces:**
- Produces `MarketLabel`, `LabelConflict`, `LabelStoreResult`, and `MarketLabelRepository.store(connection, label)`.

- [ ] **Step 1: Write failing repository tests**

Tests must prove first insert returns created, exact semantic rerun returns existing without changing `generated_at`, and changed outcome/rules/window/source snapshot at the same `(condition_id, label_version)` raises `LabelConflict`.

- [ ] **Step 2: Run RED**

Run: `pytest tests/labels/test_label_repository.py -v`
Expected: FAIL because label repository types do not exist.

- [ ] **Step 3: Implement minimal repository**

Validate timezone-aware `market_start_at`, `market_end_at`, `source_observed_at`, and `generated_at`; compare all semantic fields except `generated_at`; insert only when natural key is absent.

- [ ] **Step 4: Run GREEN**

Run: `pytest tests/labels/test_label_repository.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: enforce immutable official labels`

---

### Task 3: Build deterministic leakage-safe snapshot selection

**Files:**
- Create: `src/bp_engine/labels/service.py`
- Create: `tests/labels/test_label_service.py`

**Interfaces:**
- Produces `LabelSourceConflict`, `LabelLeakageError`, `LabelGenerationStats`, and `generate_labels(connection, start, end, generated_at)`.
- Consumes `polymarket_market_snapshots`, existing `parse_gamma_market`, and `MarketLabelRepository`.

- [ ] **Step 1: Write failing leakage/selection tests**

Cover: open market skipped; ambiguous closed outcome skipped; resolved snapshot before `window_end_at` raises `LabelLeakageError`; snapshot envelope condition/slug mismatch fails closed; two eligible snapshots with conflicting outcome/rules/window raise `LabelSourceConflict`; agreeing snapshots choose earliest `(downloaded_at, id)`; only markets with parsed `window_start_at` in `[start,end)` are considered; reference prices remain NULL.

- [ ] **Step 2: Run RED**

Run: `pytest tests/labels/test_label_service.py -v`
Expected: FAIL because generation service does not exist.

- [ ] **Step 3: Implement minimal service**

Load stored Gamma snapshots in deterministic order, parse with `parse_gamma_market`, group by condition, enforce the source/leakage contract, choose the earliest eligible resolved snapshot, store `official-outcome-v1` labels, and return inserted/existing/skipped/conditions counts.

- [ ] **Step 4: Run GREEN**

Run: `pytest tests/labels/test_label_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: generate leakage-safe official labels`

---

### Task 4: Add offline operator CLI

**Files:**
- Create: `src/bp_engine/labels/cli.py`
- Create: `scripts/generate_labels.py`
- Create: `tests/labels/test_label_cli.py`

**Interfaces:**
- Command: `python scripts/generate_labels.py --start <ISO8601> --end <ISO8601> --env-file <path>`.
- Output: JSON counts only; no network calls.

- [ ] **Step 1: Write failing CLI tests**

Prove naive timestamps are rejected, `start >= end` is rejected, environment/database conventions are reused, and successful generation emits deterministic JSON stats without constructing an HTTP client.

- [ ] **Step 2: Run RED**

Run: `pytest tests/labels/test_label_cli.py -v`
Expected: FAIL because CLI does not exist.

- [ ] **Step 3: Implement minimal CLI**

Parse aware datetimes, open the configured SQLAlchemy transaction, call `generate_labels`, commit on success, and serialize stats.

- [ ] **Step 4: Run GREEN**

Run: `pytest tests/labels/test_label_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: add offline label generation CLI`

---

### Task 5: PostgreSQL rerun/leakage acceptance and docs

**Files:**
- Create: `tests/labels/test_postgres_labels.py`
- Modify: `.github/workflows/ci.yml`
- Create: `docs/PHASE-5-DEPLOYMENT.md`

**Interfaces:**
- PostgreSQL test applies `0005_market_labels.sql` to a real Postgres 16 service and runs generation twice against stored snapshots.

- [ ] **Step 1: Write failing PostgreSQL acceptance tests**

Insert realistic Phase 4 Gamma snapshots into `polymarket_market_snapshots`, generate labels, verify first run inserts labels and second run inserts zero, verify unresolved markets remain absent, verify persisted source SHA/rules/source timestamps, and verify a conflicting stored label causes `LabelConflict`.

- [ ] **Step 2: Run RED**

Run: `pytest tests/labels/test_postgres_labels.py -v`
Expected: FAIL until migration/repository/service integration is complete.

- [ ] **Step 3: Complete integration and runbook**

Ensure CI applies/tests migration 0005, document host command and acceptance fields, explicitly document NULL official reference prices in V1, and retain live-trading-disabled constraints.

- [ ] **Step 4: Run full verification**

Run: `ruff check . && pytest`
Expected: Ruff clean and all tests PASS.

- [ ] **Step 5: Open draft Phase 5 PR**

PR body must state the authoritative outcome source, leakage guarantees, immutable rerun semantics, NULL reference-price policy, and that Phase 6 remains blocked pending Phase 5 host/closeout evidence.
