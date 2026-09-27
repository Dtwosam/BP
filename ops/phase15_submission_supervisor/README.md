# Phase 15 controlled canary submission supervisor

This operator-side macOS supervisor keeps the existing one-canary workflow moving until
one real $5 second-canary submission is clearly recorded by the Johannesburg privileged
consumer.

It does **not** submit an order itself. It has no wallet key, bot token, or Polymarket API
path. It coordinates existing guarded components through the operator's authenticated
`gcloud` CLI:

1. Observe the prepare-only watcher on `bp-recorder`.
2. Let the existing Telegram listener and operator auto-approver handle the exact fresh prompt.
3. Read the Johannesburg global attempt marker and terminal privileged-consumer receipt.
4. Stop successfully only when the receipt is bound to the prepared intent and reports:
   - `network_submission_attempt_consumed=true`
   - `authorization_slot_consumed=true`
   - `accepted=true`
   - `real_order_submitted=true`
5. If the candidate expires and **no** network-attempt marker exists, run the dedicated
   fail-closed `closed_before_submission` reconciliation helper and start another
   prepare-only watcher.
6. If a network attempt is consumed without that clear success, or its result becomes
   ambiguous, halt. There is no retry after a network attempt.

The supervisor is intentionally limited to the existing second-canary authorization:
target notional $5, maximum one network submission attempt, and all existing request,
source-truth, geoblock, account, TTL, kill-switch, and executor checks remain mandatory.

## Runtime assumptions

- The existing `com.bp.telegram-auto-approver` LaunchAgent is running in exact
  `BP_TELEGRAM_AUTO_APPROVE=true` mode.
- The Mac has an authenticated `gcloud` CLI capable of reaching `bp-recorder` and
  `bp-v3-canary-exec`.
- The supervisor uses a dedicated clean Git checkout. Before every mutation it fetches
  current `origin/main`, detaches to that exact head, and verifies all source-truth-bound
  helper/runtime Git blobs.
- It never changes global live-trading enablement.
- It never authorizes a third order.

## Terminal behavior

A local `terminal.json` is written when the controlled canary succeeds or when the
supervisor halts fail-closed. Once that file exists, a restarted supervisor exits without
performing more mutations. Manual review is required before deleting a fail-closed terminal
record.

The safe pre-network reconciliation helper is:

`scripts/deploy/phase15_v3_controlled_canary_reconcile_unsubmitted_cloudshell.sh`

It refuses to reconcile until at least 20 seconds after the prepared market end and verifies
all of the following again immediately around the database mutation:

- exact current-main/source-truth bindings;
- $5 prepared request and expected intent;
- Johannesburg executor safe idle;
- no official open orders;
- no global second-canary network-attempt marker;
- no live-order submission event already stored in PostgreSQL.

The reconciliation writes only the existing `closed_before_submission` terminal event and
cannot consume the second-canary network-attempt slot.
