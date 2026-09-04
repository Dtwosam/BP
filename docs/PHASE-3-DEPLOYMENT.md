# Phase 3 — Bounded Storage Deployment

**Phase:** 3 — retention, compact state, archives, disk protection  
**Live trading:** Disabled

> **Phase 14 supersession note — 4 September 2026**
>
> This document remains the historical Phase 3 deployment/acceptance record and still defines the canonical 24-hour hot raw + 24 additional archive-hour retention meaning. The original archive-before-bounded-`DELETE` procedure is retained for legacy/compatibility storage, but it is no longer the approved production physical-capacity mechanism at the observed recorder volume.
>
> Phase 14 incident evidence showed a monolithic `raw_market_events` relation of approximately 157 GB while root free space approached the existing critical reserve. PostgreSQL row deletion bounded logical retention but did not reliably return relation files to the filesystem. D-034 therefore supersedes the production physical-retirement mechanism with exact hourly archive verification followed by PostgreSQL child-partition drop and then dedupe-ledger cleanup.
>
> **Current recovery host warning:** do not follow the recorder-restart steps in this historical runbook while the 4 September 2026 recovery is active. `bp-recorder.service` remains intentionally stopped. The partitioned production migration has not been performed and requires a separate explicit authorization plus exact-SHA host acceptance. Production configuration must set `STORAGE_HEALTH_PATH=/mnt/bp-data` only after the rollout helper verifies PostgreSQL and the archive directory are actually on that protected filesystem. Never bypass the existing disk thresholds or manually delete unarchived raw rows.

Phase 3 keeps the Phase 2 recorder architecture but bounds high-rate raw storage. It adds a one-second compact market-state table, verified compressed raw archives, bounded deletion, archive/state retention, and disk-health timers.

The safety defaults remain explicit:

```text
LIVE_TRADING_ENABLED=false
MAX_TRADE_SIZE_USD=0
MAX_DAILY_LOSS_USD=0
STORAGE_HOT_RAW_HOURS=24
STORAGE_ARCHIVE_RETENTION_HOURS=24
STORAGE_STATE_RETENTION_DAYS=90
STORAGE_ARCHIVE_DIR=/var/lib/bp/archive/raw
STORAGE_WARNING_FREE_GIB=25
STORAGE_CRITICAL_FREE_GIB=15
STORAGE_DELETE_BATCH_SIZE=50000
```

Raw rows are never deleted unless the exact closed interval has a readable gzip archive and manifest whose SHA-256, compressed byte count, and row count verify. The maintenance process exits at critical free space rather than deleting unarchived raw data. A critical disk-health failure also stops the recorder, and recorder startup is conditioned on the same disk-health check, so the critical reserve is not consumed by continued ingestion.

## 1. Deploy the exact Phase 3 candidate

Do not update code while a retention verification step is in progress. Record the candidate SHA first.

```bash
cd /opt/bp
sudo git fetch origin build/phase-3-retention
sudo git checkout build/phase-3-retention
sudo git pull --ff-only
sudo git rev-parse HEAD
sudo /opt/bp/.venv/bin/python -m pip install --disable-pip-version-check -e /opt/bp
```

Keep that SHA with the Phase 3 evidence.

## 2. Install storage configuration and unit files

Running the bootstrap is safe for an existing host: it preserves the existing database password and appends only missing Phase 3 storage defaults. It installs the timer units but intentionally does not enable them.

```bash
cd /opt/bp
sudo bash scripts/deploy/bootstrap_ubuntu.sh
```

Verify the archive directory and installed units:

```bash
sudo ls -ld /var/lib/bp/archive/raw
sudo systemctl cat bp-storage-maintenance.service
sudo systemctl cat bp-storage-maintenance.timer
sudo systemctl cat bp-storage-disk-health.service
sudo systemctl cat bp-storage-disk-health.timer
sudo systemctl cat bp-storage-critical-stop.service
```

Do not enable the timers until the manual compact-state, report, archive, checksum, and deletion checks below have passed.

## 3. Verify compact one-second state is advancing

Restarting the recorder on the Phase 3 candidate creates the new table if needed and starts the compact-state snapshotter. Recorder startup first runs the bounded disk-health check; critical free space leaves the recorder stopped.

```bash
sudo systemctl restart bp-recorder
sleep 10
sudo systemctl is-active bp-recorder
sudo journalctl -u bp-recorder -n 50 --no-pager
```

Then inspect the four required compact feeds:

```bash
sudo docker compose --env-file /etc/bp/bp.env -f /opt/bp/docker-compose.prod.yml exec -T postgres \
  psql -U bp -d bp -c "SELECT source, stream, count(*) AS rows, max(last_event_at) AS latest FROM market_state_1s GROUP BY source, stream ORDER BY source, stream;"
```

Expected feed pairs:

- `bybit / spot`
- `bybit / linear`
- `coinbase / spot`
- `polymarket / market`

Do not proceed if a required feed is absent or its `latest` timestamp is not advancing.

## 4. Capture the pre-maintenance storage report

Run the evidence report and disk-health check before deleting anything. The explicit env file makes the manual commands use the same host database and storage configuration as systemd.

```bash
sudo -u bp /opt/bp/.venv/bin/python /opt/bp/scripts/storage_maintenance.py report \
  --env-file /etc/bp/bp.env \
  | sudo tee /var/lib/bp/evidence/phase3-storage-before.json

sudo -u bp /opt/bp/.venv/bin/python /opt/bp/scripts/storage_maintenance.py disk-health \
  --env-file /etc/bp/bp.env \
  | sudo tee /var/lib/bp/evidence/phase3-disk-before.json
```

A warning status is operationally visible but returns success. A critical status returns non-zero. Do not bypass that failure by manually deleting raw rows.

## 5. Run maintenance manually once

The maintenance command first prunes only expired archives that still verify and whose compact-state continuity is proven. It then checks disk health. Only after those checks can it archive eligible full UTC hours and delete the matching raw interval in bounded batches.

```bash
sudo -u bp /opt/bp/.venv/bin/python /opt/bp/scripts/storage_maintenance.py run \
  --env-file /etc/bp/bp.env \
  | sudo tee /var/lib/bp/evidence/phase3-maintenance-first.json
```

If it exits non-zero, inspect the JSON and journal output. Do not replace fail-closed behavior with a manual `DELETE` or `TRUNCATE`.

## 6. Verify archive and checksum evidence manually

List the resulting archive/manifest pairs:

```bash
sudo find /var/lib/bp/archive/raw -maxdepth 1 -type f -printf '%f %s bytes\n' | sort
```

For at least one new `.jsonl.gz` archive, calculate the compressed-file checksum directly:

```bash
archive=$(sudo find /var/lib/bp/archive/raw -maxdepth 1 -name '*.jsonl.gz' -type f | sort | head -n 1)
sudo sha256sum "$archive"
sudo cat "${archive}.manifest.json"
```

The SHA-256 printed by `sha256sum` must equal the manifest `sha256`. The archive must remain readable:

```bash
sudo gzip -t "$archive"
```

The manifest row count must be non-negative and the interval must be the same closed UTC interval reported by the maintenance result.

## 7. Verify raw retention and compact-state continuity

Capture a second report:

```bash
sudo -u bp /opt/bp/.venv/bin/python /opt/bp/scripts/storage_maintenance.py report \
  --env-file /etc/bp/bp.env \
  | sudo tee /var/lib/bp/evidence/phase3-storage-after.json
```

Confirm:

- raw data still covers the configured hot window, allowing the expected partial-hour boundary;
- old closed intervals removed from PostgreSQL have verified archive/manifest pairs;
- `market_state_1s` is still advancing on all four required feed pairs;
- disk free space is above the critical threshold;
- the recorder stayed healthy during maintenance.

Check the recorder and maintenance journals:

```bash
sudo systemctl is-active bp-recorder
sudo journalctl -u bp-recorder -n 100 --no-pager
sudo journalctl -u bp-storage-maintenance -n 100 --no-pager
```

## 8. Enable the timers only after manual verification

Once Sections 3–7 pass:

```bash
sudo systemctl enable --now bp-storage-maintenance.timer
sudo systemctl enable --now bp-storage-disk-health.timer
sudo systemctl list-timers 'bp-storage-*' --all --no-pager
```

The maintenance timer runs hourly. The disk-health timer runs every five minutes. Both services are oneshots; neither has `Restart=always`. A critical disk-health result fails visibly and triggers `bp-storage-critical-stop.service`, which stops `bp-recorder.service`. The recorder itself uses the same disk-health command as an `ExecCondition`, so it will not start while free space remains critical.

## 9. Observe one scheduled maintenance cycle

After at least one hourly timer activation:

```bash
sudo systemctl status bp-storage-maintenance.timer --no-pager
sudo systemctl status bp-storage-disk-health.timer --no-pager
sudo journalctl -u bp-storage-maintenance -n 100 --no-pager
sudo journalctl -u bp-storage-disk-health -n 100 --no-pager
sudo journalctl -u bp-storage-critical-stop -n 50 --no-pager
```

Capture another report and confirm the raw window, compact state, archive retention, and free-space status remain sane.

## 10. Phase 3 closeout evidence

Before declaring Phase 3 complete, preserve sanitized evidence for:

1. candidate commit SHA;
2. full CI, live smoke, and short-soak results;
3. pre-maintenance storage report;
4. first manual maintenance JSON;
5. at least one verified archive manifest plus its independent `sha256sum` output;
6. post-maintenance storage report;
7. four-feed compact-state continuity query;
8. timer status after one scheduled cycle;
9. final disk-health report.

Then update `PROJECT_STATE.json`, `docs/CHANGELOG.md`, `docs/DECISION-LOG.md`, and the Phase 3 PR. Do not start model development or enable trading until the Phase 3 storage gate is closed.

## Recovery

If disk health is critical, keep ingestion stopped while recovering capacity:

```bash
sudo systemctl stop bp-recorder.service
sudo systemctl disable --now bp-storage-maintenance.timer
sudo systemctl disable --now bp-storage-disk-health.timer
```

Do not manually delete PostgreSQL relation files, WAL files, unarchived raw rows, or Docker volumes. Recover enough filesystem headroom for PostgreSQL to start safely, then diagnose relation sizes and retention state before choosing database reclamation work.

Before restarting ingestion, require the configured disk-health check to return non-critical, and preferably `ok`:

```bash
sudo -u bp /opt/bp/.venv/bin/python /opt/bp/scripts/storage_maintenance.py disk-health \
  --env-file /etc/bp/bp.env
sudo systemctl start bp-recorder.service
```

If recorder startup is skipped, disk health is still critical; do not bypass the `ExecCondition`. Re-enable maintenance and disk-health timers only after the cause of the capacity failure is understood and storage verification is complete. Never delete unarchived raw rows as a disk-recovery shortcut.
