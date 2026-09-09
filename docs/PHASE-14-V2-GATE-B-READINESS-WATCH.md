# Phase 14 V2 Gate B Readiness Watch

**Status:** merged engineering package; production installation not authorized  
**Current production readiness:** `READY=false` from the accepted feature-only check at `2026-09-09T20:13:53Z`  
**Gate B:** unauthorized  
**Final holdout:** unread and untouched

## Purpose

The readiness watch removes the need for repeated manual Cloud Shell readiness checks while preserving the exact feature-only Gate B boundary.

It answers only one question:

> Does the current immutable V2 feature chronology satisfy the frozen unlabeled Gate B planning contract?

It does not run Gate B, read labels/outcomes, freeze a plan, select a policy, or evaluate the final holdout.

The merged package is:

- runtime: `scripts/run_v2_gate_b_readiness_watch.py`
- service: `deploy/bp-v2-gate-b-readiness-watch.service`
- timer: `deploy/bp-v2-gate-b-readiness-watch.timer`
- installer: `scripts/deploy/phase14_v2_gate_b_readiness_watch_install_cloudshell.sh`
- sidecar root after any future authorized install: `/opt/bp-v2-gate-b-readiness-watch`
- latest sanitized status after any future authorized install: `/var/lib/bp/status/v2-gate-b-readiness.json`

PR #167 merged the package to `main` as `80bf77da54fd1a2c89b4d43910457184bd06fead`. Final PR head `0513770c824baf91f82de74e2af6b4789ccaa7d9` passed CI `34410923330` with 1,039 tests, Historical Backfill Smoke `34410923248`, Live Recorder Smoke `34410923329`, and Recorder Short Soak `34410923305`. Post-merge CI `34411184671` also passed 1,039 tests plus deployment validation, research-mode health, and dashboard checks.

## Schedule

The timer schedule is:

`00,02,04,06,08,10,12,14,16,18,20,22:30 UTC`

Candidate analysis starts advance only in the frozen two-hour cadence and are anchored at `:20`. Running the watcher at `:30` gives the upstream forward-coverage collector time to materialize the newest feature rows before the next useful readiness observation.

The timer is persistent, so a missed scheduled run may execute after the host returns.

## Read-only contract

Every watcher cycle:

1. requires the packaged source of truth to keep every `gate_b_authorized` and `automatic_promotion` field false;
2. refuses to run if any `plan.json`, `selection.json`, `holdout.json`, or `summary.json` exists under a `phase14-v2-gate-b-*` evidence directory;
3. detects those artifact names by pathname only and does not open their contents;
4. requires the exact expected deployed `/opt/bp` git head;
5. requires the accepted four-worker recorder configuration;
6. requires recorder, PostgreSQL, dashboard API/web, paper execution, live predictor, and prospective outcomes services active;
7. requires storage-maintenance and disk-health timers active;
8. requires the V2 forward-coverage timer active and enabled;
9. requires composite partitioned-storage health `ok` with maintenance-fresh, current-partition-present, and retention-current guards all true;
10. requires RESEARCH mode, live trading disabled, and both real-money limits equal to zero;
11. executes only `assess_gate_b_readiness()` inside a PostgreSQL `SET TRANSACTION READ ONLY` transaction;
12. rechecks the Gate B artifact boundary and runtime/storage preconditions after the database read;
13. writes only a compact sanitized latest-status JSON after all checks pass.

The watcher locks the accepted feature-only contract:

- coverage input SHA-256 `aab75574aa7faf18e65358353403e5ec1a2b89dd42424eb7b0e3329bf683b099`;
- freshness candidates exactly `[1, 2, 5, 10]`;
- explicit `no_trade` included;
- minimum contiguous epoch exactly 18 hours;
- exactly three required ordinary folds;
- no label read;
- no plan artifact write;
- no selection artifact write;
- no holdout touch.

## Isolation from the production application checkout

The watcher is deliberately a versioned sidecar.

A future authorized install would unpack exact-main bytes under:

`/opt/bp-v2-gate-b-readiness-watch/releases/<helper-head>`

and point:

`/opt/bp-v2-gate-b-readiness-watch/current`

to that immutable release.

It does not checkout, reset, or otherwise modify `/opt/bp`. The service uses the existing production virtual environment only as the Python runtime while importing watcher code from the sidecar release.

Each release includes `REVISION.env`, binding the watcher to both:

- the exact watcher/helper main SHA; and
- the exact accepted production application SHA.

A head mismatch causes the watcher to fail rather than evaluate readiness with mixed code/runtime assumptions.

## Failure behavior

A watcher failure is diagnostic only.

There is deliberately no `OnFailure=bp-storage-critical-stop.service` relationship and no watcher path that stops or restarts the recorder or any core service.

If a scheduled cycle fails:

- it emits the failure to journald;
- it does not overwrite the last successful sanitized status;
- it does not run Gate B;
- it does not touch the final holdout;
- it does not alter `/opt/bp`;
- the normal recorder and storage fail-closed controls remain independent and unchanged.

The installer has its own rollback, limited to:

- watcher service/timer files;
- watcher timer enabled/active state;
- versioned sidecar release/current symlink;
- watcher latest-status file.

Installer rollback does not change the production application checkout and does not start, stop, or restart recorder/core services.

## Status semantics

A successful watcher cycle prints:

```text
PHASE14_V2_GATE_B_READINESS_WATCH=PASS
READY=true|false
HOLDOUT_TOUCHED=false
```

`READY=false` means immutable V2 evidence continues accumulating.

`READY=true` means only that the frozen feature-only chronology can form the required plan. It does **not** authorize:

- Gate B execution;
- label/outcome access for Gate B;
- plan/selection artifact creation;
- final-holdout evaluation;
- policy/model/calibration/edge acceptance;
- prospective V2 activation;
- automatic promotion;
- Phase 15;
- live trading;
- nonzero money limits.

## Production installation boundary

The installer performs a production mutation because it creates the sidecar release, installs systemd unit files, enables the new timer, and writes the sanitized status/evidence files.

That installation has **not** been authorized and has **not** been performed.

Until a separate explicit production-install authorization exists, use only the existing exact-main manual feature-only readiness helper:

`scripts/deploy/phase14_v2_gate_b_readiness_cloudshell.sh`

The last accepted manual result remains `READY=false`, and Gate B remains blocked.
