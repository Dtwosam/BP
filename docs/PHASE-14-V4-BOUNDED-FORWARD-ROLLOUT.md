# Phase 14: bounded V4 forward collector rollout (not authorized)

**Current disposition:** Phase 14 V4 shadow HOLD. Research-only. Live trading disabled; no model promotion. This is a proposed, guarded, production-mutating rollout procedure. Merely merging the helper does **not** authorize running it.

## Incident and accepted read-only evidence

- Post-V4-epoch forward backlog measured 2026-10-09: 327 pending markets, 315 overdue >1 hour, 255 >6 hours, 39 >24 hours. Last generation 2026-10-08 14:56:23 UTC; no rows generated in the prior hour.
- Repeated 2-minute V4 service timeouts; original cycle used one transaction for all pending markets and a full-history coverage report. Exact remaining bottleneck unproven.
- Bounded code merged in #562: one oldest pending market (four fixed offsets) per successful transaction, with journal phase markers; **not yet deployed**.
- Read-only readiness command completed successfully after #563–#565 compatibility fixes; active recorder, PostgreSQL, V3 frozen predictor and V3 paper execution; V4 timer active/enabled; research-zero-money and auto-promotion-disabled; PostgreSQL shared_buffers=128MB.
- Production checkout **a352c66ec0110925727bc40de767ee4ba981f965** must remain unchanged.
- Existing V4 symlink target **/var/lib/bp/runtime/v4-forward-36b02d0687194173ab5d3862d3b88c6c90607574**; existing V4 forward source SHA-256 **e8882286a4fb92969d0e51ab75b81908deae1000fe26806a9491e75c18e4f846**. The /var/lib/bp parent is bp:bp 0750: SSH user cannot traverse it; use root for protected metadata operations.

## CI prerequisite

Every release commit must have passing CI. A 2026-10-09 post-merge main test job and its retry failed during GitHub Actions **Initialize containers** because unauthenticated pulls of Docker Hub postgres:16 hit a rate limit. Tests did not execute in those runs, despite the corresponding PR's green pre-merge checks. Resolve/clear that external CI gate before executing the rollout; don't assume the failed job is a passing test.

## Guarded controller (Mac / Cloud Shell)

Run only from a clean, current local main with the exact helper SHA (after merge). The controller is intentionally **preflight-only by default**, with no VM access:

    PHASE14_V4_BOUNDED_ROLLOUT_HEAD=<exact_current_main_sha> \
      bash scripts/deploy/phase14_v4_bounded_forward_rollout_cloudshell.sh

It prints a candidate-bound approval string plus explicit PRODUCTION_HOST_CONTACTED=false and PRODUCTION_MUTATION=false. **Do not set PREFLIGHT_ONLY=false or provide the approval string without new, explicit operator authorization to mutate only the V4 forward runtime, timer and at most one committed feature batch.** Do not infer approval from an ordinary "continue."

The approved path requires the exact operator-supplied environment variables PHASE14_V4_BOUNDED_ROLLOUT_PREFLIGHT_ONLY=false and PHASE14_V4_BOUNDED_ROLLOUT_APPROVAL=<exact_preflight_token>; there is no stored approval. An unchanged clean local main and exact GitHub origin/main SHA are required.

## Mutating path after separate authorization

1. Locally create an archive of the exact Git commit; hash both the archive and host script. Transfer into a short-lived /tmp directory on the VM without updating the /opt/bp Git checkout.
2. Host-side fail-closed checks: approval bound to candidate SHA, pinned recorder checkout and old runtime code hash; verify transferred payload hashes, V4 service and timer state/identity, all frozen core services, research-zero-money settings, recorder batch100/workers4/priority20, PostgreSQL 128MB.
3. Stage an immutable versioned runtime archive with a one-market limit and stage logging. Acquire an exclusive rollout lock.
4. **Stop only the V4 timer.** Let the existing activating V4 one-shot exit naturally; abort if not quiesced after 150 seconds. Record the before-row count only after it is idle.
5. Atomically repoint only v4-forward-current to the staged version. With the timer still stopped, execute exactly one direct V4 forward cycle under a 110-second timeout, shorter than the existing unit's 120-second limit.
6. Require one eligible target, all four planned offsets, 1–4 *new* rows, an exact feature-row delta equal to inserted, zero cutoff/regime/Polymarket predictor violations, and policy_selected/training_run/automatic_promotion all false.
7. Verify unchanged V4 unit-file and timer-file hashes, unchanged recorder checkout, V3 and recorder PID continuity, PostgreSQL container continuity, and core service health. Reactivate the originally active V4 timer and require it to remain enabled.
8. Retain a private production evidence directory with structured cycle JSON, phase timings, row counts, source/target runtime identities and PASS result.

**Rollback:** For an error after timer mutation, stop the V4 timer and wait for any V4 one-shot or directly launched forward Python writer to become inactive before changing the runtime. Then atomically restore the previous symlink and the timer's originally active state. If timer stop, quiescence, or symlink restoration fails, leave the timer stopped whenever possible, report INCOMPLETE_OPERATOR_ACTION_REQUIRED, and do not change code underneath a running writer. Do not delete immutable market_features rows on rollback. Staged artifacts/evidence can remain for forensic review. A failed validation cycle can have committed some immutable rows before a later gate fails; restoration of the runtime does not undo those rows. Never automatically lift Phase 14 HOLD after a PASS. Review later journal stage timings and forward backlog measurements separately.

## Additional gates before execution

- Exact rollback helper and source scripts reviewed, tested and merged on main.
- Full CI green, including test job (not skipped or blocked by Docker Hub).
- Fresh read-only production readiness verification, with exact checkout/runtime and 128MB baseline.
- Operator explicitly authorizes V4-only timer/symlink mutation and one research-only database feature-generation cycle, bound to exact SHA. No recorder/PostgreSQL restart, DDL, cache A/B, promotion or live trading.
- After successful rollout, confirm later scheduled cycles commit, backlog declines, old feature fingerprints remain unchanged, and recorder/V3 health remains green. One cycle succeeding does not by itself prove sustained recovery.
