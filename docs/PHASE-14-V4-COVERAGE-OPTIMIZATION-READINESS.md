# Phase 14 — optimized V4 forward coverage: read-only readiness and guarded successor rollout

**Status:** Engineering prepared, not deployed or newly authorized. Phase 14 **HOLD**
continues. Research-only, zero money, live trading disabled, no automatic model
promotion, no additional final-holdout access. Do not interpret GitHub merge or
this runbook as operator authorization.

## Evidence and fixed identities

- V4 bounded collector deployed successfully after separate approval on
  2026-10-09 from `e7a21462374a1c19beae878c98ca319f13fb2d69` into
  `/var/lib/bp/runtime/v4-forward-e7a21462374a1c19beae878c98ca319f13fb2d69`.
  The canonical recorder checkout remained
  `a352c66ec0110925727bc40de767ee4ba981f965`.
- First five scheduled cycles committed with zero observed failures. Cycle
  times averaged 83.03 seconds (maximum 88.34s); the reported pending count
  moved 392 to 388. This is initial, **not sustained**, backlog recovery.
- PR #567 merged optimized forward-only global invariant coverage into main
  as `d9f62ec719e27179f0c754afbdc32c416590cef0`. Original full audit
  remains unchanged. 2,416 tests passed on post-merge CI; the isolated
  20,000-row PostgreSQL benchmark measured 3.391s vs 0.928s median
  (3.66x); **not a production performance guarantee**.
- Historical scripts `phase14_v4_bounded_forward_rollout_*.sh` are preserved
  unchanged. Their old-runtime assumptions are **not** valid for this next
  deployment and they must not be reused.

## Step 1 — Fresh read-only host readiness (no rollout)

After this helper is merged and post-merge CI has passed, update the Mac's
local `main` to its **exact clean GitHub origin/main**, then run:

```bash
bash scripts/deploy/phase14_v4_coverage_optimization_readiness_cloudshell.sh \
  2>&1 | tee "$HOME/phase14_v4_coverage_optimization_readiness.log"
```

This helper:
- checks local clean/main/origin parity, one-market bound, optimized-summary
  source marker, and old baseline code SHA-256 calculated from the Git history;
- reads only the VM's production checkout, exact V4 symlink target and source
  bytes, systemd unit status, frozen core service health, safety settings,
  automatic-promotion flags and PostgreSQL read-only 128 MB buffer baseline;
- summarizes already-existing V4 journals for committed cycles, failures and
  pending-count direction; it does not invoke the writer or open a write-capable
  DB session;
- classifies every post-deploy scheduled V4 cycle using ordered stage markers.
  A paired 120-second systemd timeout **during** coverage reporting is
  counted as `COVERAGE_STAGE_TIMEOUTS`. A distinct, verified 2026-10-10
  failure pattern logs `committed elapsed_seconds=116.30–116.54` **after**
  the SQLAlchemy transaction has exited successfully, but the systemd
  oneshot fails before process exit. Only a paired timeout after a valid
  `coverage_complete` and `committed` stage with elapsed time in
  [110, 120) seconds qualifies as `POST_COMMIT_EXIT_TIMEOUTS`.
  Both counts remain visible and result in
  `OLD_V4_RUNTIME_HEALTH=DEGRADED_COVERAGE_AND_EXIT_TIMEOUTS`;
  neither classifies the old collector as healthy nor proves the process
  exited successfully. The other 130 failures in the supplied journal were
  paired timeouts within coverage reporting. Every other failure blocks.
  This classification permits **only a read-only evaluation of the exact
  coverage-performance remediation**, never general service-health approval.
- fails closed on any unpaired timeout, unexpected stage order, traceback,
  other service failure, increasing observed backlog, or no commits during
  the latest 30-minute window. The currently executing cycle may be
  incomplete; its recorded failure, if any, is not silently waived.

Its result is `PHASE14_V4_COVERAGE_OPTIMIZATION_READINESS=PASS` with
`PRODUCTION_MUTATION=false`, `DEPLOYMENT_EXECUTED=false`,
`ROLLOUT_AUTHORIZED=false`. A PASS alone does not permit production changes.

If the expected runtime changed, the old code hash differs, the checkout
changed, an **unclassified** failure occurred, no recent cycles committed, or
a frozen service is unhealthy: **STOP** and investigate read-only. Do not
retry an older rollout. The historical coverage-stage timeouts remain a
documented defect even when their classification passes. An optimized
deployment cannot be treated as a cure until independently measured.

## Step 2 — Local-only successor rollout preflight (no VM contact)

The successor controller is
`scripts/deploy/phase14_v4_coverage_optimization_rollout_cloudshell.sh`.
After a fresh readiness PASS, it can be inspected in default safe mode:

```bash
HEAD="$(git rev-parse HEAD)"
PHASE14_V4_COVERAGE_ROLLOUT_HEAD="$HEAD" \
  bash scripts/deploy/phase14_v4_coverage_optimization_rollout_cloudshell.sh
```

Preflight checks exact clean/local/origin main, requires the optimized summary,
calculates the expected **old** code hash from the recorded bounded-collector
Git commit, and prints a candidate/deployed/hash-bound approval string.
It exits before `gcloud`, archives/transfers, or production mutation.
Its default `PHASE14_V4_COVERAGE_ROLLOUT_PREFLIGHT_ONLY=true` is intentional.

**Do not set `PREFLIGHT_ONLY=false` or supply the approval token unless the
operator later explicitly authorizes this precise successor, with fresh host
readiness and fully green exact-main CI.** The 2026-10-09 approval covered only
the earlier one-market bounded rollout; it cannot authorize this optimization.

## Future separately authorized path (NOT executed)

The new controller and root half copy the audited narrow V4 rollout contract:
archive exact candidate, validate hashes and pinned recorder checkout, lock,
stop **only** the V4 timer, wait for the V4 one-shot to quiesce, atomically
switch **only** the V4 runtime symlink, run exactly one research-only bounded
cycle under a 110-second cutoff. If one market is eligible, verify four
planned offsets and 1–4 immutable new feature rows. **If the pending
backlog is zero**, require zero eligible targets, zero planned/existing/
inserted rows, zero remaining pending and exact equality of feature-row
counts before and after; still run the complete global leakage/regime/
no-promotion invariant scan. Neither branch is permitted to invent a
market or write test rows. Verify frozen service
and PostgreSQL identity continuity, then restore V4 timer and record evidence.

Failing any check after timer mutation triggers cautious rollback to the
**existing e7a21462 bounded runtime**, without deleting immutable feature
rows. Incomplete rollback leaves the V4 timer stopped wherever possible and
requires an operator; never repoint a runtime underneath an active writer.
Do not restart recorder, PostgreSQL, V3, or the old frozen paper shadow; do
not change money limits, trading mode, training, or policy.

A successful one-cycle validation is **not** proof of scheduled throughput.
If ever authorized, separately observe repeated scheduled commits, database
feature-hash integrity, pending market **age** and count, V3/recorder PIDs,
and persistent zero-money settings before reassessing Phase 14 HOLD.

## Current gate

- [x] Optimized source merged and post-merge CI green on d9f62ec7.
- [x] Isolated benchmark with equivalence assertion: 3.66x on synthetic rows.
- [ ] New readiness and rollout helpers reviewed, merged, and post-merge CI green.
- [ ] Fresh read-only host readiness PASS on expected current runtime; if coverage-only timeouts are present, they are explicitly classified as degraded, not ignored.
- [ ] New explicit operator authorization, bound to successor exact SHA.
- [ ] Authorized bounded deployment acceptance, if subsequently approved.
- [ ] Sustained production recovery and integrity evidence; HOLD remains.

No new production mutation is authorized by preparing these helpers.
