# Phase 14 Partitioned Raw Retention Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace monolithic PostgreSQL raw-event retirement with hourly archive-then-drop partitions while preserving global replay dedupe, recorder concurrency semantics, and all Phase 14 safety gates.

**Architecture:** Keep the existing SQLAlchemy `raw_market_events` logical mapping for reader and SQLite compatibility. Add PostgreSQL-only partition-management primitives that explicitly create/migrate the physical raw parent and a 16-way hash-partitioned dedupe ledger. Recorder writes detect partitioned mode and claim dedupe keys transactionally before payload insert; maintenance archives exact UTC-hour partitions, drops them only after verification, then removes narrow ledger keys. Composite storage health adds partition availability, retention lag, and maintenance heartbeat to the existing disk gate.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x, psycopg/PostgreSQL 16, pytest, Ruff, systemd, Bash, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-04-phase-14-partitioned-raw-retention-design.md`

**Implementation status:** Tasks 1–6 engineering-verified on `44ba80fc9e6fdafb6e29c40b0d634d22c86d12e4`. Task 7 documentation/source-of-truth reconciliation and docs-complete verification passed on `3c8bb5cb9f80e077e506dc80d329053a9350d568`: 903 tests, Ruff, deployment/health/dashboard validation, Historical Backfill Smoke, Live Recorder Smoke, and Recorder Short Soak all green. The final evidence-recording head still requires its exact-head recheck before PR #50 leaves draft. Production migration remains unperformed and separately gated.

## Global Constraints

- `MODE=research`
- `LIVE_TRADING_ENABLED=false`
- `MAX_TRADE_SIZE_USD=0`
- `MAX_DAILY_LOSS_USD=0`
- `automatic_promotion=false`
- Phase 15 remains blocked.
- Gate B remains unauthorized.
- No V2 policy/model/calibration/edge/min-edge selection.
- No V2 paper execution.
- Existing selected-book freshness remains exactly 10 seconds.
- Existing V1 prospective evidence remains immutable and separate.
- Raw archive serialization remains unchanged.
- Production rollout remains a separate operational gate; engineering/merge does not restart production ingestion.

---

### Task 1: PostgreSQL partition primitives

**Files:**
- Create: `src/bp_engine/storage/partitioned_raw.py`
- Create: `tests/storage/test_partitioned_raw_postgres.py`

**Interfaces:**
- Produces: `RawStorageMode`, `raw_storage_mode(connection)`, `ensure_partitioned_raw_storage(engine, *, now, migrate_existing=False)`, `ensure_hour_partitions(connection, *, start_at, hours_ahead)`, `list_raw_partitions(connection)`, `drop_raw_partition(connection, *, start_at, end_at)`.
- Physical PostgreSQL objects: `raw_market_events` range parent, `raw_event_dedupe` 16-way hash parent, `raw_market_events_id_seq_v2`.

- [ ] **Step 1: Write failing PostgreSQL tests for fresh initialization**
  - Drop only the test-owned raw tables/sequence.
  - Create the legacy SQLAlchemy schema.
  - Call `ensure_partitioned_raw_storage(..., migrate_existing=False)`.
  - Assert the empty legacy raw table becomes a `RANGE(received_at)` parent.
  - Assert exactly 16 `raw_event_dedupe_h00..h15` hash children.
  - Assert current + two future UTC-hour raw partitions exist.
  - Assert there is no default raw partition.

- [ ] **Step 2: Run the focused test and confirm RED**
  - Run: `pytest tests/storage/test_partitioned_raw_postgres.py -vv`
  - Expected: import/function missing.

- [ ] **Step 3: Implement minimal PostgreSQL DDL helpers**
  - Quote identifiers through fixed internally generated names only.
  - Parent raw columns exactly match the existing physical contract.
  - Raw parent uses `PRIMARY KEY (received_at, id)`.
  - Dedupe parent uses `PRIMARY KEY (dedupe_key)` and `PARTITION BY HASH (dedupe_key)`.
  - Shared sequence starts above existing `max(id)`.
  - Hour partitions use half-open UTC bounds and required local indexes.

- [ ] **Step 4: Run focused test GREEN**
  - Run: `pytest tests/storage/test_partitioned_raw_postgres.py -vv`

- [ ] **Step 5: Add migration parity test**
  - Seed legacy rows spanning at least three UTC hours.
  - Run `migrate_existing=True`.
  - Assert count/min/max ID/min/max received_at/per-feed counts and every dedupe key match.
  - Assert the old table is retained under a rollback name until explicit cleanup.

- [ ] **Step 6: Run focused PostgreSQL suite GREEN and commit**
  - Commit: `feat: add partitioned raw storage primitives`

### Task 2: Transactional dedupe writer

**Files:**
- Modify: `src/bp_engine/storage/recorder.py`
- Extend: `tests/storage/test_partitioned_raw_postgres.py`
- Extend: `tests/storage/test_recorder.py`

**Interfaces:**
- Existing public interface remains `RecorderRepository.insert_events(connection, events) -> int`.
- Partitioned PostgreSQL path returns newly persisted raw row count.
- Legacy PostgreSQL and SQLite paths remain backward compatible.

- [ ] **Step 1: Add RED test for duplicate replay in partitioned mode**
  - Insert the same `RawEvent` twice.
  - Assert first call returns 1, second returns 0.
  - Assert one ledger key and one raw row.

- [ ] **Step 2: Add RED concurrent duplicate-race test**
  - Two independent PostgreSQL transactions race the same dedupe key.
  - Assert exactly one committed raw row and one ledger row.

- [ ] **Step 3: Add RED missing-partition rollback test**
  - Remove the target hour partition.
  - Insert an event in that hour.
  - Assert insert fails and no dedupe ledger claim remains.

- [ ] **Step 4: Implement partitioned PostgreSQL writer**
  - Detect partitioned mode once per connection operation.
  - Claim keys using `INSERT ... ON CONFLICT (dedupe_key) DO NOTHING RETURNING dedupe_key,id`.
  - Insert only claimed payload rows into `raw_market_events`.
  - Use the surrounding SQLAlchemy transaction; never commit internally.
  - Preserve legacy `ON CONFLICT` code for monolithic PostgreSQL.

- [ ] **Step 5: Run recorder + partition tests GREEN**
  - Run: `pytest tests/storage/test_recorder.py tests/storage/test_partitioned_raw_postgres.py -vv`
  - Commit: `feat: preserve dedupe across raw partitions`

### Task 3: Partition archive retirement

**Files:**
- Modify: `src/bp_engine/storage/maintenance.py`
- Extend: `tests/storage/test_partitioned_raw_postgres.py`
- Preserve: `tests/storage/test_retention_archive.py`

**Interfaces:**
- New: `retire_verified_partition(engine, archive_path, manifest_path) -> PartitionRetirementResult`.
- Existing `delete_verified_interval` remains for SQLite/legacy compatibility.

- [ ] **Step 1: Add RED archive-failure and corruption tests**
  - Partition remains attached when archive/manifest missing or corrupt.
  - Ledger rows remain.

- [ ] **Step 2: Add RED compact-state gate test**
  - Verified archive alone cannot retire a partition if required compact feeds have not advanced beyond the interval.

- [ ] **Step 3: Add RED successful-retirement test**
  - Archive exact hour, verify manifest, advance all four compact feeds.
  - Capture relation bytes for that child.
  - Retire partition.
  - Assert child relation disappears, raw rows disappear, and dedupe rows are removed only afterward.

- [ ] **Step 4: Implement retirement path**
  - Verify archive first.
  - Reuse existing compact-state continuity helper.
  - Drop exact child partition.
  - Delete matching dedupe ledger rows in bounded batches after drop.
  - Return dropped partition name and ledger rows removed.

- [ ] **Step 5: Run legacy archive tests plus partition suite GREEN**
  - Run: `pytest tests/storage/test_retention_archive.py tests/storage/test_partitioned_raw_postgres.py -vv`
  - Commit: `feat: retire verified raw partitions`

### Task 4: Durable maintenance heartbeat and composite health

**Files:**
- Modify: `src/bp_engine/storage/schema.py`
- Modify: `src/bp_engine/storage/maintenance.py`
- Modify: `scripts/storage_maintenance.py`
- Modify: `src/bp_engine/config.py`
- Modify: `tests/storage/test_storage_health.py`
- Modify: `tests/storage/test_storage_report.py`
- Extend: `tests/storage/test_partitioned_raw_postgres.py`

**Interfaces:**
- New SQLAlchemy table: `storage_maintenance_runs`.
- New setting: `storage_health_path: str | None = None`.
- New: `build_composite_storage_health(engine, path, settings, *, now)`.
- New report fields: storage mode, maintenance age, oldest partition age, partition count/bytes, dedupe bytes, projected hours-to-critical.

- [ ] **Step 1: RED tests for maintenance freshness**
  - Healthy completed run under two hours passes.
  - No successful run older than two hours fails closed in partitioned mode.
  - Legacy mode remains governed by disk status only during migration compatibility.

- [ ] **Step 2: RED tests for retention lag and writable partition**
  - Missing current partition fails.
  - Oldest partition beyond allowed bound fails.
  - Current + future partitions and normal retention pass.

- [ ] **Step 3: RED tests for health filesystem selection**
  - `storage_health_path` overrides archive path.
  - When unset, archive path remains fallback.

- [ ] **Step 4: Implement durable run recording**
  - Record start, success/failure, completed time, partitions retired, dedupe rows removed, final disk status, and error text.
  - Do not hide maintenance exceptions; record then re-raise/fail.

- [ ] **Step 5: Update CLI**
  - `run` provisions current+two future partitions first.
  - Partitioned mode iterates eligible child partitions rather than scanning earliest row.
  - `disk-health` invokes composite guard when PostgreSQL is reachable and partitioned.
  - `report` exposes new partition/heartbeat metrics.

- [ ] **Step 6: Run storage suites GREEN**
  - Run: `pytest tests/storage -vv`
  - Commit: `feat: fail closed on stale raw retention`

### Task 5: Recorder startup and deployment wiring

**Files:**
- Modify: `src/bp_engine/recorder/service.py`
- Modify: `scripts/deploy/ensure_storage_indexes.py`
- Modify: `scripts/deploy/bootstrap_ubuntu.sh`
- Modify: `deploy/bp.env.example`
- Modify: `deploy/systemd/bp-recorder.service`
- Modify: `tests/deploy/test_phase3_storage_deployment.py`
- Create/modify deployment tests for Phase 14 storage assets.

**Interfaces:**
- Recorder startup provisions/validates the current + two future partitions only when partitioned mode is active.
- Production `STORAGE_HEALTH_PATH=/mnt/bp-data`; portable default remains unset/fallback.

- [ ] **Step 1: RED deployment-asset tests**
  - Ensure bootstrap adds `STORAGE_HEALTH_PATH` default without changing trading gates.
  - Ensure recorder still executes storage health condition before service start.
  - Ensure index installer safely initializes only an empty legacy raw table; non-empty migration requires explicit rollout script.

- [ ] **Step 2: Implement startup provisioning**
  - Before collectors start, partitioned PostgreSQL ensures current + two future hours.
  - Missing/provisioning failure aborts recorder startup.

- [ ] **Step 3: Update index installer**
  - Empty fresh PostgreSQL install becomes partitioned.
  - Existing non-empty legacy installation is reported as legacy and left untouched unless explicit migration helper is invoked.

- [ ] **Step 4: Run deploy tests and shell/Python validation**
  - Run relevant pytest deployment tests.
  - `bash -n scripts/deploy/bootstrap_ubuntu.sh`
  - `python -m py_compile scripts/deploy/ensure_storage_indexes.py`
  - Commit: `feat: wire partition storage guards into recorder`

### Task 6: Controlled production migration/rollback helper

**Files:**
- Create: `scripts/deploy/phase14_partitioned_storage_rollout_cloudshell.sh`
- Create: `tests/deploy/test_phase14_partitioned_storage_rollout.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Script accepts exact candidate SHA and expected deployed-from SHA.
- Script never enables live trading and never starts recorder unless an explicit separate restart flag/step is introduced later; default rollout ends with recorder stopped.

- [ ] **Step 1: RED static rollout safety tests**
  - Require research/zero-money gates.
  - Require recorder stopped before migration.
  - Require dedicated data mount and free-space threshold.
  - Require exact before/after SHA evidence.
  - Require rollback arm before table swap.
  - Require post-migration parity checks.
  - Require `RECORDER_RESTARTED=false`.
  - Forbid live-trading enablement/geography bypass/Phase 15.

- [ ] **Step 2: Implement detached/idempotent migration helper**
  - Use `systemd-run` for long host work.
  - Run migration using repository Python helper.
  - Validate raw parity, dedupe parity, partitions, indexes, sequence.
  - Preserve rollback table until explicit cleanup gate.
  - Emit sanitized evidence under `/mnt/bp-data/evidence`.
  - Leave recorder stopped.

- [ ] **Step 3: Add CI syntax validation**
  - `bash -n scripts/deploy/phase14_partitioned_storage_rollout_cloudshell.sh`.

- [ ] **Step 4: Run rollout static tests GREEN and commit**
  - Commit: `feat: add controlled partitioned storage rollout`

### Task 7: Documentation, state, and regression verification

**Files:**
- Modify: `docs/PHASE-3-DEPLOYMENT.md`
- Modify: `docs/DECISION-LOG.md`
- Modify: `docs/CHANGELOG.md`
- Modify: `PROJECT_STATE.json`
- Modify: design spec status after implementation evidence.

**Interfaces:**
- Project status remains `PHASE_14_ENGINEERING_COMPLETE_LIVE_GATE_BLOCKED`.
- Add a Phase 14 storage-recovery/reliability checkpoint; do not rewrite older Phase 3 acceptance evidence.

- [x] **Step 1: Update docs with measured incident and new physical-retention contract**
  - Record why Phase 3 chunked deletion was superseded by partition drop.
  - Keep original Phase 3 evidence immutable.

- [x] **Step 2: Update PROJECT_STATE truthfully**
  - Record engineering candidate, test evidence, production recorder-stopped recovery state.
  - Do not claim production partition rollout until host acceptance occurs.

- [x] **Step 3: Run exact-head full CI via GitHub Actions**
  - Required: Ruff, complete pytest suite, deployment validation, dashboard tests/typecheck/build.

- [x] **Step 4: Trigger/verify PR smoke gates**
  - Historical Backfill Smoke.
  - Live Recorder Smoke.
  - Recorder Short Soak.
  - Treat smoke tests as engineering verification only; no production rollout.

- [x] **Step 5: Diff review against `main`**
  - Verify no model/policy/calibration/edge/live-order/geoblock/Phase 15 changes.
  - Verify selected-book 10-second freshness untouched.

- [ ] **Step 6: Keep PR draft until all exact-head gates are green**
  - Production remains stopped until separately authorized rollout.
