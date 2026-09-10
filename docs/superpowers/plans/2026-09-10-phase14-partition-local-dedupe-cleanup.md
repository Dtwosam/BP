# Phase 14 Partition-Local Dedupe Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent partitioned storage maintenance from timing out by deleting expired dedupe rows through partition-local indexed batches instead of a global 24M-row parent scan.

**Architecture:** Preserve the existing archive verification, compact-state, raw-partition drop, replay-dedupe, and bounded batch contracts. After a verified raw partition is dropped, iterate the fixed 16 hash child tables and delete only rows in the retired time interval using child-local `ctid` batches so PostgreSQL can use each child `received_at` index without hashing/scanning the entire parent ledger.

**Tech Stack:** Python 3.12, SQLAlchemy, PostgreSQL 16, pytest.

**Spec:** `docs/superpowers/specs/2026-09-04-phase-14-partitioned-raw-retention-design.md`

## Global Constraints

- Never remove dedupe rows before the corresponding raw partition has been retired.
- Preserve global replay dedupe semantics; stale dedupe keys are fail-safe.
- Keep cleanup bounded by `batch_size`.
- Do not alter retention windows, storage thresholds, trading mode, or Gate B behavior.

---

### Task 1: Partition-local dedupe cleanup

**Files:**
- Modify: `src/bp_engine/storage/maintenance.py`
- Test: `tests/storage/test_partitioned_raw_postgres.py`

**Interfaces:**
- Consumes: existing `retire_verified_partition(..., batch_size=...) -> PartitionRetirementResult`.
- Produces: unchanged public interface and exact dedupe deletion count.

- [x] **Step 1: Write the failing regression test** asserting retirement emits child-table `ctid` deletes rather than parent-table deletes.
- [ ] **Step 2: Run the focused PostgreSQL test and verify RED** because current implementation deletes from `raw_event_dedupe AS target`.
- [ ] **Step 3: Implement minimal partition-local child cleanup** with identifier-safe fixed child names and bounded `ctid` batches.
- [ ] **Step 4: Run focused PostgreSQL retirement tests and verify GREEN**.
- [ ] **Step 5: Run Ruff and the complete test suite**.
- [ ] **Step 6: Commit, push, open PR, verify CI, and merge**.
