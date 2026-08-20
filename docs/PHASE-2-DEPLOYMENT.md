# Phase 2 — Always-On Recorder Deployment

**Status:** Pre-host deployment package  
**Phase:** 2 — 24/7 raw recorder  
**Live trading:** Disabled

This runbook deploys the Phase 2 recorder on a small Ubuntu host while keeping the architecture portable. PostgreSQL runs in Docker; the Python recorder runs directly under systemd so the recorder can verify the host's NTP synchronization state.

## Why this shape

- The recorder requires trustworthy UTC time and checks host NTP before starting.
- PostgreSQL stays bound to `127.0.0.1`; port 5432 is not exposed publicly.
- The recorder runs as a dedicated non-login `bp` user and does not receive Docker-socket access.
- systemd restarts the recorder after unexpected exits.
- Docker keeps the database portable across hosts.
- `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0` remain explicit deployment defaults.

## Zero-cost host target

The source of truth prefers Oracle Cloud Infrastructure Always Free Ampere A1 when capacity/account creation is available.

Re-verified on 21 August 2026 against Oracle's current documentation:

- Always Free A1 allocation for an Always Free tenancy is equivalent to 2 OCPUs and 12 GB RAM total.
- Always Free compute must be created in the tenancy's home region.
- the minimum boot volume is 47 GB and the account has 200 GB of Always Free block volume allocation;
- Always Free capacity can be temporarily unavailable;
- Oracle may reclaim an Always Free VM that remains idle under its published 7-day utilization criteria.

Official references:

- https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm
- https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm

This makes OCI suitable for the zero-cost validation objective, but it is not treated as a permanent infrastructure dependency.

## Recommended VM

For the first 24-hour gate:

- Ubuntu 24.04 LTS;
- OCI `VM.Standard.A1.Flex` when available;
- up to the Always Free 2 OCPU / 12 GB RAM allowance;
- a boot volume comfortably inside the free block-volume allowance;
- one public IPv4 address for SSH only.

No inbound application port is required. Do **not** expose PostgreSQL port 5432 in the cloud firewall/security list.

## 1. Clone the recorder branch

During Phase 2 validation, use the exact Phase 2 branch rather than `main`:

```bash
sudo git clone --branch build/phase-2-recorder --single-branch \
  https://github.com/Dtwosam/BP.git /opt/bp
```

After Phase 2 is formally merged, deployment can switch to the approved stable ref.

## 2. Bootstrap the host

```bash
cd /opt/bp
sudo bash scripts/deploy/bootstrap_ubuntu.sh
```

The bootstrap is idempotent enough for the Phase 2 host and will:

- install/check Python 3.12+, Docker Compose, Chrony, Git, and OpenSSL;
- set the host timezone to UTC;
- create the non-login `bp` service account;
- create `/opt/bp/.venv` and install the project;
- generate a URL-safe random PostgreSQL password on the host if `/etc/bp/bp.env` does not already exist;
- keep `/etc/bp/bp.env` readable only by root and the `bp` group;
- install and enable `bp-postgres.service` and `bp-recorder.service`;
- start the database and recorder.

The generated database password is never printed and must never be committed or pasted into ChatGPT.

## 3. Verify the running recorder

```bash
sudo systemctl status bp-postgres --no-pager
sudo systemctl status bp-recorder --no-pager
sudo journalctl -u bp-recorder -n 100 --no-pager
sudo timedatectl show -p NTPSynchronized --value
```

Expected:

- both services are active;
- `NTPSynchronized` is `yes`;
- recorder logs do not show repeated startup failures;
- `LIVE_TRADING_ENABLED` remains false.

To follow logs:

```bash
sudo journalctl -u bp-recorder -f
```

## 4. Formal 24-hour Phase 2 gate

Keep the recorder running continuously. Once the database contains a full 24-hour observation window, run:

```bash
sudo /opt/bp/scripts/deploy/phase2_soak_report.sh
```

The script refuses to certify the run if the recorder service is not active. It evaluates the latest 24 hours with the existing Phase 2 soak auditor and saves the JSON evidence under:

```text
/var/lib/bp/evidence/phase2-soak-<UTC timestamp>.json
```

A passing report requires all four required feeds to have data and rejects:

- backpressure incidents;
- clock-skew incidents;
- unresolved stale-feed states;
- an observation window shorter than the required duration.

Reconnect/disconnect incidents remain visible in the report and must be reviewed; they are not silently hidden.

## 5. Phase 2 closeout

Do **not** advance to Phase 3 merely because the service stayed online for a day.

Before Phase 2 closes:

1. inspect the 24-hour JSON report;
2. review recorder journal entries for unexplained gaps/restart loops;
3. copy a sanitized evidence report into the repository;
4. update `PROJECT_STATE.json`;
5. update `docs/CHANGELOG.md`;
6. add the Phase 2 closeout note;
7. only then begin retention/aggregation work from Phase 3.

## Recovery commands

Restart recorder only:

```bash
sudo systemctl restart bp-recorder
```

Restart database and recorder:

```bash
sudo systemctl restart bp-postgres
sudo systemctl restart bp-recorder
```

Inspect database container:

```bash
sudo docker compose --env-file /etc/bp/bp.env \
  -f /opt/bp/docker-compose.prod.yml ps
```

The PostgreSQL data is held in the named Docker volume `bp_postgres_data`; restarting or recreating the container does not intentionally delete that volume.

## Updating code during Phase 2

Do not casually update the recorder during the formal 24-hour gate. A code change invalidates the single-version soak evidence.

Outside an active formal soak:

```bash
cd /opt/bp
sudo git fetch origin
sudo git checkout build/phase-2-recorder
sudo git pull --ff-only
sudo /opt/bp/.venv/bin/python -m pip install --disable-pip-version-check -e /opt/bp
sudo systemctl restart bp-recorder
```

Record the new commit SHA before starting another formal soak.
