# Prospective Paper Evidence Monitor V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only, reproducible report for prospective post-Phase-14 paper evidence so the Master live gate can be re-evaluated from immutable data without activating live trading.

**Architecture:** Keep evidence calculation separate from data access. A pure summarizer computes prediction calibration and settled-paper P&L uncertainty; a PostgreSQL reader selects only rows whose immutable source prediction/order begins at or after an explicit `since` boundary; a thin CLI emits JSON and reuses the existing paper reconciliation checker. The report never declares a magic sample size or authorizes live trading.

**Tech Stack:** Python 3.12, SQLAlchemy 2, PostgreSQL 16, NumPy, pytest, Ruff.

**Spec:** `docs/MASTER-SOURCE-OF-TRUTH.md` live-gate requirements and `docs/evidence/phase-14-closeout-20260830.json`.

## Global Constraints

- `MODE=research` remains required for production paper operation.
- `LIVE_TRADING_ENABLED=false` remains required.
- `MAX_TRADE_SIZE_USD=0` and `MAX_DAILY_LOSS_USD=0` remain required.
- No wallet, signing, order-submission, geoblock bypass, or secret-bearing CLI path is added.
- Prospective evidence must use an explicit timezone-aware start boundary; older paper rows must not contaminate the sample.
- No fixed sample-count threshold may be invented; uncertainty must be reported explicitly.
- All production behavior changes use RED → GREEN → exact-head CI.

---

### Task 1: Pure prospective evidence summary

**Files:**
- Create: `src/bp_engine/execution/evidence.py`
- Test: `tests/execution/test_prospective_evidence.py`

**Interfaces:**
- Consumes: immutable prediction/evaluation/settled-trade rows and reconciliation summary.
- Produces: `summarize_prospective_paper_evidence(...) -> ProspectivePaperEvidenceReport`.

- [x] Write the failing summary tests.
- [x] Verify RED because the module is missing.
- [x] Implement deterministic P&L bootstrap and prospective calibration metrics.
- [x] Verify the full CI lane is green.

### Task 2: Explicit-boundary PostgreSQL reader

**Files:**
- Modify: `src/bp_engine/execution/evidence.py`
- Create: `tests/execution/test_prospective_evidence_postgres.py`

**Interfaces:**
- Consumes: SQLAlchemy engine and timezone-aware `since`.
- Produces: prospective predictions, evaluations, and settled trades where the immutable prediction/order begins at or after `since`.

- [ ] Write a PostgreSQL integration test proving pre-boundary rows are excluded.
- [ ] Verify RED because the reader does not exist.
- [ ] Implement the minimal read-only reader.
- [ ] Verify full exact-head CI.

### Task 3: Read-only JSON CLI

**Files:**
- Create: `src/bp_engine/execution/evidence_cli.py`
- Create: `scripts/prospective_paper_evidence.py`
- Create: `tests/execution/test_prospective_evidence_cli.py`

**Interfaces:**
- Consumes: required `--since`, safe project settings, immutable PostgreSQL evidence.
- Produces: one JSON report containing sample counts, after-cost realized P&L and 95% bootstrap interval, accuracy/Brier/log-loss/calibration gap, and reconciliation status.

- [ ] Write CLI tests requiring timezone-aware `--since` and forbidding secret/live options.
- [ ] Verify RED.
- [ ] Implement the thin CLI and script entrypoint.
- [ ] Verify full exact-head CI.

### Task 4: Production evidence run and handoff

**Files:**
- Modify only documentation/state if the resulting evidence justifies a durable checkpoint.

- [ ] Run the exact candidate in an isolated writable runtime against `/etc/bp/bp.env` without changing production services or live settings.
- [ ] Use the Phase 14 host-acceptance timestamp as the initial prospective boundary.
- [ ] Record the actual evidence values; do not promote insufficient evidence to PASS.
- [ ] Keep Phase 15 blocked unless every Master live-gate row becomes PASS and explicit real-money authorization exists.
