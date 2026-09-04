# Phase 14 Storage Recovery — Read-Only Production Preflight

**Phase:** 14 — recorder/storage reliability  
**Production mutation:** none  
**Recorder requirement:** stopped  
**Live trading:** disabled  
**Migration authorization:** separate explicit gate

Use this preflight before any partitioned-storage production migration. It is intentionally read-only with respect to the production VM application state: it does not fetch or check out Git commits, stop/start/enable services, run schema migration commands, run maintenance, write evidence files, or mutate PostgreSQL.

## What it verifies

The helper fails closed unless all of the following are true:

- the expected deployed SHA exactly matches `/opt/bp`;
- the exact candidate SHA is the current remote head of the selected branch;
- the deployed checkout contains no unexpected residue;
- `MODE=research`;
- `LIVE_TRADING_ENABLED=false`;
- `MAX_TRADE_SIZE_USD=0`;
- `MAX_DAILY_LOSS_USD=0`;
- `bp-recorder.service` is stopped;
- `/mnt/bp-data` is mounted;
- the canonical archive and evidence directories exist;
- protected-data free space is greater than the configured migration headroom;
- the running PostgreSQL container's `/var/lib/postgresql/data` source is on the same filesystem as `/mnt/bp-data`;
- the canonical archive directory is on that same filesystem;
- the newest `phase14-storage-recovery-24-48h-*.json` evidence contains exactly 24 contiguous one-hour intervals;
- PostgreSQL can answer read-only storage-shape queries.

The output includes current relation bytes/estimated rows, whether raw storage is already partitioned, whether legacy/ledger tables exist, timer states, data-disk/root free bytes, archive evidence identity, and `MUTATIONS_PERFORMED=false`.

## Run from Google Cloud Shell

Use a **clean local checkout at the exact candidate SHA**. The evidence runner refuses to proceed if the local `HEAD` differs from `PHASE14_PARTITIONED_STORAGE_HEAD` or if the local working tree is dirty, so the helper and verifier used for evidence are bound to the candidate being evaluated.

```bash
export PHASE14_PARTITIONED_STORAGE_FROM_HEAD='<exact currently deployed production SHA>'
export PHASE14_PARTITIONED_STORAGE_HEAD='<exact verified candidate SHA>'
export PHASE14_PARTITIONED_STORAGE_BRANCH='main'

bash scripts/deploy/phase14_storage_preflight_evidence_cloudshell.sh
```

The operator wrapper uses `umask 077`, runs the existing read-only host preflight, captures its transcript in Cloud Shell, and then runs `scripts/deploy/verify_phase14_storage_preflight.py` against that exact transcript. On success it emits:

```text
PHASE14_STORAGE_PREFLIGHT_EVIDENCE=PASS
TRANSCRIPT=<Cloud Shell transcript path>
VERIFIED=<Cloud Shell verified JSON path>
```

The transcript and verified JSON are created in Cloud Shell, not on the production VM. By default they are written under `$HOME`; custom paths may be supplied with `PHASE14_STORAGE_PREFLIGHT_TRANSCRIPT` and `PHASE14_STORAGE_PREFLIGHT_VERIFIED`. Do not substitute a shortened SHA and do not run the wrapper from a modified checkout.

The raw preflight must end with:

```text
PHASE14_PARTITIONED_STORAGE_PREFLIGHT=PASS
MUTATIONS_PERFORMED=false
```

The independent verifier then rejects conflicting duplicate fields, stale/unexpected SHAs, an active recorder, an already/partially migrated raw schema, a non-canonical recovery archive path, any mutation claim, or insufficient migration headroom. Required free space is the larger of the configured 40 GiB floor and the current raw relation size plus the unchanged 15 GiB critical reserve.

A verified JSON result has `"verdict": "PASS"`, `"mutations_performed": false`, and `"storage_shape": "legacy_unmigrated"`. It intentionally omits the PostgreSQL mount source and display-only relation-size strings.

A preflight/verifier PASS is evidence that the host satisfies the non-mutating prerequisites at that moment. It is **not** authorization to run `phase14_partitioned_storage_rollout_cloudshell.sh` and does not imply migration acceptance.

## Production migration boundary

The partitioned-storage rollout remains separately gated. Engineering verification, a read-only preflight PASS, and merge to `main` do **not** authorize the migration.

The rollout helper now machine-enforces that separate approval boundary before any production contact. It requires both `PHASE14_PARTITIONED_STORAGE_APPROVED_FROM_HEAD` and `PHASE14_PARTITIONED_STORAGE_APPROVED_HEAD` to be exact 40-character SHAs and to equal the exact deployed-from and candidate SHAs supplied to the rollout. Missing, malformed, stale, or mismatched approval values fail with `migration_approval_missing_or_invalid` before `gcloud config set project` or VM contact. These approval variables are not produced by the read-only preflight and must not be inferred from a PASS; set them only after the separate explicit production-migration approval has actually been granted for that exact SHA transition.

When migration is separately and explicitly approved, the rollout helper now requires `PHASE14_PARTITIONED_STORAGE_PREFLIGHT_VERIFIED` to point to the readable absolute path of the verified JSON produced by the preflight operator. Before it contacts the production VM, it fails closed unless that JSON reports `PASS`, matches the exact deployed-from and candidate/remote SHAs, reports `mutations_performed=false`, confirms `RECORDER_STATE=stopped`, and identifies `storage_shape=legacy_unmigrated`. The helper computes a SHA-256 of that verified evidence and records the digest in the eventual rollout evidence.

The host worker then independently rechecks the live storage boundary. It first re-queries the live schema shape and requires `raw_market_events` to remain non-partitioned while both `raw_market_events_legacy` and `raw_event_dedupe` remain absent. It also measures the current legacy `raw_market_events` total relation bytes using the configured `POSTGRES_USER` / `POSTGRES_DB`, recomputes required free space as `max(configured floor, raw relation bytes + 15 GiB critical reserve)`, and fails closed on insufficient headroom. Both the schema-shape and dynamic-headroom checks run before mutation and again after managed services are stopped, immediately before candidate checkout/migration, so stale preflight evidence cannot silently weaken the mutation boundary or admit a changed/partial schema.

After migration parity succeeds, rollout acceptance now requires a real archive-to-partition-drop cycle rather than merely a successful maintenance command. The helper records total attached `raw_market_events` child-relation bytes, runs one partitioned maintenance cycle, requires at least one retired interval with `archived_rows > 0`, requires `dedupe_rows_removed == archived_rows` for every non-empty retired interval, then measures attached partition bytes again. The rollout fails through the existing rollback path unless the post-maintenance total is strictly lower. Eventual rollout evidence records the before, after, and released byte counts plus the verified non-empty retirement flag.

Only after those checks and the separate explicit authorization may the helper perform the controlled migration with rollback armed. A successful migration must still leave:

- `RECORDER_RESTARTED=false`;
- `ROLLBACK_MATERIAL_RETAINED=true`;
- research/zero-money safety unchanged;
- Gate B unauthorized;
- selected-book freshness unchanged at exactly 10 seconds;
- Phase 15 blocked.

After host migration acceptance and a verified archive-to-partition-drop cycle demonstrates physical relation-size release, recorder restart remains a separate operational step using the already-merged recorder reliability repair and `RECORDER_WRITER_WORKERS=4`.
