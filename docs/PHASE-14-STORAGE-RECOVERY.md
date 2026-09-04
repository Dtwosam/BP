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

The partitioned-storage rollout remains separately gated. When explicitly authorized, the rollout helper rechecks the safety boundary and then performs the controlled migration with rollback armed. A successful migration must still leave:

- `RECORDER_RESTARTED=false`;
- `ROLLBACK_MATERIAL_RETAINED=true`;
- research/zero-money safety unchanged;
- Gate B unauthorized;
- selected-book freshness unchanged at exactly 10 seconds;
- Phase 15 blocked.

After host migration acceptance and a verified archive-to-partition-drop cycle demonstrates physical relation-size release, recorder restart remains a separate operational step using the already-merged recorder reliability repair and `RECORDER_WRITER_WORKERS=4`.
