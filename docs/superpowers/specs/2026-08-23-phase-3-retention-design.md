# Phase 3 Retention and Aggregation Design

**Date:** 23 August 2026
**Phase:** 3 — retention and aggregation
**Branch:** `build/phase-3-retention`
**Live trading:** Disabled

## Goal

Prevent the recorder from filling the host disk while preserving the data needed for later research. Keep a short, exact hot raw window in PostgreSQL, keep compact one-second market state much longer, archive raw rows before deletion with checksum verification, and expose disk-growth/retention evidence.

## Constraints from the existing system

- Phase 2 recorded about 45.7 million raw events in 24 hours and previously exhausted a 40 GB disk.
- `raw_market_events` is append-only and globally deduplicated by `dedupe_key`.
- Recorder capture must remain the highest-priority path; maintenance must not make a feed outage more likely.
- PostgreSQL stays localhost-only and the recorder remains unprivileged.
- Trading remains `RESEARCH`; no model, paper-trading, or live-trading work is part of Phase 3.
- Existing raw payloads and timestamps remain immutable.

## Selected architecture

### 1. Hot raw PostgreSQL window

Keep `raw_market_events` as the exact live event store, with a default hot retention target of **24 hours**. Maintenance works only on fully closed UTC hour ranges older than the hot cutoff, so the actual steady-state hot window is approximately 24–25 hours.

Add a BRIN index on `received_at` for small, append-friendly range lookup. Do **not** range-partition the table in this increment. PostgreSQL range partitioning would require changing the existing global `UNIQUE(dedupe_key)` invariant because a partitioned-table unique constraint must include the partition key. Silent weakening of replay dedupe is not acceptable. Phase 3 will first use verified archive + chunked deletion and measure whether the table reaches a stable reusable size. Partitioning becomes a follow-up only if evidence shows chunked retention is insufficient.

### 2. One-second compact market state

Add `market_state_1s`, keyed by `(bucket_at, state_key)`. Each row contains:

- UTC second bucket;
- source / stream / instrument;
- optional Polymarket market and asset identifiers;
- timestamp of the last real event represented;
- compact JSON state.

An in-memory reducer receives the same immutable `RawEvent` objects as the raw writer and maintains current state without changing raw events. A separate snapshot component writes one row per active state key per second. The state includes only information derivable from live payloads, such as best bid/ask, last price, book depth, and available ticker/derivatives fields.

Reducer failures must not block raw capture: they are recorded as incidents and the raw event still enters the normal buffer. Snapshot persistence is a separate recorder component.

Default compact-state retention is **90 days**. This is configurable.

### 3. Verified raw archive before deletion

A separate maintenance command runs outside the recorder's capture loop. For each closed UTC hour that has fallen outside hot retention:

1. if no verified archive exists, stream the complete interval from PostgreSQL in deterministic `(received_at, id)` order;
2. write a temporary gzip JSONL file containing all raw columns in canonical JSON form;
3. atomically rename the completed archive;
4. compute SHA-256 of the compressed archive;
5. re-open the gzip file, count records, and verify it is readable;
6. write an atomic manifest containing interval, row count, byte size, and SHA-256;
7. only after archive + manifest verification, delete matching PostgreSQL rows in bounded batches.

If archive creation or verification fails, **no raw rows for that interval are deleted**.

If maintenance crashes after archive verification but during deletion, rerunning first verifies the existing archive and then safely continues deletion. This makes the process idempotent.

Archives live under `/var/lib/bp/archive/raw` by default. Local compressed-archive retention defaults to **24 hours after leaving the hot store**, giving roughly 48 hours of full raw recoverability while keeping enough free space on the current ~100 GB host. Archive retention is configurable.

Expired archive/manifest pairs are removed only after they verify successfully and compact state has advanced beyond their interval.

### 4. Storage maintenance and disk guard

Add one maintenance CLI with three operator-facing actions:

- `run`: prune already-expired verified archives, archive/delete eligible raw hours, and prune expired compact state;
- `disk-health`: emit JSON free-space status and return non-zero at the critical threshold;
- `report`: emit JSON storage measurements suitable for Phase 3 acceptance evidence.

Default free-space thresholds:

- warning: **25 GiB** free;
- critical: **15 GiB** free.

Before creating a new archive, maintenance prunes already-expired verified archives and checks free space. It never deletes unarchived raw data merely to recover disk space.

Systemd timers run maintenance hourly and disk-health checks every five minutes. Their output goes to the journal; the recorder itself is not placed in a disk-triggered restart loop.

### 5. Storage report

The report includes, at minimum:

- raw event count and receive-time span;
- raw table total bytes on PostgreSQL when available;
- compact-state count and time span;
- archive count and bytes;
- free filesystem bytes / percent;
- configured retention and thresholds;
- recent 24-hour event count;
- estimated raw bytes per event and projected raw bytes/day when PostgreSQL size data is available.

This report is the evidence source for the Phase 3 acceptance requirement to estimate safe retention on the host.

## Data model

`market_state_1s`:

- `bucket_at` — timezone-aware UTC timestamp, second precision;
- `state_key` — deterministic state identity;
- `source`;
- `stream`;
- `instrument`;
- `market_id` nullable;
- `asset_id` nullable;
- `last_event_at` — receive timestamp of the latest real event represented;
- `state` — JSON compact state;
- unique `(bucket_at, state_key)`.

The table is independently rebuildable from retained raw/archive data for intervals where full raw data still exists.

## State reduction rules

- **Polymarket `book`:** compute best bid/ask and aggregate displayed bid/ask depth from the snapshot payload.
- **Polymarket `price_change`:** update each listed asset independently using `best_bid`, `best_ask`, price, size, and side.
- **Polymarket `last_trade_price`:** update last trade for its asset.
- **Bybit orderbook snapshot/delta:** maintain the 50-level book in memory, applying zero-size deletes; expose best bid/ask and summed displayed depth.
- **Bybit public trades:** update last trade and latest trade side/size.
- **Bybit linear ticker:** preserve available mark/index/funding/open-interest style fields in compact state.
- **Coinbase ticker:** preserve available best bid/ask and price fields.
- **Coinbase market trades:** update last trade fields.

Unknown or incomplete fields are ignored rather than fabricated. `last_event_at` always lets later feature code distinguish carried-forward state from a fresh event.

## Failure behavior

- Raw capture continues if compact-state reduction fails.
- Raw deletion is forbidden without a verified archive + manifest.
- Missing or corrupt existing archive files stop deletion for their interval.
- Disk critical status returns a failing maintenance/health status but does not cause the recorder to restart-loop.
- Maintenance is safe to rerun after partial work.
- No maintenance path enables trading or changes trade limits.

## Testing strategy

Use TDD for every production behavior.

Focused tests cover:

- state reduction for authentic Polymarket, Bybit, and Coinbase fixtures;
- one-second snapshot identity and upsert behavior;
- archive canonicalization, SHA verification, row-count verification, and atomic manifest behavior;
- archive corruption preventing deletion;
- missing archive preventing deletion;
- safe resume when a verified archive exists but rows remain;
- bounded interval deletion;
- archive retention requiring compact state to be newer than the archive interval;
- disk warning/critical threshold behavior;
- storage-report calculations;
- recorder wiring keeps raw capture operational when state reduction fails.

Full CI, live recorder smoke, and short PostgreSQL soak remain required before Phase 3 can close.

## Deployment sequence

1. Merge the completed Phase 2 PR.
2. Develop Phase 3 on `build/phase-3-retention`.
3. Deploy schema/code without deleting existing Phase 2 evidence.
4. Start the recorder with compact snapshots enabled.
5. Run maintenance manually in report/dry observation first.
6. Verify archive output and checksums on one closed hour.
7. Enable hourly maintenance and five-minute disk-health timers.
8. Collect storage-growth evidence long enough to estimate steady-state retention and verify the host remains comfortably above the critical threshold.
9. Record Phase 3 closeout only after the acceptance evidence passes.

## Acceptance

Phase 3 passes only when:

- hot raw retention is bounded and archive-before-delete is verified on the host;
- compact one-second market state persists correctly;
- no unarchived raw interval is deleted;
- disk warning/critical checks operate on the host;
- storage growth/day and safe retention are measured and documented;
- database performance remains adequate during recorder + maintenance operation;
- CI, live smoke, and short soak are green;
- `PROJECT_STATE.json`, decision log, changelog, and closeout evidence are updated.
