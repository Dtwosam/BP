# Phase 12 — Paper Execution Deployment and Acceptance

Phase 12 deploys deterministic paper execution only. It does not enable real order placement. The production safety boundary remains:

```text
MODE=research
LIVE_TRADING_ENABLED=false
MAX_TRADE_SIZE_USD=0
MAX_DAILY_LOSS_USD=0
```

The worker uses the same execution protocol intended for a later controlled implementation, but the Phase 12 runtime contains only the paper adapter and append-only paper ledgers. It has no public listener and the hardened systemd unit permits network access only to localhost so it can reach PostgreSQL.

## 1. Preconditions

Do not run host acceptance until the exact Phase 12 candidate SHA has a fully green CI run: Python lint, full pytest, deployment validation, health check, dashboard tests, dashboard typecheck, and dashboard production build.

The following production services must already be active and remain active throughout acceptance:

```bash
systemctl is-active bp-recorder.service
systemctl is-active bp-postgres.service
systemctl is-active bp-dashboard-api.service
systemctl is-active bp-dashboard-web.service
```

The production environment must still resolve to the four fail-closed values shown above. Phase 12 does not change those values.

## 2. Isolated exact-head host acceptance

From Google Cloud Shell, pin the exact green candidate SHA and run the helper fetched from that same immutable SHA:

```bash
export PHASE12_HEAD=<GREEN_CANDIDATE_SHA>

curl -fsSL \
  "https://raw.githubusercontent.com/Dtwosam/BP/${PHASE12_HEAD}/scripts/deploy/phase12_cloudshell_accept.sh" \
  -o /tmp/phase12_cloudshell_accept.sh

bash /tmp/phase12_cloudshell_accept.sh
```

Defaults remain the production recorder host:

```text
project: project-4397f2c0-7098-4c1c-abb
zone: us-east1-c
vm: bp-recorder
branch: phase-12-paper-execution-v1
```

The helper fetches the exact branch ref, rejects any SHA mismatch, archives the candidate into an isolated runtime directory, and launches `phase12_host_acceptance.sh` through a disconnect-resilient transient systemd job. Re-running the Cloud Shell helper reattaches to the same exact-head acceptance job when it is still running.

The host gate creates an isolated candidate virtual environment, verifies/creates the append-only paper schema, and runs the paper worker with the production database while keeping the permanent worker disabled. It requires RESEARCH mode, live disabled, zero real-money limits, and continuity of the recorder, PostgreSQL, and both Phase 11 dashboard services.

Acceptance observes prospective 5m/15m immutable predictions. If a bounded window contains only `trade=false` signals, it records that fact and extends the observation window instead of manufacturing an order. If an eligible `trade=true` signal appears, the gate requires a real paper order linked to that immutable signal and a terminal state. Any fills must be causal, occur inside the simulated order lifetime, use a recorded book anchor, and respect the limit price. A zero-fill order must have an explicit no-fill terminal reason.

The gate then reruns the same target idempotently and requires reconciliation status `OK` with zero violations and nonnegative derived paper cash.

A successful isolated gate emits:

```text
PHASE12_HOST_ACCEPTANCE=PASS
HEAD=<GREEN_CANDIDATE_SHA>
RECONCILIATION_STATUS=OK
IDEMPOTENT_RERUN=PASS
RECORDER_STATUS=active
POSTGRES_STATUS=active
DASHBOARD_API_STATUS=active
DASHBOARD_WEB_STATUS=active
```

Sanitized host evidence is stored under:

```text
/var/lib/bp/evidence/phase12-paper-execution/<UTC_TIMESTAMP>/
```

Do not proceed to permanent installation if the PASS token is absent, the exact head differs, reconciliation reports any violation, paper cash is negative, or any Phase 11 production service is disturbed.

## 3. Install the exact accepted SHA

Only after isolated host acceptance passes, update `/opt/bp` to that exact accepted SHA and confirm it before installation:

```bash
cd /opt/bp
git rev-parse HEAD
```

Then run:

```bash
sudo bash /opt/bp/scripts/deploy/phase12_install.sh <ACCEPTED_SHA>
```

The installer refuses a repository/head mismatch. It also rechecks:

```text
MODE=research
LIVE_TRADING_ENABLED=false
MAX_TRADE_SIZE_USD=0
MAX_DAILY_LOSS_USD=0
```

Before activation it validates the hardened worker unit, installs the current Python package, creates any missing paper tables/indexes, runs dashboard dependency checks/tests/typecheck, and performs a fresh production dashboard build. The existing Phase 11 Node `24.20.0` runtime is required rather than replaced.

The installer runs the paper worker in one-shot mode before enabling the permanent service, restarts only the dashboard services needed to expose Phase 12 evidence, and then enables `bp-paper-execution.service`. It never stops or restarts `bp-recorder.service` to make the gate pass.

If activation fails after runtime changes begin, rollback is limited to the paper worker unit and dashboard build/runtime state owned by this installer. Recorder and PostgreSQL continuity remain mandatory.

A successful install emits:

```text
PHASE12_INSTALL=PASS
```

and writes a root-generated, `bp`-readable evidence record:

```text
/var/lib/bp/evidence/phase12-install-<UTC_TIMESTAMP>.txt
```

## 4. Operational checks

After installation:

```bash
systemctl is-active bp-recorder.service
systemctl is-active bp-postgres.service
systemctl is-active bp-dashboard-api.service
systemctl is-active bp-dashboard-web.service
systemctl is-active bp-paper-execution.service

curl -fsS http://127.0.0.1:8787/health
curl -fsS http://127.0.0.1:8787/api/v1/snapshot
curl -fsS http://127.0.0.1:3000/api/snapshot
```

The dashboard snapshot must report real execution unavailable, paper execution available, paper P&L status `AVAILABLE`, and paper reconciliation status `OK` with zero violations. The read-only dashboard must continue to reject mutation requests.

Useful logs:

```bash
journalctl -u bp-paper-execution.service -n 100 --no-pager
journalctl -u bp-dashboard-api.service -n 100 --no-pager
journalctl -u bp-dashboard-web.service -n 100 --no-pager
```

## 5. Phase 12 closeout boundary

Phase 12 is not complete from CI alone. Closeout requires both `PHASE12_HOST_ACCEPTANCE=PASS` and `PHASE12_INSTALL=PASS` on the exact accepted operational SHA, plus sanitized production evidence for paper orders, paper fills, paper settlements, no-fill/no-trade behavior, paper account math, and reconciliation.

Paper P&L is simulated execution evidence, not a claim of live profitability. Real-money execution remains disabled. Live readiness and any later controlled launch remain separate build-order phases with their own safety gates and explicit authorization requirements.
