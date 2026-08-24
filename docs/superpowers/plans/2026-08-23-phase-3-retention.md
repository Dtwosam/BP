# Phase 3 Retention and Aggregation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound raw recorder storage safely while preserving verified raw archives and long-lived one-second market state.

**Architecture:** Keep the Phase 2 raw writer intact, fan immutable events into a lightweight in-memory state reducer, and persist compact state separately once per second. Run archive/retention/disk maintenance outside the live capture loop, requiring a verified gzip archive and manifest before deleting any raw hour.

**Tech Stack:** Python 3.12, asyncio, Pydantic, SQLAlchemy 2, PostgreSQL 16, SQLite for focused tests, systemd.

**Spec:** `docs/superpowers/specs/2026-08-23-phase-3-retention-design.md`

## Global Constraints

- Trading remains `RESEARCH`; `LIVE_TRADING_ENABLED=false`.
- Preserve immutable `RawEvent` payloads and existing global `dedupe_key` behavior.
- Hot raw default: 24 hours; fully closed UTC-hour maintenance boundaries.
- Compressed local archive retention default: 24 hours after leaving hot storage.
- Compact one-second state retention default: 90 days.
- Warning free-space threshold: 25 GiB; critical threshold: 15 GiB.
- Never delete raw rows unless the exact interval has a verified archive + manifest.
- Do not range-partition `raw_market_events` in this increment; use BRIN + chunked deletion and measure first.

---

### Task 1: Compact-state schema and repository

**Files:**
- Modify: `src/bp_engine/storage/schema.py`
- Modify: `src/bp_engine/storage/recorder.py`
- Create: `src/bp_engine/recorder/state.py`
- Create: `migrations/0003_retention_and_state.sql`
- Create: `tests/storage/test_market_state.py`

**Interfaces:**
- Produces: `MarketStateSnapshot`, `RecorderRepository.upsert_state_snapshots(connection, snapshots)`.
- Produces table: `market_state_1s` unique on `(bucket_at, state_key)`.

- [ ] Write tests that construct state snapshots, create metadata in SQLite, upsert one bucket, then upsert the same `(bucket_at,state_key)` with a newer `last_event_at` and assert a single updated row remains.
- [ ] Run the focused test and confirm RED because the snapshot/table/repository API does not exist.
- [ ] Add the frozen `MarketStateSnapshot` model, table definition, BRIN migration SQL for PostgreSQL raw receive-time scans, and dialect-aware upsert implementation.
- [ ] Run focused storage tests and full existing storage tests; confirm GREEN.
- [ ] Commit the Task 1 production change.

### Task 2: Market-state reducer

**Files:**
- Modify: `src/bp_engine/recorder/state.py`
- Create: `tests/recorder/test_state_reducer.py`

**Interfaces:**
- Produces: `MarketStateReducer.observe(event: RawEvent) -> None`.
- Produces: `MarketStateReducer.snapshots(bucket_at: datetime) -> list[MarketStateSnapshot]`.

- [ ] Add fixture-driven tests for Polymarket `book`, `price_change`, and `last_trade_price`; Bybit snapshot/delta/trade/ticker; and Coinbase ticker/trade behavior.
- [ ] Include a Bybit test proving a zero-size delta removes a price level and changes the computed best quote.
- [ ] Include a snapshot test proving `last_event_at` is the real latest event receive time while `bucket_at` is the requested UTC second.
- [ ] Run focused tests and confirm RED because reducer behavior is absent.
- [ ] Implement only the reducer behavior required by those tests, ignoring unknown fields rather than fabricating values.
- [ ] Run focused reducer tests and all recorder tests; confirm GREEN.
- [ ] Commit Task 2.

### Task 3: One-second snapshot component and recorder fanout

**Files:**
- Modify: `src/bp_engine/recorder/state.py`
- Modify: `src/bp_engine/recorder/service.py`
- Create: `tests/recorder/test_state_snapshotter.py`
- Modify: `tests/recorder/test_service.py`

**Interfaces:**
- Produces: `MarketStateSnapshotter.run(stop: asyncio.Event)`.
- `_BufferedEventSink` receives optional reducer/incident handling and must always continue raw buffering if state reduction fails.
- `_DatabaseSink.write_state_snapshots(snapshots)` persists compact state.

- [ ] Add an async test proving the snapshotter writes one-second snapshots and performs a final safe flush on stop.
- [ ] Add a service test proving a reducer exception records a `state_reducer_error` incident while the raw event still enters the event buffer.
- [ ] Add assembly assertions that the default recorder includes the compact-state snapshot component.
- [ ] Run focused tests and confirm RED.
- [ ] Implement the snapshotter, database sink method, fanout, and default service wiring.
- [ ] Run focused tests and complete recorder suite; confirm GREEN.
- [ ] Commit Task 3.

### Task 4: Retention configuration

**Files:**
- Modify: `src/bp_engine/config.py`
- Modify: `deploy/bp.env.example`
- Modify: `tests/test_config.py`

**Interfaces:**
- Settings: `storage_hot_raw_hours=24`, `storage_archive_retention_hours=24`, `storage_state_retention_days=90`, `storage_archive_dir=/var/lib/bp/archive/raw`, `storage_warning_free_gib=25`, `storage_critical_free_gib=15`, `storage_delete_batch_size=50000`.

- [ ] Add configuration tests for exact safe defaults and environment overrides.
- [ ] Run config tests and confirm RED.
- [ ] Add Settings fields and example deployment values.
- [ ] Run config tests and full suite; confirm GREEN.
- [ ] Commit Task 4.

### Task 5: Verified raw-hour archive

**Files:**
- Create: `src/bp_engine/storage/maintenance.py`
- Create: `tests/storage/test_retention_archive.py`

**Interfaces:**
- `archive_interval(engine, archive_dir, start, end) -> ArchiveManifest`.
- `verify_archive(archive_path, manifest_path) -> ArchiveManifest`.
- Archive format: gzip JSONL in deterministic `(received_at,id)` order; manifest includes UTC interval, row count, compressed bytes, SHA-256.

- [ ] Add SQLite tests creating raw rows across multiple hours and proving only the requested interval is archived.
- [ ] Assert manifest row count and SHA match a re-read archive.
- [ ] Add corruption test that changes archive bytes and requires verification failure.
- [ ] Add idempotency test that reuses an existing verified archive rather than rebuilding from a partially deleted DB interval.
- [ ] Run focused tests and confirm RED.
- [ ] Implement canonical row serialization, temp-file + atomic rename, compressed-file SHA, gzip re-read count verification, and atomic manifest write.
- [ ] Run focused tests and full storage suite; confirm GREEN.
- [ ] Commit Task 5.

### Task 6: Fail-closed deletion and archive pruning

**Files:**
- Modify: `src/bp_engine/storage/maintenance.py`
- Modify: `src/bp_engine/storage/recorder.py`
- Modify: `tests/storage/test_retention_archive.py`

**Interfaces:**
- `delete_verified_interval(...)` verifies archive first, then deletes in bounded batches.
- `prune_expired_archives(...)` removes only verified archive/manifest pairs whose interval is older than retention and whose four required compact feeds have advanced beyond the archive end.
- `prune_expired_state(...)` removes compact state older than configured retention.

- [ ] Add test proving missing archive means zero raw deletes.
- [ ] Add test proving corrupt archive means zero raw deletes.
- [ ] Add test proving a verified archive permits deletion and rerun finishes safely after partial prior deletion.
- [ ] Add test proving archive pruning is blocked until compact state has advanced beyond the archive end for all four required feeds.
- [ ] Run focused tests and confirm RED.
- [ ] Implement generic SQLite deletion for tests and bounded PostgreSQL CTE deletion for production, plus state/archive pruning rules.
- [ ] Run focused tests and storage suite; confirm GREEN.
- [ ] Commit Task 6.

### Task 7: Disk health and storage report

**Files:**
- Modify: `src/bp_engine/storage/maintenance.py`
- Create: `scripts/storage_maintenance.py`
- Create: `tests/storage/test_storage_health.py`
- Create: `tests/storage/test_storage_report.py`

**Interfaces:**
- `disk_health(path, warning_free_gib, critical_free_gib) -> dict` with status `ok|warning|critical`.
- `build_storage_report(engine, archive_dir, settings, disk_usage_fn=...) -> dict`.
- CLI subcommands: `run`, `disk-health`, `report`.

- [ ] Add deterministic fake-disk tests for ok/warning/critical status and CLI exit codes.
- [ ] Add SQLite report tests for event/state counts and time spans; PostgreSQL-only size fields may be null under SQLite.
- [ ] Add a calculation test for projected raw bytes/day from recent event rate and average bytes/event.
- [ ] Run focused tests and confirm RED.
- [ ] Implement health/report functions and CLI JSON output.
- [ ] Ensure maintenance prunes expired verified archives before critical-space evaluation, and never bypasses archive verification to free disk.
- [ ] Run focused tests and full suite; confirm GREEN.
- [ ] Commit Task 7.

### Task 8: Systemd maintenance scheduling and runbook

**Files:**
- Create: `deploy/systemd/bp-storage-maintenance.service`
- Create: `deploy/systemd/bp-storage-maintenance.timer`
- Create: `deploy/systemd/bp-disk-health.service`
- Create: `deploy/systemd/bp-disk-health.timer`
- Modify: `scripts/deploy/bootstrap_ubuntu.sh`
- Modify: `docs/PHASE-2-DEPLOYMENT.md`
- Create: `docs/PHASE-3-OPERATIONS.md`
- Modify: `.github/workflows/ci.yml` only if current deployment validation does not cover new unit/script syntax.
- Create/modify focused deployment tests if repository convention supports them.

**Interfaces:**
- Hourly storage maintenance.
- Five-minute disk health.
- Both read `/etc/bp/bp.env`; recorder remains independent.

- [ ] Add validation coverage first for new shell/systemd assets where practical; confirm the check fails while assets are absent.
- [ ] Add services/timers and bootstrap installation/enabling steps.
- [ ] Document dry report, one-hour archive verification, timer enablement, recovery, and evidence commands.
- [ ] Run shell syntax, Compose validation, pytest, and Ruff; confirm GREEN.
- [ ] Commit Task 8.

### Task 9: CI/live verification and Phase 3 host evidence

**Files:**
- Modify only after evidence: `PROJECT_STATE.json`, `docs/DECISION-LOG.md`, `docs/CHANGELOG.md`.
- Create only after evidence: `docs/PHASE-3-CLOSEOUT.md`, sanitized machine-readable evidence under `docs/evidence/`.

- [ ] Confirm branch CI is green with Ruff and all tests.
- [ ] Confirm Live Recorder Smoke and Recorder Short Soak are green.
- [ ] Deploy exact verified Phase 3 commit to the host without deleting Phase 2 evidence.
- [ ] Verify `market_state_1s` advances for all required feeds.
- [ ] Run `storage_maintenance.py report` and save baseline evidence.
- [ ] Archive and verify at least one complete closed hour before any raw deletion.
- [ ] Run maintenance and prove that interval was deleted from hot PostgreSQL only after archive verification.
- [ ] Enable timers and observe storage growth/maintenance long enough to calculate safe steady-state retention and ensure free space stays above the critical threshold.
- [ ] Record the partitioning decision explicitly: BRIN + chunked retention accepted if stable; otherwise stop Phase 3 closeout and design the dedupe-safe partition migration.
- [ ] Update state/decision/changelog/closeout only from observed evidence.
- [ ] Commit closeout and make the Phase 3 PR ready only when acceptance passes.
