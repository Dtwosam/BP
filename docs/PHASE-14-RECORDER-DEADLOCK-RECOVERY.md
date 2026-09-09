# Phase 14 recorder deadlock recovery

**Status:** engineering-only recovery package  
**Production execution:** not authorized  
**Trading boundary:** RESEARCH only; live trading disabled; trade-size and daily-loss limits remain zero  
**Gate B:** unauthorized; final holdout must remain untouched

## Purpose

Recover the accepted four-writer recorder runtime after the 9 September 2026 fail-closed storage stop without deploying the 153 commits that accumulated between the accepted production checkout and current main.

Production diagnostics established the incident sequence:

1. the recorder was active with `RECORDER_WRITER_WORKERS=4`;
2. the 15:00 and 16:00 UTC storage-maintenance cycles deadlocked while re-running dedupe-parent index DDL;
3. the missing successful maintenance heartbeat made `maintenance_fresh=false`;
4. the existing disk-health fail-closed chain correctly stopped only `bp-recorder.service`;
5. with the recorder stopped, the 17:00 maintenance cycle succeeded and storage returned to `status=ok`.

PR #160 fixed the already-partitioned runtime path on main by replacing repeated parent/sequence/index bootstrap DDL with read-only schema validation plus current + two future raw-hour provisioning.

## Exact production patch candidate

Do **not** move production directly from the accepted head to current main for this recovery.

The recovery candidate is a minimal descendant of the accepted production checkout:

- deployed-from head: `895c6bd2f9409f16bf5d544b26b30e20ecbfe43a`
- candidate branch: `ops/phase14-storage-deadlock-recovery-candidate`
- candidate head: `e9c7afc1536880e4612cb6e3d1a7282fa37c69f5`
- accepted storage evidence: `/mnt/bp-data/evidence/phase14-partitioned-storage-rollout-20260909T070219Z.json`
- accepted storage evidence SHA-256: `f33a28f5306e46c509b0000a176d226c079aa2d160d595095ca228118542ce19`

The exact deployed-from → candidate diff is restricted to:

- `src/bp_engine/storage/partitioned_raw.py`
- `tests/storage/test_partitioned_raw_postgres.py`

The runtime file and PostgreSQL regression-test file are byte-identical to the corresponding files on the exact main helper head. The helper verifies both Git blob identities before cloud contact and repeats the candidate scope/blob checks on the production VM.

Verification-only PR #161 uses a frozen base branch at the accepted production head so this production-shaped backport is tested independently from the newer main tree. Its full PostgreSQL-backed CI passed 998 tests, including both active-writer deadlock regressions.

## Recovery helper

The exact-main helper is:

`scripts/deploy/phase14_recorder_deadlock_recovery_gate_cloudshell.sh`

It is deliberately different from the earlier `phase14_recorder_restart_gate_cloudshell.sh`. The old restart gate was a one-time 1→4 worker-setting transition and fails closed when the environment already contains `RECORDER_WRITER_WORKERS=4`. The deadlock-recovery gate requires the accepted four-worker configuration to already exist and never edits the production environment file.

Before mutation the helper requires:

- clean exact-main local helper checkout and unchanged remote main;
- exact immutable recovery-candidate branch/head and exact two-file candidate scope;
- exact accepted production deployed head;
- exact accepted partitioned-storage evidence digest;
- recorder state `inactive`;
- `RECORDER_WRITER_WORKERS=4` exactly once in `/etc/bp/bp.env` and parsed as 4 by application settings;
- PostgreSQL, dashboard API/web, paper execution, live predictor, and prospective outcomes active;
- storage-maintenance, disk-health, and V2 forward-coverage timers active+enabled;
- their oneshot services idle with last result `success`;
- installed recorder/storage/V2 unit fragments exactly matching the deployed repository, with no drop-ins;
- `MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0` in both runtime and prospective safety files;
- every `automatic_promotion` field in deployed `PROJECT_STATE.json` equal to false;
- current composite storage health `ok`, storage mode `partitioned`, and all three guards true;
- a fingerprint of all existing Gate B plan/selection/holdout/summary artifacts before mutation.

## Authorized mutation sequence

This section documents what the helper will do **only after separate explicit production authorization**.

1. Stop only the storage-maintenance, disk-health, and V2 forward-coverage timers. Core research services remain running and the recorder is already inactive.
2. Fetch and re-verify the exact minimal candidate.
3. Switch `/opt/bp` detached from the accepted head to the exact minimal candidate without `--force`, preserving the established tolerated dashboard build residue.
4. Re-validate checkout scope, unit contracts, four-worker settings, research/zero-money safety, and automatic-promotion=false.
5. Run one storage-maintenance cycle while the recorder is still stopped. It must return systemd `Result=success` and `ExecMainStatus=0`.
6. Re-run composite storage health; it must remain `ok` with every guard true.
7. Restore the disk-health timer before recorder start.
8. Start—not restart—`bp-recorder.service`. Its existing `ExecCondition` runs storage disk-health first.
9. Require a stable MainPID and wait 45 seconds.
10. While the recorder is actively writing, force one real `bp-storage-maintenance.service` cycle with the maintenance timer still stopped. This is the direct production acceptance for the incident class: the cycle must succeed, the recorder MainPID must remain unchanged, and `NRestarts` must not increase.
11. Run the established natural-load soak. All four required feeds must emit events and record zero backpressure incidents.
12. Verify dashboard safety remains RESEARCH/live-disabled/real-execution-unavailable.
13. Re-run composite storage health and require `ok` plus all three guards.
14. Restore the storage-maintenance and V2 forward-coverage timers and require all three timers active+enabled.
15. Recompute the Gate B artifact fingerprint and require it to be byte-for-byte unchanged.
16. Write a host acceptance artifact under `/var/lib/bp/evidence/phase14-recorder-deadlock-recovery-<timestamp>.json`.

The helper performs no environment-file edit, no schema migration, no policy/model/calibration/edge selection, no Gate B command, no holdout evaluation, no automatic promotion, and no live/money change.

## Rollback

Any failure after mutation is armed causes the helper to:

1. stop `bp-recorder.service`;
2. stop the three controlled timers/oneshots;
3. return `/opt/bp` detached to `895c6bd2f9409f16bf5d544b26b30e20ecbfe43a`;
4. restart the disk-health, storage-maintenance, and V2 forward-coverage timers;
5. leave the recorder stopped;
6. emit `PHASE14_RECORDER_DEADLOCK_RECOVERY_ROLLBACK=COMPLETE`.

The helper does not attempt to restore or rewrite database rows because its only checkout change is the already-tested steady-state runtime deadlock fix. Any maintenance retirement performed before a later acceptance failure remains governed by the existing verified partition-retirement contract.

## Operator command — do not run without authorization

From a clean Cloud Shell checkout at the exact current main head:

```bash
git fetch origin
git checkout main
git pull --ff-only
git status --short

export PHASE14_RECORDER_DEADLOCK_RECOVERY_HELPER_HEAD="$(git rev-parse HEAD)"

bash scripts/deploy/phase14_recorder_deadlock_recovery_gate_cloudshell.sh
```

The helper refuses to run if local `HEAD` is not the supplied helper head, if remote main changed, if the candidate branch moved, if the candidate is no longer exactly the two-file backport, or if either candidate blob differs from exact main.

Success ends with:

```text
PHASE14_RECORDER_DEADLOCK_RECOVERY_GATE=PASS
EVIDENCE_FILE=/var/lib/bp/evidence/phase14-recorder-deadlock-recovery-<timestamp>.json
```

## After a production PASS

A production PASS would authorize only recording the recovered research runtime state. It still would not authorize Gate B.

After the PASS:

1. record the exact deployed candidate head and host evidence SHA/path in the source-of-truth files;
2. verify the recorder remains four-worker, all four feeds remain fresh, all three timers remain active, and storage remains healthy;
3. only then resume the repeatable **feature-only** Gate B readiness helper;
4. `READY=false` means continue collecting evidence;
5. `READY=true` remains a prerequisite only and is not Gate B authorization;
6. the final holdout remains untouched until a separately authorized Gate B attempt.
