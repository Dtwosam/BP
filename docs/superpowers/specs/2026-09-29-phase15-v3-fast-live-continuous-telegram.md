# Phase 15 — Continuous Telegram-approved fast-live V3

**Date:** 29 September 2026  
**Status:** engineering candidate; not activated or deployed by this change

## Objective

Run the frozen V3 live lane continuously during an explicitly authorized live session,
while requiring a fresh human Telegram approval for every exact real-money trade intent.

The service lifecycle is session-scoped. Network submission idempotency is intent-scoped.
A completed trade must not consume the entire live session.

## Continuous flow

For every new eligible frozen-V3 trade prediction:

1. The source derives the exact $5 request and persists the normal live risk decision and
   intent.
2. The exact prepared candidate is staged for the private Telegram approval listener.
3. An authenticated PREPARE message is published to Johannesburg immediately.
4. Telegram review, fresh account/risk state, book warming, and order pre-signing happen in
   parallel.
5. APPROVE creates an authenticated APPROVAL message bound to the exact intent, request
   hash, prepared hash, approval hash, and expiry.
6. Johannesburg durably claims that exact approval before making the execution decision.
7. The final gate requires the exact approval, fresh safety state, an unengaged kill
   switch, and fresh executable ask liquidity at or below the frozen V3 limit.
8. The executor creates an atomic attempt marker scoped to the exact
   `intent_id + request_sha256`, then POSTs the already-signed limit order.
9. That intent can never POST again. Pub/Sub redelivery or receiver restart replays the
   durable decision/result instead of reconsidering the market.
10. The authenticated result is recorded and reconciled.
11. If the trade is rejected, zero-filled, skipped, expired, or otherwise closes without
    exposure, the source immediately returns to monitoring for the next V3 prediction.
12. If a confirmed fill creates exposure, the source remains settlement-blocked until the
    official market outcome clears that exposure, then resumes monitoring.

## Authorization model

Continuous live mode is explicit and separate from the legacy one-shot mode.

Source truth must carry:

- `status = AUTHORIZED_CONTINUOUS_SESSION`;
- `authorized = true`;
- `authorization_mode = manual-telegram-continuous-v1`;
- `requires_telegram_approval = true`;
- `max_network_submission_attempts_per_intent = 1`;
- the existing $5 target and $10 exposure/loss ceilings;
- the frozen V3 prediction/execution versions;
- executor country `ZA`;
- an explicit session expiry.

The runtime authorization repeats the continuous-session mode and exact per-intent attempt
limit and remains bound to the exact release main SHA and source-truth hash.

The activation helper requires the explicit value:

`PHASE15_ACCEPT_FAST_LIVE_ACTIVATION=I_ACCEPT_CONTINUOUS_TELEGRAM_APPROVED_LIVE_SESSION`

This code does not create that production authorization or activate the session by itself.

## Idempotency and replay

The old global `attempt.json` remains the legacy one-shot path.

Continuous mode stores per-intent attempt and result files under a deterministic hash of
the exact intent and request. This gives each approved trade one network-attempt budget
without preventing later independent intents.

A second durable approval-decision record is keyed by the exact intent, request hash, and
human approval hash. One Telegram approval therefore produces at most one execution
decision even if Pub/Sub redelivers the APPROVAL message after the market changes.

A crash after claiming an approval but before a durable non-attempt result fails closed as
`approval_recovery_blocked`. A crash after the network-attempt marker uses the existing
submission-unknown/result replay path and never POSTs again.

The source also binds its current result wait to the exact intent and request hash. Results
from old intents may still be recorded, but cannot satisfy or clear the wait for a newer
trade.

## Risk behavior

Continuous mode removes the canary-only 24-hour cooldown. It does **not** remove the
substantive live risk gates:

- target notional: $5;
- max trade size: $10;
- max total exposure: $10;
- max daily loss: $10;
- max consecutive losses: 1;
- min edge: 0.075;
- max spread: 0.10;
- minimum selected liquidity: $5;
- prediction freshness and minimum time-to-expiry checks;
- fresh Johannesburg safety/account checks;
- zero official open orders before execution;
- unresolved critical reconciliation blocks;
- confirmed live exposure blocks until official settlement;
- the frozen limit price is never raised to chase the book.

A losing settled trade still trips the existing one-loss stop. Continuous means the
service keeps running and evaluating safely; it does not bypass risk stops.

## Kill switch and service lifecycle

The kill switch is a session-level emergency/fault stop, not a normal per-trade latch.

A successful or rejected ordinary trade does not re-engage it in continuous mode.
Johannesburg still checks it before quoting and again immediately before the atomic attempt
marker and POST.

Stopping or failing the Johannesburg service still re-engages the kill switch through
systemd `ExecStopPost`. This intentionally prevents automatic real-money resumption after
an executor process fault without a fresh operator re-arm.

The source and receiver remain active across completed trades until the runtime session
expires, an operator stops them, the kill switch blocks execution, or another fail-closed
condition requires intervention.

## Secret separation

The source and Telegram listener never receive the Polymarket private key. Johannesburg
never receives the Telegram bot secret. The dedicated fast-live HMAC transport key remains
separate from Telegram transport material.

## Production boundary

Merging or staging this candidate does not activate real-money trading.

Production activation remains an explicit operation requiring continuous-session source
truth, matching runtime authorization, exact deployed release hashes, clean account/open
order checks, Johannesburg geoblock eligibility, transport setup, and explicit session
acceptance.
