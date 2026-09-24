# Phase 15 — V3 Telegram Approval v1

**Date:** 24 September 2026  
**Status:** engineering candidate; listener and handoff code only; not deployed; no new live authorization

## Current source-truth boundary

The first frozen-V3 real-money canary has already consumed the single authorized network
submission attempt and has been reconciled as zero fill. Current source truth keeps global live
trading disabled and does not authorize a second order.

This Telegram work does not change that boundary. In particular, it does not set or imply:

- `second_order_authorized = true`;
- `automated_real_money_submission = true`;
- `telegram_one_tap_submission_authorized = true`;
- `telegram_persistent_execution_transport_authorized = true`.

Missing Telegram authorization fields are interpreted as false by the readiness gate.

## Objective

Replace terminal/chat latency in a future separately authorized live action with one exact,
short-lived Telegram approval:

```text
new frozen-V3 candidate
-> existing prepare/risk path
-> private Telegram notification
-> APPROVE or SKIP
-> exact intent/request binding revalidated
-> separately authorized persistent execution transport
-> existing arm/executor/record semantics
```

The approval must never approve a later candidate accidentally.

## Approval binding

Every pending Telegram approval is bound to:

- `intent_id`;
- `prediction_id`;
- `paper_order_id`;
- SHA-256 of the exact prepared request;
- one random callback nonce;
- one Telegram user ID;
- one private Telegram chat ID;
- a short expiry no later than the market safety floor.

Wrong user, wrong chat, wrong nonce, request mutation, identity mutation, expiry, SKIP, stale
prepared state, or a second/replayed attempt fails closed.

## Persistent listener

The listener is designed to run on `bp-recorder` as
`bp-phase15-canary-telegram-approval.service`.

The service is explicitly research/zero-money:

```text
MODE=research
LIVE_TRADING_ENABLED=false
MAX_TRADE_SIZE_USD=0
MAX_DAILY_LOSS_USD=0
BP_TELEGRAM_HANDOFF_ENABLED=no
```

It unsets Polymarket key/wallet variables, reads the prepare-watch state read-only, and writes
only its own approval state.

The listener installer is:

```text
scripts/deploy/phase15_v3_telegram_approval_install_cloudshell.sh
```

It requires explicit install authorization through
`PHASE15_ACCEPT_TELEGRAM_APPROVAL_INSTALL=yes`, a clean checkout exactly at current
`origin/main`, and an authenticated Cloud Shell session.

The bot token is entered at a hidden `/dev/tty` prompt. It is never committed to Git and is
copied to `/etc/bp/telegram-approval.env` as `root:bp 0640`. The installer validates the bot
and private chat through Telegram before upload, installs no handoff command, preserves core
service PIDs, and rolls back the unit/env/current symlink on install failure.

The read-only status verifier is:

```text
scripts/deploy/phase15_v3_telegram_approval_status_cloudshell.sh
```

It reports service/binding/permission health without printing the bot token or Telegram IDs.

## Handoff state machine

An approved intent is revalidated immediately before handoff and copied to an immutable
per-intent `handoff-prepared.json` snapshot.

Before any configured handoff command starts, the listener persists `handoff-attempt.json`.
After that marker exists, a missing result, crash, timeout, restart, or ambiguous outcome is
terminal and must never be retried automatically.

The child handoff environment removes:

- `BP_TELEGRAM_BOT_TOKEN`;
- `POLYMARKET_PRIVATE_KEY`;
- `POLYMARKET_WALLET_ADDRESS`.

An approval received while handoff is unconfigured or invalid is also terminal. Turning on
handoff later cannot resurrect an old approval.

## Existing arm/submission bridge

`scripts/deploy/phase15_v3_canary_telegram_handoff.sh` binds the approved snapshot to the
existing Phase 15 arm, Johannesburg executor, and record semantics.

The bridge additionally requires a future source-truth flag:

```text
telegram_one_tap_submission_authorized = true
```

Current source truth does not contain that authorization, so the bridge fails before arming.

The bridge is intentionally not configured in the listener service or installer.

## Persistent execution transport is still a separate gate

Existing BP live control uses authenticated Cloud Shell `gcloud compute ssh` commands. The
repository does not currently define an approved persistent control channel from
`bp-recorder` to `bp-v3-canary-exec`.

The listener therefore must not silently depend on Cloud Shell credentials, user home files,
or an undeclared host-to-host tunnel.

Before one-tap execution can be enabled, a separate persistent transport design must be
reviewed and explicitly authorized. The readiness gate treats

```text
telegram_persistent_execution_transport_authorized
```

as false unless source truth explicitly sets it true.

The read-only readiness helper is:

```text
scripts/deploy/phase15_v3_telegram_activation_readiness_cloudshell.sh
```

It checks source-truth authorization, listener health, and the Johannesburg executor's safe
idle state. It performs no arm, kill-switch removal, order submission, cancellation, or state
mutation.

## Safety invariants

- no second order without new explicit source-truth authorization;
- no bot token, wallet key, or wallet address in Git or chat;
- private Telegram chat only;
- exact intent/request binding only;
- approval expiry enforced at callback and handoff;
- no approval reuse after disabled/invalid handoff;
- attempt marker before any handoff command;
- no automatic retry after attempted/ambiguous handoff;
- listener remains research/zero-money even when persistent;
- no Polymarket signing material on `bp-recorder`;
- no VPN/proxy/tunnel geographic circumvention;
- Johannesburg executor retains its independent geoblock/account/activation/request checks;
- current deployment configuration contains no execution handoff.

## Production status

No Telegram listener, handoff command, transport, source-truth authorization, or new
real-money order has been deployed by this engineering branch.
