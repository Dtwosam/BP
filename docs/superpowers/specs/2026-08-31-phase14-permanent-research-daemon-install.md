# Phase 14 Permanent Research Daemon Install Design

**Date:** 2026-08-31
**Status:** Approved by user
**Scope:** Operational continuity only; no model promotion, live-gate change, or real-money authorization.

## Goal

Permanently establish `bp-live-predictor.service` and `bp-prospective-outcomes.service` on the production research host so new immutable prospective predictions continue to be produced and, after market resolution, acquire canonical Gamma snapshots, official labels, evaluations, and paper settlements.

## Safety boundary

The rollout is permitted only with all of the following true before and after installation:

- `MODE=research`
- `LIVE_TRADING_ENABLED=false`
- `MAX_TRADE_SIZE_USD=0`
- `MAX_DAILY_LOSS_USD=0`
- `bp-recorder.service`, `bp-postgres.service`, `bp-dashboard-api.service`, `bp-dashboard-web.service`, and `bp-paper-execution.service` active
- no wallet, signing, real-order submission, funding, promotion, or live-enable action
- Master live gate remains `fail`
- Phase 15 remains blocked

Both prospective units must independently enforce the same research/live-disabled/zero-money values. Because systemd `EnvironmentFile=` values override duplicate `Environment=` assignments, the units load `/etc/bp/bp.env` first and a separate root-controlled `/etc/bp/bp-prospective-runtime-safety.env` second. The installer creates that second file with exactly the four safety assignments and mode `0644`, and the units require it. This makes the safety file the effective systemd environment source for those duplicate keys; a later mutation of the general environment file cannot silently relax the prospective-daemon boundary.

The application-level research-only guards remain defense in depth. Host verification additionally inspects each running daemon's actual process environment and requires the effective values to be research/live-disabled/zero-money.

## Exact-head deployment

The Cloud Shell entrypoint requires an explicit 40-character candidate SHA and verifies that the configured feature branch resolves to exactly that SHA on the host. The candidate is materialized in a detached temporary worktree. The permanent installer executes from that verified candidate while `/opt/bp` still points to the currently deployed revision.

Before mutation, the installer records the existing `/opt/bp` HEAD/ref and validates deployed-checkout integrity. Production dashboard installation intentionally leaves generated runtime/build residue in the repository working tree, so integrity is not defined as an empty `git status`. The only tolerated tracked mutations are `apps/dashboard/next-env.d.ts` and `apps/dashboard/tsconfig.json`; the only tolerated untracked runtime paths are `.node/`, `apps/dashboard/.next/`, `apps/dashboard/node_modules/`, and `apps/dashboard/tsconfig.tsbuildinfo`. Every other tracked or untracked status entry fails closed as `unexpected_deployed_checkout_change`.

Before switching `/opt/bp`, the installer also proves that the exact candidate does not track any of those untracked runtime paths; a collision fails closed as `candidate_runtime_path_collision`. If either tolerated tracked Next-generated file is modified, its current bytes are backed up. The installer then checks out the verified candidate detached at `/opt/bp`; it does not move a local branch pointer. The established `/opt/bp/.venv` and untracked dashboard runtime roots are preserved. The virtualenv must successfully import the predictor and prospective-outcome modules from the new checkout. No migration or package installation is part of this slice.

## Service installation

The installer validates both candidate units before copying them into `/etc/systemd/system`. Required contracts include unprivileged `bp` execution, the ordered general-then-safety environment files, explicit research/live-disabled/zero-money declarations, systemd hardening, and the expected Python module entrypoints. Outbound network access remains available because both prospective runtimes require public market data.

After the root-controlled safety file and both units are installed, `daemon-reload` runs and both units are enabled and started. The installer waits for both to remain active, checks their actual `/proc/<pid>/environ` safety values, checks the five pre-existing core services are still active, verifies the dashboard still reports research mode with real execution unavailable, verifies `/opt/bp` remains at the exact candidate SHA, and revalidates that the checkout contains no status entries outside the narrow dashboard-generated allowlist.

## Rollback

Any failure after mutation arms rollback. Rollback stops the two prospective daemons, restores or removes their previous unit files and root-controlled safety file, restores the previous `/opt/bp` checkout/ref, restores the pre-install bytes of either tolerated tracked Next-generated file that had been modified, restores previous prospective-service enabled/active states, reloads systemd, and leaves the existing core services and untracked dashboard runtime roots untouched. Because this slice adds no migrations or dependency changes, no database or package rollback is required.

## Production-discovered pre-mutation failure

The first exact-head installation attempt using candidate `196519555bed8f68d37654bd171dac23f681fd52` failed before any mutation with `REASON=deployed_checkout_not_clean`. Read-only host inspection showed the established dashboard runtime/build residue described above, including `apps/dashboard/node_modules/` and `apps/dashboard/tsconfig.tsbuildinfo`. The failure therefore exposed an incorrect cleanliness assumption in the new installer rather than an unexpected application-source modification. The correction is test-driven: arbitrary dirt remains forbidden; only the previously established dashboard-generated paths are tolerated and preserved.

## Evidence

A successful installation writes a sanitized evidence file under `/var/lib/bp/evidence/` containing the old/new deployment heads, money-disabled settings, active/enabled states for both prospective daemons, the safety-environment path, and active status of recorder/PostgreSQL/dashboard/paper services. The Cloud Shell wrapper must emit a single `PHASE14_PROSPECTIVE_RUNTIME_INSTALL=PASS` marker only after these checks succeed.

Installation success is operational evidence only. It must not change profitability, calibration, sample-sufficiency, geographic-compliance, explicit-authorization, overall live-gate, or Phase 15 status.