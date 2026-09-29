# Phase 15 — Continuous Telegram-approved fast-live V3

**Date:** 29 September 2026  
**Status:** engineering candidate; not activated or deployed by this change

## Objective

Run the frozen V3 live lane continuously during an explicitly authorized live
session while requiring a fresh human Telegram approval for every exact
real-money trade intent.

The live service is session-scoped. Network-submission idempotency is
intent-scoped. A normal completed trade does not consume the whole session.

## Continuous parallel flow

For every new eligible frozen-V3 prediction:

1. The source derives a risk-pending preview containing the exact frozen $5
   request. Request economics are already immutable; no live-risk pass is
   assumed yet.
2. The preview is staged for the private Telegram listener and an authenticated
   PREPARE message is sent to Johannesburg immediately.
3. Three paths progress in parallel:
   - the human reviews the exact request in Telegram;
   - the US source runs full live risk and persists the final live intent;
   - Johannesburg refreshes safety/book state and pre-signs the exact preview.
4. If final live risk fails, the preview is cancelled, the Telegram prompt is
   invalidated, and no executable APPROVAL message is produced.
5. If final live risk passes, the final intent must preserve the exact
   prediction, paper order, and request hash from the preview. Any drift fails
   closed.
6. Human APPROVE is bound to the preview candidate and exact request. The source
   creates an authenticated APPROVAL carrying the finalized live intent plus
   the preview candidate/hash binding.
7. Johannesburg joins the finalized risk-approved intent to the pre-signed
   preview order only when the exact prediction/request identity matches.
8. Johannesburg durably claims that exact approval before making the execution
   decision.
9. The final join gate requires the exact human approval, finalized risk pass,
   fresh safety/account state, unengaged kill switch, and fresh executable ask
   liquidity at or below the frozen limit.
10. The executor atomically creates a per-intent attempt marker immediately
    before POST.
11. That finalized intent can never POST again. Redelivery/restart replays or
    recovers the durable state instead of reconsidering the market.
12. The authenticated result is recorded and reconciled.
13. Rejected, skipped, expired, zero-fill, fresh-book-rejected, or other
    no-exposure outcomes return the source to V3 monitoring.
14. Confirmed exposure blocks new trades until official settlement clears it,
    after which monitoring resumes.

## Authorization model

Continuous mode is explicit and separate from the legacy one-shot mode.

Source truth must carry:

- `status = AUTHORIZED_CONTINUOUS_SESSION`;
- `authorized = true`;
- `authorization_mode = manual-telegram-continuous-v1`;
- `requires_telegram_approval = true`;
- `max_network_submission_attempts_per_intent = 1`;
- target notional $5;
- max trade size $10;
- max total exposure $10;
- max daily loss $10;
- max consecutive losses 1;
- min edge 0.075;
- the frozen V3 prediction/execution versions;
- executor country `ZA`;
- an explicit session expiry.

Runtime authorization repeats the continuous-session constraints and is bound to
the exact release main SHA and source-truth hash.

Activation requires:

`PHASE15_ACCEPT_FAST_LIVE_ACTIVATION=I_ACCEPT_CONTINUOUS_TELEGRAM_APPROVED_LIVE_SESSION`

This code does not create production source-truth authorization and does not
activate the session by itself.

## Per-intent idempotency

Legacy one-shot mode retains global `attempt.json` / `result.json`.

Continuous mode stores attempt/result state under a deterministic hash of the
exact `intent_id + request_sha256`. Each approved intent therefore gets at
most one network POST while later independent intents can trade in the same
session.

The accepted-order preliminary record and final record are both kept in the
same per-intent result path; continuous mode does not create the legacy global
result file.

A second durable approval-decision record is keyed by final intent, exact
request hash, and human approval hash. One Telegram approval therefore produces
at most one execution decision even under Pub/Sub redelivery.

A crash after approval claim but before a durable non-attempt result fails
closed as `approval_recovery_blocked`. A pre-attempt Johannesburg safety
failure becomes a durable `pre_submission_blocked` result so the source can
close that intent instead of hanging.

## Result replay and reconciliation

The source binds its active wait to the exact `intent_id + request_sha256`.
An old result cannot advance a newer intent.

Result ingestion is receipt-idempotent. Once a result has a durable source
receipt, duplicate/re-published copies reuse the already-recorded ledger and
reconciliation outcome and are ACKed without a second ledger mutation.

Each publication receipt stores `published_at` and a per-intent result-wait
threshold. The operational stall threshold is 20 seconds. Crossing it does
**not** mark the trade safe or abandon it. The source enters
`fast_live_result_reconciliation_stalled` and remains reconciliation-only
until a valid bound result arrives.

Authenticated result messages remain valid for five minutes. This is
deliberately longer than the operational stall threshold: delayed or replayed
Pub/Sub delivery can repair the exact stalled intent, but the source cannot
advance to another trade while that intent is unresolved.

If the session expires while an approved result is unresolved, the source does
not publish another trade. It continues result-only recovery under the same
frozen authorization identity. If restarted after expiry, it validates that
same authorization at its last valid instant and resumes only the already-bound
result reconciliation.

Confirmed fills remain settlement-only work even after session expiry. Official
settlement may therefore finish after trading authority has ended without
extending authority to place another order.

Successful settlement writes a durable marker using the same deterministic
receipt basename under `published/settlements/`. Restart therefore
distinguishes a historical settled fill from unresolved live exposure without
repeating settlement work. A new session is refused if an earlier publication
has no result or if a result still requires settlement without that marker.

## Johannesburg crash recovery

Stopping/failing the Johannesburg service engages the kill switch through
systemd `ExecStopPost`. The process may restart, but cannot silently resume
new real-money submissions.

If a crash happens after POST succeeds but before TTL cancellation/probing is
complete, the per-intent result is durably marked
`cancellation_pending=true`. Recovery performs only the missing cancellation
and official probe; it never signs or POSTs another order.

Startup also scans for stranded cancellation/recovery results before admitting
new callbacks. Recovered final results are marked
`recovery_result_publish_pending=true` until Pub/Sub publication succeeds.
A restart may republish the same final result, but will not cancel or POST a
second time.

If the runtime authorization has already expired, Johannesburg runs this
cancel/result recovery only, publishes under the original authorization
identity, and exits without starting the subscriber, book cache, or new-trade
path.

Activation of a new session is blocked while any prior per-intent state still
contains `cancellation_pending=true` or
`recovery_result_publish_pending=true`.

## Expiry and callback drain

Session expiry stops admission of new Johannesburg callbacks. Any callback that
was already admitted is allowed a bounded drain window so a valid in-flight
order/result is not interrupted by teardown.

The source similarly closes a finalized-but-not-approved intent as
`telegram_expired` when session authorization ends before APPROVAL
publication.

After APPROVAL publication, expiry changes the system to reconciliation-only;
it does not retroactively abandon the already-authorized intent.

## Telegram session isolation

Fast-live Telegram preview state is bound to the exact live authorization ID.
A new session cannot reuse a prior session's `current-run`.

Activation refuses to start if stale fast-live Telegram preview state exists.
The exact Telegram listener code/unit is included in the fast-live release and
staged as a dormant sidecar release.

Activation switches the private Telegram listener to the exact release before
Johannesburg is unarmed. If later activation steps fail, rollback restores the
previous Telegram sidecar release/unit and its previous active/stopped state.

## Risk behavior

Continuous mode removes only the canary-only 24-hour cooldown. It preserves:

- target notional: $5;
- max trade size: $10;
- max total exposure: $10;
- max daily loss: $10;
- max consecutive losses: 1;
- min edge: 0.075;
- max spread: 0.10;
- minimum selected liquidity: $5;
- prediction freshness and time-to-expiry checks;
- fresh Johannesburg geoblock/account/open-order/collateral checks;
- unresolved critical reconciliation blocks;
- confirmed exposure blocks;
- frozen limit price; the executor never chases the book upward.

A losing settled trade increments the consecutive-loss counter and the existing
one-loss stop blocks later submissions. A winning trade resets that counter.

Normal no-trade states do not crash/restart the service. Preview selection skips
signals that no longer have the minimum 30-second preparation arm window.
Frozen-paper terminal draft conditions are surfaced as blocked live candidates,
and persistent blocked states are throttled rather than polled/logged at the
normal fast cadence.

`realized_daily_pnl_usd` resets at the UTC day boundary. Exposure and
consecutive-loss state do not reset with the day.

## Kill switch and service lifecycle

The kill switch is a session/fault emergency stop, not a normal per-trade
latch.

Normal accepted/rejected/no-attempt outcomes do not engage it in continuous
mode. Johannesburg checks it before quote and immediately before the atomic
attempt marker/POST.

A Johannesburg process stop/failure engages the kill switch. Recovery work may
cancel/probe/publish already-posted state, but no new order may be submitted
until a separately authorized operator re-arm.

## Secret separation

The source and Telegram listener never receive the Polymarket private key.
Johannesburg never receives the Telegram bot secret. The fast-live HMAC key is
separate from Telegram transport material.

## Release, staging, and production boundary

The deterministic fast-live release includes the source, receiver, Telegram
listener/unit, and required `bp_engine` source but contains no project state,
runtime authorization, wallet secret, Telegram secret, or transport key.

Staging installs exact release bytes and the dormant Telegram sidecar only. It
does not start/enable fast-live services, unarm Johannesburg, create runtime
authorization, or submit an order.

Before activation, run the read-only readiness helper:

`scripts/deploy/phase15_v3_fast_live_preflight_cloudshell.sh`

It requires a clean checkout at current `main` and verifies, without creating
or modifying production resources:

- valid continuous-session source truth;
- exact staged source, receiver, and Telegram sidecar release;
- fast-live services inactive/disabled before activation;
- Telegram private listener configuration present with privileged handoff
  absent;
- no stale Telegram preview;
- every prior source publication has a result;
- every result requiring settlement has its durable settlement marker;
- no pending Johannesburg cancellation/result-publication recovery;
- Johannesburg kill switch engaged;
- ZA geography, clean official account state, zero open orders, and at least $5
  collateral.

The preflight explicitly reports that it creates no runtime authorization,
Pub/Sub resource/IAM mutation, service start, kill-switch removal, or real
order.

Production activation remains a separate explicit operation requiring:
continuous-session source truth, matching runtime authorization, exact staged
release hashes, clean official account/open-order state, Johannesburg geography,
no stale Telegram preview, no pending execution recovery, transport setup, and
the explicit continuous-session acceptance value above.

Recommended deployment sequence is therefore:

1. merge the reviewed code to current `main`;
2. build the deterministic fast-live release;
3. stage the release on both hosts;
4. run the read-only preflight and require PASS;
5. only with separate explicit authorization, run the activation helper.

Merging, building, staging, or preflighting this candidate does not activate
real-money trading.
