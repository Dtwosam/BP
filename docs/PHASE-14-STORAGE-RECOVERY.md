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

From a checkout containing the verified helper:

```bash
export PHASE14_PARTITIONED_STORAGE_FROM_HEAD='<exact currently deployed production SHA>'
export PHASE14_PARTITIONED_STORAGE_HEAD='<exact verified candidate SHA>'
export PHASE14_PARTITIONED_STORAGE_BRANCH='main'

bash scripts/deploy/phase14_partitioned_storage_preflight_cloudshell.sh
```

Do not substitute a shortened SHA.

A successful result ends with:

```text
PHASE14_PARTITIONED_STORAGE_PREFLIGHT=PASS
MUTATIONS_PERFORMED=false
```

A preflight PASS is evidence that the host satisfies the non-mutating prerequisites at that moment. It is **not** authorization to run `phase14_partitioned_storage_rollout_cloudshell.sh` and does not imply migration acceptance.

## Production migration boundary

The partitioned-storage rollout remains separately gated. When explicitly authorized, the rollout helper rechecks the safety boundary and then performs the controlled migration with rollback armed. A successful migration must still leave:

- `RECORDER_RESTARTED=false`;
- `ROLLBACK_MATERIAL_RETAINED=true`;
- research/zero-money safety unchanged;
- Gate B unauthorized;
- selected-book freshness unchanged at exactly 10 seconds;
- Phase 15 blocked.

After host migration acceptance and a verified archive-to-partition-drop cycle demonstrates physical relation-size release, recorder restart remains a separate operational step using the already-merged recorder reliability repair and `RECORDER_WRITER_WORKERS=4`.
