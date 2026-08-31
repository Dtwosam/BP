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

The predictor and outcome-sync unit files must independently pin the same research/live-disabled/zero-money values so a mutable environment file cannot silently relax the daemon boundary.

## Exact-head deployment

The Cloud Shell entrypoint requires an explicit 40-character candidate SHA and verifies that the configured feature branch resolves to exactly that SHA on the host. The candidate is materialized in a detached temporary worktree. The permanent installer executes from that verified candidate while `/opt/bp` still points to the currently deployed revision.

Before mutation, the installer records the existing `/opt/bp` HEAD/ref, requires a clean checkout, records whether the predictor/outcome unit files already exist, and records their active/enabled states. The installer then checks out the verified candidate detached at `/opt/bp`; it does not move a local branch pointer. The established `/opt/bp/.venv` is reused and must successfully import the predictor and prospective-outcome modules from the new checkout. No migration or package installation is part of this slice.

## Service installation

The installer validates both candidate units before copying them into `/etc/systemd/system`. Required contracts include unprivileged `bp` execution, `/etc/bp/bp.env`, explicit research/live-disabled/zero-money overrides, systemd hardening, and the expected Python module entrypoints. Outbound network access remains available because both prospective runtimes require public market data.

After `daemon-reload`, both units are enabled and started. The installer waits for both to remain active, checks the five pre-existing core services are still active, verifies the dashboard still reports research mode with real execution unavailable, and verifies `/opt/bp` remains at the exact candidate SHA.

## Rollback

Any failure after mutation arms rollback. Rollback stops the two prospective daemons, restores or removes their previous unit files, restores previous enabled/active states, restores the previous `/opt/bp` checkout/ref, reloads systemd, and leaves the existing core services untouched. Because this slice adds no migrations or dependency changes, no database or package rollback is required.

## Evidence

A successful installation writes a sanitized evidence file under `/var/lib/bp/evidence/` containing the old/new deployment heads, money-disabled settings, active/enabled states for both prospective daemons, and active status of recorder/PostgreSQL/dashboard/paper services. The Cloud Shell wrapper must emit a single `PHASE14_PROSPECTIVE_RUNTIME_INSTALL=PASS` marker only after these checks succeed.

Installation success is operational evidence only. It must not change profitability, calibration, sample-sufficiency, geographic-compliance, explicit-authorization, overall live-gate, or Phase 15 status.