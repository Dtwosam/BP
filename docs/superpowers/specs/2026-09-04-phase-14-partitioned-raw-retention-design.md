# Phase 14 Partitioned Raw Retention Reliability Design

**Date:** 4 September 2026  
**Phase:** 14 — live readiness engineering, live gate blocked  
**Branch:** `design/phase14-partitioned-raw-retention`  
**Status:** engineering implementation verified on draft PR #50; production migration not performed  
**Production:** recorder intentionally stopped during recovery; partition migration not performed; no live trading

## Engineering verification

Implementation checkpoint `44ba80fc9e6fdafb6e29c40b0d634d22c86d12e4` passed Ruff, full deployment validation, health check, dashboard tests/typecheck/build, 903 Python/PostgreSQL tests, Historical Backfill Smoke `33874446822`, Live Recorder Smoke `33874446877`, and Recorder Short Soak `33874446903`. Exact-row migration verification is streaming and bounded-memory, rollback restores the preserved legacy monolith transactionally, and the detached rollout helper leaves the recorder stopped with rollback material retained.

This verification is engineering evidence only. It does not authorize or claim a production partition migration, recorder restart, Gate B, V2 policy/model/calibration/edge selection, Phase 15, or live trading.

## Goal

Make recorder storage physically bounded under the existing retention contract.

The current Phase 3 mechanism archives closed UTC hours and then deletes matching rows from one monolithic `raw_market_events` table. That bounds logical row retention but does not guarantee that PostgreSQL returns relation files to the filesystem. The 4 September 2026 incident demonstrated this failure mode: the raw relation reached approximately 157 GB while the root filesystem approached the configured critical reserve even though the intended hot-retention window remained 24 hours.

This change replaces the monolithic raw table with hourly PostgreSQL range partitions and replaces row-deletion retirement with verified archive + partition drop. It also adds durable maintenance-health guards so a stopped maintenance timer cannot silently accumulate days of raw data.

## Immutable constraints

This change is storage/recorder reliability only.

- `MODE=research`
- `LIVE_TRADING_ENABLED=false`
- `MAX_TRADE_SIZE_USD=0`
- `MAX_DAILY_LOSS_USD=0`
- `automatic_promotion=false`
- Phase 15 remains blocked.
- Gate B remains unauthorized.
- No V2 policy/model/calibration/edge/min-edge selection.
- No V2 paper execution.
- Existing selected-book freshness semantics remain unchanged.
- Existing V1 prospective failure evidence remains separate and immutable.
- Production rollout requires separate explicit authorization after engineering verification.
- Raw event payloads, timestamps, ordering semantics, and replay-dedupe behavior remain immutable.

## Incident evidence motivating the change

The recovered production database showed:

- `raw_market_events`: approximately 157 GB total;
- raw heap: approximately 113 GB;
- raw indexes: approximately 44 GB;
- retained 24-hour raw window: 24,482,850 rows;
- rebuilt retained database: approximately 35 GB;
- maintenance timer had been inactive for roughly four days;
- deleting/rebuilding the old monolithic database reclaimed roughly 164 GB from the root filesystem.

The conclusion is architectural: chunked `DELETE` is safe for archive-before-delete semantics, but it is insufficient as the primary physical-capacity mechanism for this event rate.

## Selected architecture

### 1. Hourly range-partitioned raw event table

PostgreSQL production uses a declaratively partitioned parent named `raw_market_events`:

```text
raw_market_events
├── raw_market_events_20260904_10
├── raw_market_events_20260904_11
├── raw_market_events_20260904_12
└── ...
```

Partition key:

```sql
PARTITION BY RANGE (received_at)
```

Every child covers exactly one closed/open UTC hour:

```text
[start_of_hour, start_of_next_hour)
```

The logical columns remain the existing raw-event contract:

- `id`
- `source`
- `stream`
- `instrument`
- `event_type`
- `source_timestamp`
- `received_at`
- `sequence`
- `market_id`
- `asset_id`
- `payload`
- `dedupe_key`

Read callers continue to query `raw_market_events`; partitioning is transparent to feature, replay, soak, and execution readers.

### 2. Preserve global replay dedupe with a hash-partitioned ledger

Native range partitioning cannot enforce the current global `UNIQUE(dedupe_key)` invariant unless `received_at` participates in the unique key. Adding `received_at` would silently weaken replay dedupe because the same provider event can be replayed at a later receive time.

Instead, production adds `raw_event_dedupe`, hash-partitioned by `dedupe_key` into exactly 16 fixed partitions. Sixteen keeps each uniqueness/index structure small without adding operationally unnecessary partition count.

Logical ledger columns:

```text
dedupe_key   primary identity key
id           recorder event id allocated from one shared sequence
received_at  receive timestamp for retention cleanup
```

The ledger has a database-enforced primary key on `dedupe_key`. Fixed hash partitions keep uniqueness enforceable because the partition key is itself the unique key.

A single shared PostgreSQL sequence allocates `id` values. Raw rows use the ID returned by the dedupe claim. This preserves globally unique generated event IDs in normal recorder operation without requiring an unsupported single-column primary key on the range-partitioned raw parent. Parallel writers do not imply global commit chronology from `id`, matching the already accepted bounded-parallel writer semantics.

The raw parent MUST enforce `PRIMARY KEY (received_at, id)`, which PostgreSQL permits because the range partition key participates in the key. Ordering remains `(received_at, id)`, exactly as existing readers already use. The shared sequence remains the source of global generated-ID uniqueness; the composite primary key is the database row-identity constraint compatible with range partitioning.

### 3. Transactional recorder write path

For every writer batch, one database transaction performs:

1. insert candidate `dedupe_key`, `received_at` rows into `raw_event_dedupe`;
2. use `ON CONFLICT (dedupe_key) DO NOTHING`;
3. collect only newly claimed keys and their generated IDs;
4. insert only those raw payload rows into `raw_market_events`;
5. commit both operations together.

Required behavior:

- duplicate/replayed events return the same effective result as today: no new raw row;
- two concurrent writer workers racing on the same key produce one raw row;
- failure to route to an hourly partition rolls back the dedupe claim;
- database failure rolls back both ledger and raw inserts;
- no secondary unbounded queue or event drop is introduced;
- existing `RecorderRepository.insert_events()` return semantics remain the number of newly persisted raw events.

### 4. Partition provisioning

DDL never runs in the hot WebSocket receive/write path.

A dedicated storage-partition helper creates partitions ahead of ingestion. At minimum it maintains:

- current UTC hour;
- next two UTC hours.

It is idempotent and safe to run repeatedly.

Recorder startup fails closed if the current receive-time partition is unavailable after the provisioning step. Maintenance also provisions future partitions during every successful cycle.

No default partition is used. A default partition could hide provisioning failures and make retention ownership ambiguous.

### 5. Per-partition indexes

Every hourly raw partition receives the indexes required by current readers, including the existing effective contracts for:

- `received_at` / `id` ordering;
- source/stream time-range scans;
- market/time scans;
- Phase 12 Polymarket replay anchor/delta access paths.

Index creation is part of partition provisioning before the partition becomes writable.

The design does not rely on one monolithic BRIN relation for capacity management. Partition-local indexes are dropped with their owning hourly relation.

### 6. Archive then drop partition

The existing canonical archive format remains unchanged: deterministic gzip JSONL, manifest, compressed byte count, row count, SHA-256, and exact UTC interval.

For an hourly partition outside the 24-hour hot window:

1. identify the exact partition bounds;
2. create or verify the canonical archive for that exact hour;
3. require archive gzip readability, row-count parity, byte-count parity, and SHA-256 parity;
4. require compact-state advancement beyond the interval;
5. detach/drop the exact raw partition;
6. only after raw partition retirement, delete matching dedupe-ledger rows in bounded batches;
7. record maintenance success evidence.

If archive verification fails, the partition remains attached and no ledger row is removed.

If partition drop succeeds but ledger cleanup fails, stale dedupe keys remain. That is fail-safe: replay is conservatively suppressed longer than required. Maintenance retries ledger cleanup later.

Ledger rows are never removed before the corresponding raw partition has been retired.

### 7. Dedupe-ledger physical boundedness

`raw_event_dedupe` is narrow compared with raw payload storage, but it must also remain bounded.

The hash-partitioned ledger:

- keeps only keys associated with retained raw partitions plus temporary stale cleanup residue;
- has an index on `received_at` in each hash partition for bounded interval cleanup;
- is vacuumed by normal PostgreSQL autovacuum;
- exposes total ledger bytes and dead-row estimates in storage reporting;
- has an explicit health threshold for excessive logical retention lag.

The initial implementation does not add periodic `VACUUM FULL` or automatic `REINDEX`. Those operations need extra space/locking and are unnecessary unless later evidence shows ledger bloat fails to stabilize.

### 8. Maintenance heartbeat and retention health

Add durable maintenance-run state in PostgreSQL rather than relying only on systemd journal history.

Each maintenance cycle records:

- started_at;
- completed_at;
- status;
- archived/dropped partition count;
- oldest raw partition start/end;
- dedupe rows removed;
- final disk health;
- error summary when failed.

Storage health becomes unhealthy when any of these are true:

- no successful maintenance cycle within two hours;
- raw partition retention exceeds the allowed 24–25 hour steady-state window by more than one additional hourly cycle;
- the writable current-hour partition is missing;
- free space is at or below the existing critical threshold.

The report also exposes a projected hours-to-critical metric derived from recent partition-size growth. Projection is advisory unless later separately approved as an automatic stop threshold.

### 9. Recorder stop-before-damage guard

The five-minute storage-health service remains the fail-closed supervisor and retains the existing critical-disk stop behavior.

In addition to free-space critical state, it fails when:

- maintenance freshness exceeds two hours; or
- raw retention lag exceeds the fail-closed bound; or
- the current writable partition is missing.

A failed health service triggers the existing `bp-storage-critical-stop.service`, which stops the recorder.

Recorder startup uses the same composite storage guard. It cannot restart into a stale-maintenance or missing-partition condition.

The existing warning and critical free-space thresholds are not silently lowered or bypassed.

### 10. Dedicated production data filesystem

The recovered production host now has PostgreSQL on the dedicated `/mnt/bp-data` filesystem. Production configuration MUST make the canonical archive directory live on the same protected data filesystem or provide an explicit persistent bind mount to it.

Add `STORAGE_HEALTH_PATH` as an optional absolute-path setting. When unset, it defaults to `STORAGE_ARCHIVE_DIR` for backward compatibility. Production sets it to `/mnt/bp-data`. The composite storage guard evaluates `STORAGE_HEALTH_PATH`, so it checks the filesystem that actually contains PostgreSQL raw storage rather than an unrelated root path.

The repository remains portable: mount device names and GCP disk IDs are deployment concerns, not application constants.

## PostgreSQL schema compatibility

### Production PostgreSQL

Production uses the partitioned raw table and dedupe ledger described above.

### SQLite/unit-test compatibility

SQLite does not need to emulate PostgreSQL declarative partition DDL. Unit tests that validate generic raw-event model behavior may keep a simple SQLite representation, but PostgreSQL-specific correctness is mandatory for:

- transactional dedupe claims;
- concurrent duplicate races;
- partition routing;
- partition creation;
- partition retirement;
- sequence behavior;
- archive/drop ordering.

The implementation must not claim partition guarantees based only on SQLite tests.

## Migration of the recovered production database

Production ingestion remains stopped during migration.

The current compact monolithic retained database is the migration source.

Sequence:

1. verify research/zero-money safety environment;
2. verify PostgreSQL is on the dedicated data filesystem and has safe headroom;
3. verify the existing 24–48h archive evidence;
4. create the shared event-ID sequence at or above the current `max(id)`;
5. create hash-partitioned `raw_event_dedupe`;
6. create a temporary partitioned raw parent;
7. pre-create all hourly partitions covering retained rows plus current and two future hours;
8. copy retained raw rows preserving existing IDs and payload bytes/values;
9. populate dedupe ledger from retained rows preserving their IDs;
10. validate exact total count, min/max ID, receive-time range, per-feed counts/ranges, dedupe cardinality, and duplicate absence;
11. validate all non-raw tables remain unchanged;
12. perform controlled table-name swap while all database writers are stopped;
13. verify existing read paths against the new parent;
14. verify a synthetic duplicate is suppressed transactionally;
15. verify a new synthetic event routes to the expected current-hour partition inside an explicit transaction that is rolled back; no synthetic production evidence is committed;
16. retain rollback material until post-swap verification passes;
17. reclaim rollback storage only after the accepted rollback gate.

The production migration must be a dedicated rollout helper with automatic rollback before irreversible cleanup.

## Acceptance tests

### Recorder persistence

- one new event claims one ledger key and creates one raw row;
- replayed event creates neither a second ledger key nor second raw row;
- concurrent writers racing on one key persist exactly one raw row;
- batch containing duplicate and unique events reports only unique inserted count;
- missing current partition causes transaction failure and no orphaned dedupe claim;
- worker failure propagation/no-drop behavior from the recorder backpressure repair remains green.

### Partition lifecycle

- provisioning creates exact UTC-hour bounds;
- provisioning is idempotent;
- current + two future partitions exist;
- no default partition exists;
- events route to the correct hour;
- partition-local required indexes exist;
- archive failure prevents detach/drop;
- archive corruption prevents detach/drop;
- compact-state lag prevents detach/drop;
- successful verified archive permits drop;
- dropped partition relation bytes disappear from PostgreSQL storage accounting;
- ledger cleanup occurs only after raw partition retirement;
- failed ledger cleanup is retryable and does not recreate dropped raw data.

### Retention health

- successful maintenance heartbeat is durable;
- one delayed cycle is visible;
- more than two hours without success fails the composite guard;
- excessive raw partition age fails the guard;
- missing writable partition fails the guard;
- healthy partition/maintenance/free-space state passes;
- projected hours-to-critical is reported deterministically.

### Migration

A PostgreSQL integration test/fixture proves migration from the current monolithic schema to the partitioned schema while preserving:

- all raw logical columns;
- exact retained row count;
- IDs;
- dedupe keys;
- payloads;
- receive/source timestamps;
- per-feed ranges;
- current read-query behavior;
- sequence position.

### Regression

Require, on the exact candidate SHA:

- full Python CI;
- lint;
- deployment-asset tests;
- Historical Backfill Smoke;
- Live Recorder Smoke;
- Recorder Short Soak;
- deterministic >observed-peak recorder burst test with `RECORDER_WRITER_WORKERS=4`;
- PostgreSQL partition lifecycle integration suite.

## Production host acceptance

Production rollout is not authorized by this design approval.

When separately authorized, acceptance must include:

1. exact from/to SHA;
2. safety environment verification before and after;
3. recorder remains stopped through schema migration;
4. migration parity evidence;
5. PostgreSQL starts from partitioned schema;
6. maintenance and disk-health timers enabled;
7. one verified old partition archive + drop;
8. measured PostgreSQL relation-size decrease after partition drop;
9. current + two future writable partitions present;
10. recorder restart with bounded writer count explicitly selected;
11. all four feeds resubscribed and advancing;
12. no slow-consumer/backpressure recurrence under natural load;
13. V2 coverage report with zero future-cutoff/nonfinite violations;
14. Gate A immutable baseline preserved;
15. policy selection false and automatic promotion false;
16. rollback instructions and evidence path recorded.

Natural-load soak is required before declaring the recorder/storage repair operationally accepted.

## Rollback

Before irreversible cleanup, rollback restores:

- the prior monolithic `raw_market_events` table;
- the prior writer path;
- the prior service configuration;
- the prior exact code SHA.

Rollback never enables trading or changes any research/live gate.

After old monolithic rollback material is intentionally reclaimed, rollback becomes restore-from-verified migration artifact/snapshot rather than table rename. That transition must be explicit in rollout evidence.

## Out of scope

- V2 model or policy selection;
- calibration or edge tuning;
- paper/live execution changes;
- Phase 15;
- trading authorization;
- geography changes/bypass;
- durable event disk spool;
- changing selected-book freshness;
- changing canonical raw archive serialization;
- long-term archive retention beyond the existing policy;
- automatic dedupe-ledger `VACUUM FULL`/reindex maintenance unless later evidence requires it.

## Decision

Proceed with hourly range-partitioned `raw_market_events`, a hash-partitioned transactional dedupe ledger, archive-before-partition-drop retirement, and durable maintenance/retention guards.

The previous Phase 3 choice to defer partitioning was correct until host evidence existed. The 4 September 2026 storage incident supplies that evidence: monolithic chunked deletion is no longer sufficient for physical-capacity safety at the observed recorder volume.
