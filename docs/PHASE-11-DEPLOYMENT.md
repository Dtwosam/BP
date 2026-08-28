# Phase 11 Dashboard V1 Deployment

Phase 11 adds a read-only operator dashboard for the existing research system. It does **not** add paper execution, wallet/signing support, order placement, positions, or live trading.

The dashboard is deliberately localhost-only. The Python snapshot API listens on `127.0.0.1:8787`; the Next.js web process listens on `127.0.0.1:3000`. Remote operator access must use an SSH tunnel rather than exposing either listener publicly.

## Safety invariants

Before candidate acceptance or permanent installation, all of the following must remain true on the recorder host:

- `MODE=research`
- `LIVE_TRADING_ENABLED=false`
- `MAX_TRADE_SIZE_USD=0`
- `MAX_DAILY_LOSS_USD=0`
- `bp-recorder.service` is active
- `bp-postgres.service` is active
- dashboard services run as the unprivileged `bp` user
- dashboard network access is constrained to localhost by systemd
- the snapshot API rejects mutation requests
- paper P&L remains `UNAVAILABLE_UNTIL_PHASE_12` with no manufactured value

Phase 11 acceptance is invalid if any of these conditions is weakened.

## 1. Verify the candidate in CI

Use an exact commit SHA from `phase-11-dashboard-v1`. Both CI jobs must be green on that exact SHA before touching the host.

The Python lane must pass lint, the complete pytest suite, deployment-asset syntax validation, and the engine health check. The dashboard lane must pass its tests, strict TypeScript typecheck, and production Next.js build under Node `24.20.0`.

Do not use a moving branch head as host evidence. Pin the exact verified commit SHA.

## 2. Run isolated host acceptance first

Run the Cloud Shell wrapper with the exact CI-verified SHA:

```bash
export PHASE11_HEAD=<CANDIDATE_SHA>
curl -fsSL \
  "https://raw.githubusercontent.com/Dtwosam/BP/${PHASE11_HEAD}/scripts/deploy/phase11_cloudshell_accept.sh" \
  -o /tmp/phase11_cloudshell_accept.sh
bash /tmp/phase11_cloudshell_accept.sh
```

The wrapper defaults to:

- project: `project-4397f2c0-7098-4c1c-abb`
- zone: `us-east1-c`
- VM: `bp-recorder`
- branch: `phase-11-dashboard-v1`

These may be overridden with `PHASE11_PROJECT`, `PHASE11_ZONE`, `PHASE11_VM`, and `PHASE11_BRANCH` when the infrastructure changes.

The wrapper verifies that the fetched branch head still equals `PHASE11_HEAD`, creates a detached exact-head candidate source tree, and launches a disconnect-resilient one-shot systemd job on the recorder host.

Candidate services use isolated loopback ports by default:

- API: `127.0.0.1:18787`
- web: `127.0.0.1:13000`

They are temporary and are cleaned up after the acceptance run. They must not replace, stop, or restart `bp-recorder.service`.

A successful run must include:

```text
PHASE11_HOST_ACCEPTANCE=PASS
HEAD=<CANDIDATE_SHA>
API_LISTENER=127.0.0.1:18787
WEB_LISTENER=127.0.0.1:13000
RECORDER_STATUS=active
```

The latest wrapper log is stored at:

```text
/var/lib/bp/evidence/phase11-host-acceptance-latest.log
```

Detailed candidate evidence is stored below:

```text
/var/lib/bp/evidence/phase11-dashboard/<UTC_TIMESTAMP>/
```

The evidence includes candidate provenance, Node/npm versions, frontend install/test/typecheck/build logs, API health and snapshot payloads, the proxied web snapshot, rendered dashboard HTML, and copies of the hardened service units.

Do not proceed to permanent installation if the PASS token is absent, the exact head differs, either service is non-loopback, a mutation request is accepted, the dashboard invents paper P&L, or the recorder is not active after acceptance.

## 3. Install the accepted dashboard

Only after exact-head CI and isolated host acceptance both pass, update `/opt/bp` to that same accepted SHA. Confirm the checkout before installation:

```bash
cd /opt/bp
git rev-parse HEAD
```

Then install with the same exact SHA:

```bash
sudo bash /opt/bp/scripts/deploy/phase11_install.sh <ACCEPTED_SHA>
```

The installer fails closed if the repository head differs from the supplied SHA or the research-only environment contract is not satisfied.

Before changing dashboard services, it:

1. verifies the hardened systemd unit contract;
2. downloads Node `24.20.0` from nodejs.org and verifies the archive against the published `SHASUMS256.txt` value;
3. installs the current Python package into the existing BP virtual environment;
4. runs dashboard dependency installation, tests, strict typecheck, and production build;
5. gives only `/opt/bp/apps/dashboard/.next/cache` the runtime write path required by Next.js.

It then atomically swaps the pinned Node runtime, installs/enables only the two dashboard units, and validates the API, web page, fail-closed snapshot mode, mutation rejection, loopback listeners, and recorder continuity.

If installation fails after dashboard changes begin, the installer restores the previous dashboard unit files, enablement/active state, and previous Node runtime. It does not stop or restart `bp-recorder.service`.

A successful permanent install emits:

```text
PHASE11_INSTALL=PASS
Dashboard is localhost-only at http://127.0.0.1:3000
```

It also writes a root-generated, `bp`-readable evidence record:

```text
/var/lib/bp/evidence/phase11-install-<UTC_TIMESTAMP>.txt
```

## 4. Operator access

Keep port `3000` private. From an operator workstation with SSH access to the VM, create a tunnel such as:

```bash
gcloud compute ssh bp-recorder \
  --project=project-4397f2c0-7098-4c1c-abb \
  --zone=us-east1-c \
  -- -L 3000:127.0.0.1:3000
```

Then open `http://127.0.0.1:3000` locally.

Do not add a public firewall rule for ports `3000` or `8787` as part of Phase 11.

## 5. Operational checks

On the host:

```bash
systemctl is-active bp-recorder.service
systemctl is-active bp-postgres.service
systemctl is-active bp-dashboard-api.service
systemctl is-active bp-dashboard-web.service

curl -fsS http://127.0.0.1:8787/health
curl -fsS http://127.0.0.1:8787/api/v1/snapshot
curl -fsS http://127.0.0.1:3000/api/snapshot

ss -ltn | grep -E '127\.0\.0\.1:(8787|3000)'
```

A POST to the API snapshot path must remain rejected:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  -X POST http://127.0.0.1:8787/api/v1/snapshot
```

Expected HTTP status: `405`.

Useful logs:

```bash
journalctl -u bp-dashboard-api.service -n 100 --no-pager
journalctl -u bp-dashboard-web.service -n 100 --no-pager
```

## 6. Phase 11 acceptance boundary

Phase 11 is not closed merely because CI or a local build passes. Closeout requires production-host evidence showing that an operator can understand active markets, observed market/model state, feed health, immutable prediction history, available official-evaluation performance/calibration, current RESEARCH mode, and the explicit Phase 12 paper-P&L boundary without opening PostgreSQL directly.

Until that host acceptance evidence exists, project state must remain Phase 11 with host acceptance pending.

Paper execution begins only in Phase 12. Real-money execution remains blocked by later build-order gates and explicit authorization, and `LIVE_TRADING_ENABLED=false` remains unchanged.
