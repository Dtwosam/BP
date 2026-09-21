# Phase 14 concurrent partition retirement rollout

**Status:** engineering rollout gate under validation; production execution not authorized  
**Production checkout:** `7c3af78da1922a0e5187c24b799951130cc98887`  
**Production candidate:** `ed7d930c69e417dda388b0cb62b3a543a4b8134f`  
**Trading boundary:** RESEARCH only; live trading disabled; real-money limits remain zero

## Purpose

Deploy the Phase 14 steady-state raw-partition retirement contention fix without advancing production across unrelated main history.

The 21 September 2026 incident showed that an hourly archive could finish successfully while direct physical retirement of the attached raw child remained blocked behind active recorder transactions until the 55-minute systemd timeout. D-055 replaces steady-state direct attached-child drop with verified concurrent detach, detached-table parity recheck, physical drop, then existing dedupe cleanup.

This rollout changes storage-maintenance runtime only. It does not change V3/V4 models, paper sizing, trading policy, Gate B state, automatic promotion, live execution, or money limits.

## Immutable production-shaped candidate

Verification-only PR #214 uses a frozen base at the currently deployed production checkout.

- from head: `7c3af78da1922a0e5187c24b799951130cc98887`
- candidate branch: `ops/phase14-concurrent-partition-retirement-candidate`
- candidate head: `ed7d930c69e417dda388b0cb62b3a543a4b8134f`
- verification PR: #214, closed without merge after CI success
- accepted storage evidence: `/mnt/bp-data/evidence/phase14-partitioned-storage-rollout-20260909T070219Z.json`
- accepted storage evidence SHA-256: `f33a28f5306e46c509b0000a176d226c079aa2d160d595095ca228118542ce19`

The candidate diff is exactly:

- `scripts/storage_maintenance.py`
- `src/bp_engine/storage/maintenance.py`
- `src/bp_engine/storage/partitioned_raw.py`
- `tests/storage/test_partitioned_raw_postgres.py`

Each candidate blob must be byte-identical to the corresponding exact-main helper-head blob at execution time.

## Rollout helper

The helper is:

`scripts/deploy/phase14_concurrent_partition_retirement_rollout_cloudshell.sh`

Before any GCP contact it requires:

- clean local exact-main checkout;
- remote `main` equal to the supplied helper SHA;
- immutable candidate branch/head unchanged;
- candidate descended from the deployed production head;
- exact four-file candidate scope;
- all four candidate blobs equal to exact main;
- an exact separate production approval token binding helper head, from head, candidate head, and accepted storage-evidence SHA.

Production preflight then requires:

- deployed checkout exactly at the expected from head;
- accepted storage evidence digest unchanged;
- installed recorder/storage unit fragments equal to the deployed repository with no drop-ins;
- recorder and maintenance units bound to `/etc/bp/bp.env`;
- both safety files in `MODE=research`;
- `LIVE_TRADING_ENABLED=false`;
- `MAX_TRADE_SIZE_USD=0`;
- `MAX_DAILY_LOSS_USD=0`;
- every deployed `automatic_promotion` field false;
- PostgreSQL, recorder, dashboard, legacy paper/predictor, prospective outcomes, frozen V3 predictor, and frozen V3 paper execution active;
- storage-maintenance, storage-disk-health, V2 forward-coverage, and V4 forward-coverage timers active+enabled;
- composite partitioned-storage health `ok` with maintenance-fresh/current-partition/retention guards true;
- no pending concurrent detach and no detached raw retirement leftovers;
- at least one actually eligible expired raw partition, so acceptance cannot pass on a no-op cycle.

## Authorized sequence when separately approved

A future explicit authorization may permit only this helper-defined sequence:

1. Freeze automatic storage maintenance by stopping only `bp-storage-maintenance.timer`.
2. Fetch and revalidate the immutable four-file candidate.
3. Switch `/opt/bp` detached to the candidate without force.
4. Revalidate safety, unit contracts, active services/timers, checkout scope, and candidate blobs.
5. Run one real `bp-storage-maintenance.service` cycle while the recorder and V3 paper services remain active.
6. Require the cycle to succeed and its new `storage_maintenance_runs` row to record at least one retired partition.
7. Require recorder MainPID and restart count unchanged.
8. Require frozen V3 predictor and execution MainPIDs unchanged.
9. Require no pending/detached retirement leftovers.
10. Run the established natural-load recorder soak and require all four feeds with zero backpressure.
11. Reconfirm dashboard RESEARCH/live-disabled safety and zero-money environment safety.
12. Re-run composite storage health and require `ok` with every storage guard true.
13. Restore the storage-maintenance timer.
14. Write durable acceptance evidence under `/var/lib/bp/evidence/phase14-concurrent-partition-retirement-rollout-<timestamp>.json`.

The helper does not restart the recorder, V3 services, or V4 collector on the passing path.

## Fail-closed rollback

Any failure after candidate checkout arms fail-closed rollback.

The rollback:

1. stops automatic storage maintenance;
2. stops frozen V3 execution and predictor;
3. stops the recorder;
4. keeps the candidate checkout long enough to run one stopped-recorder maintenance reconciliation;
5. requires no pending or detached raw retirement leftovers before old-code checkout is allowed;
6. only after successful reconciliation returns `/opt/bp` to the original production head;
7. restores storage-health, storage-maintenance, V2, and V4 timers;
8. leaves the recorder and frozen V3 services stopped for a separately authorized recovery.

If candidate reconciliation itself cannot complete, the helper does **not** return to the old checkout. It leaves the candidate code in place, the recorder stopped, and reports:

`PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_ROLLBACK=INCOMPLETE_RECONCILIATION_REQUIRED`

This prevents old maintenance code from silently losing visibility of a pending or already-detached raw table.

## Authorization boundary

Repository merge, candidate verification, and CI success are engineering evidence only.

Do not set `PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_APPROVAL` and do not execute the helper until a separate explicit production authorization is granted. The token format is:

`I_APPROVE_PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT:<helper-head>:7c3af78da1922a0e5187c24b799951130cc98887:ed7d930c69e417dda388b0cb62b3a543a4b8134f:f33a28f5306e46c509b0000a176d226c079aa2d160d595095ca228118542ce19`

That future authorization would cover only the helper-defined research/storage rollout and rollback. It would not authorize Gate B work, holdout access, V3/V4 tuning, automatic promotion, Phase 15, geographic bypass, live trading, or nonzero money.
