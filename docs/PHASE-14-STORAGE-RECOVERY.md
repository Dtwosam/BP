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
- the exact recovery evidence path recorded in candidate `PROJECT_STATE.json` contains exactly 24 contiguous one-hour intervals;
- PostgreSQL can answer read-only storage-shape queries.

The output includes current relation bytes/estimated rows, whether raw storage is already partitioned, whether legacy/ledger tables exist, timer states, data-disk/root free bytes, archive evidence identity, and `MUTATIONS_PERFORMED=false`.

## Run from Google Cloud Shell

Use a **clean local checkout at the exact candidate SHA**. The evidence runner refuses to proceed if the local `HEAD` differs from `PHASE14_PARTITIONED_STORAGE_HEAD` or if the local working tree is dirty, so the helper and verifier used for evidence are bound to the candidate being evaluated. It also reads `phase_14_storage_reliability_followup.archive_recovery_host_evidence` from that exact candidate `PROJECT_STATE.json`, validates the canonical host path, and passes it to the remote preflight. The preflight no longer chooses a recovery JSON by mtime.

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

The transcript and verified JSON are created in Cloud Shell, not on the production VM. By default they are written under `$HOME`; custom paths may be supplied with `PHASE14_STORAGE_PREFLIGHT_TRANSCRIPT` and `PHASE14_STORAGE_PREFLIGHT_VERIFIED`. Do not substitute a shortened SHA and do not run the wrapper from a modified checkout. After verification succeeds, the wrapper also prints `VERIFIED_SHA256=<64-hex>` for review and approval bookkeeping. That output is evidence only: the wrapper never sets `PHASE14_PARTITIONED_STORAGE_APPROVED_PREFLIGHT_SHA256` and does not authorize migration.

The raw preflight must end with:

```text
PHASE14_PARTITIONED_STORAGE_PREFLIGHT=PASS
MUTATIONS_PERFORMED=false
```

The independent verifier then rejects conflicting duplicate fields, stale/unexpected SHAs, an active recorder, an already/partially migrated raw schema, a non-canonical recovery archive path, any mutation claim, or insufficient migration headroom. Required free space is the larger of the configured 40 GiB floor and the current raw relation size plus the unchanged 15 GiB critical reserve.

A verified JSON result has `"verdict": "PASS"`, `"mutations_performed": false`, and `"storage_shape": "legacy_unmigrated"`. Its `target` block preserves the exact `PROJECT`, `ZONE`, and `VM` from the captured preflight, and its archive block carries the exact SHA-256 of the canonical recovery evidence file captured read-only during preflight. It intentionally omits the PostgreSQL mount source and display-only relation-size strings.

A preflight/verifier PASS is evidence that the host satisfies the non-mutating prerequisites at that moment. It is **not** authorization to run `phase14_partitioned_storage_rollout_cloudshell.sh` and does not imply migration acceptance.

## Production migration boundary

The partitioned-storage rollout remains separately gated. Engineering verification, a read-only preflight PASS, and merge to `main` do **not** authorize the migration.

Run the rollout helper only from a clean local BP checkout whose `HEAD` exactly equals `PHASE14_PARTITIONED_STORAGE_HEAD`. The helper fails before any gcloud interaction on a local candidate-head mismatch or any tracked/untracked working-tree change, so stale or locally modified launcher code cannot be used for a newer candidate.

The rollout helper now machine-enforces that separate approval boundary before any production contact. It requires `PHASE14_PARTITIONED_STORAGE_APPROVED_FROM_HEAD` and `PHASE14_PARTITIONED_STORAGE_APPROVED_HEAD` to be exact 40-character SHAs matching the deployed-from and candidate SHAs, and it requires `PHASE14_PARTITIONED_STORAGE_APPROVED_PREFLIGHT_SHA256` to be an exact 64-character SHA-256 matching the single-snapshot digest of the verified preflight JSON. Missing or malformed approval inputs fail with `migration_approval_missing_or_invalid`; a valid but different preflight digest fails with `migration_approval_preflight_mismatch`, all before `gcloud config set project` or VM contact. These approval values are not produced as authorization by the read-only preflight and must not be inferred from a PASS. The exact verified JSON digest may be calculated for review, but set the approved digest only after the separate production-migration approval explicitly covers that exact SHA transition and verified-preflight snapshot.

When migration is separately and explicitly approved, the rollout helper now requires `PHASE14_PARTITIONED_STORAGE_PREFLIGHT_VERIFIED` to point to the readable absolute path of the verified JSON produced by the preflight operator. Before it contacts the production VM, it fails closed unless that JSON reports `PASS`, matches the exact deployed-from and candidate/remote SHAs, reports `mutations_performed=false`, confirms `RECORDER_STATE=stopped`, identifies `storage_shape=legacy_unmigrated`, and carries `PROJECT` / `ZONE` / `VM` target identity that exactly matches the rollout target. A PASS captured from a different project, zone, or VM cannot authorize contact with another host. The helper reads the verified preflight JSON bytes once, computes the SHA-256 from that same byte snapshot, validates the JSON from those same bytes, and records that digest in the eventual rollout evidence. This prevents the recorded digest from drifting from the evidence fields actually validated if the local file changes between separate reads. It also extracts the verified archive `evidence_name`, SHA-256, and `window_end` and carries all three into the detached worker. The worker requires that exact canonical recovery file, re-hashes its current bytes, requires the digest to equal the preflight-captured SHA-256, and then checks the matching window. A same-named recovery file whose contents changed after preflight therefore fails closed before migration.

The host worker then independently rechecks the live storage boundary. It first re-queries the live schema shape and requires `raw_market_events` to remain non-partitioned while both `raw_market_events_legacy` and `raw_event_dedupe` remain absent. It also measures the current legacy `raw_market_events` total relation bytes using the configured `POSTGRES_USER` / `POSTGRES_DB`, recomputes required free space as `max(configured floor, raw relation bytes + 15 GiB critical reserve)`, and fails closed on insufficient headroom. The archive evidence, schema-shape, and dynamic-headroom checks all run before mutation and again after managed services are stopped and the recorder is re-confirmed stopped, immediately before candidate checkout/migration. This closes the remaining in-worker time-of-check/time-of-use window for the preflight-bound archive and prevents stale evidence from silently weakening the mutation boundary.

After migration parity succeeds, rollout acceptance now requires a real archive-to-partition-drop cycle rather than merely a successful maintenance command. The helper records total attached `raw_market_events` child-relation bytes, runs one partitioned maintenance cycle, requires at least one retired interval with `archived_rows > 0`, requires `dedupe_rows_removed == archived_rows` for every non-empty retired interval, then measures attached partition bytes again. The rollout fails through the existing rollback path unless the post-maintenance total is strictly lower. Eventual rollout evidence records the before, after, and released byte counts plus the verified non-empty retirement flag. It also records the exact preflight-bound recovery evidence SHA-256 and `window_end` alongside the source archive path and verified-preflight digest, so a future accepted rollout can be audited back to the same recovery artifact that passed the pre-mutation checks. The same acceptance JSON also records the exact preflight-verified `PROJECT`, `ZONE`, and `VM`, so the artifact can be audited back to the production target whose identity was checked before any cloud contact. It now also records the explicit approval scope (`from_sha`, `candidate_sha`, and approved verified-preflight SHA-256). The detached worker requires that tuple to still equal its expected transition and verified-preflight digest before continuing, so final evidence cannot claim a different authorization scope than the one validated by the launcher. Final rollback retention is also proven live rather than inferred: the last pre-migration headroom check captures the original `raw_market_events` PostgreSQL relation OID, and after maintenance/service restoration the worker requires `raw_market_events_legacy` to retain that exact OID with nonzero relation bytes before PASS evidence is written. Acceptance JSON records the original OID, retained OID, and retained relation bytes. Composite partitioned-storage health is then rerun read-only after managed-unit restoration and the final rollback-material proof; the same `DISK_JSON` consumed by acceptance evidence is overwritten by this final snapshot, so PASS evidence cannot rely on a health result captured before services were restored. Before installing the final JSON, the worker computes and validates `EVIDENCE_TMP_SHA256`; after install it computes and validates `EVIDENCE_SHA256` from the canonical acceptance artifact and requires the two digests to match byte-for-byte. After that equality proof it runs `sync -f` on the canonical evidence path and fails closed with `rollout_evidence_sync_failed` if the containing filesystem cannot be synchronized. The worker marks the canonical artifact as installed only after `install` succeeds. If any later step fails while rollback is still armed, the exit trap first attempts to remove that unaccepted canonical artifact and sync the evidence filesystem, reports `ROLLBACK_EVIDENCE_CLEANUP=PASS` or `FAILED`, and then invokes rollback regardless of cleanup outcome. Only after the durability sync succeeds does the success path remove the temp file and set `ROLLBACK_ARMED=0`; the installed digest is printed with the PASS output so the exact accepted evidence file has an operator-visible identity, is proven to be the bytes that were generated, and is flushed before rollback protection is disarmed.

Only after those checks and the separate explicit authorization may the helper perform the controlled migration with rollback armed. A successful migration must still leave:

- `RECORDER_RESTARTED=false`;
- `ROLLBACK_MATERIAL_RETAINED=true`;
- research/zero-money safety unchanged;
- Gate B unauthorized;
- selected-book freshness unchanged at exactly 10 seconds;
- Phase 15 blocked.

After host migration acceptance and a verified archive-to-partition-drop cycle demonstrates physical relation-size release, recorder restart remains a separate operational step using the already-merged recorder reliability repair and `RECORDER_WRITER_WORKERS=4`.
