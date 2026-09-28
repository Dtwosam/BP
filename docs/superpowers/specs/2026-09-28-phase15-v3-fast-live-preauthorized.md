# Phase 15 — Pre-authorized fast-live V3 lane

**Date:** 28 September 2026  
**Status:** engineering candidate; not authorized or deployed for another real-money order

## Objective

Reduce preventable live-order staleness by moving human approval and slow setup work out
of the post-signal critical path while preserving the frozen V3 model, the $5 target, the
$10 monetary ceilings, the one-loss stop, and the existing live-risk evaluation.

The performance target is not a guaranteed fill. The target is to make the system's own
delay small enough that a fresh executable quote is followed by submission in milliseconds
or low hundreds of milliseconds, then measure the actual result.

## Architecture

The fast lane is separate from the historical Telegram/canary artifacts so their reviewed
Git blob hashes remain unchanged.

US recorder host:

1. A one-shot source-truth authorization and matching root-owned runtime authorization must
   already exist.
2. A dedicated preparer watches only new frozen-V3 paper orders after authorization.
3. The preparer runs the existing V3 live-risk policy and persists the exact risk decision
   and live intent.
4. If eligible, the source creates an HMAC-bound envelope containing the exact request,
   risk-decision ID, intent, prediction, paper order, authorization ID, and a two-second
   transit expiry.
5. A persistent Pub/Sub publisher sends that envelope immediately on a dedicated fast-live
   topic.

Johannesburg execution host:

1. A persistent StreamingPull receiver and two warm Polymarket clients are started before
   the signal: one for background safety/account refresh, one for execution.
2. The receiver independently validates source truth, runtime authorization, HMAC, exact
   request bindings, two-second transit age, fresh safety state, country ZA, zero open
   orders, and collateral.
3. The exact frozen limit order is signed **before** the final order-book query. Signing
   does not submit or consume the one-shot authorization.
4. The receiver obtains the latest Polymarket book immediately before submission.
5. If any ask liquidity exists at or below the frozen V3 limit, the receiver atomically
   creates the one-shot network-attempt marker, immediately re-engages the kill switch,
   and posts the already-signed crossing limit order.
6. The full requested size is submitted. Visible depth need not cover the full order:
   immediately executable shares may fill at once and the remainder retains the existing
   two-second resting/cancel window. The limit price is never raised.
7. A stale book with no executable ask at or below the V3 limit does not consume the
   network-attempt marker.
8. Submission ambiguity never retries after the attempt marker exists.

## Critical-path design

Slow or reusable work is removed from the post-quote window:

- Telegram and the Mac auto-approver are absent from the critical path.
- Google Pub/Sub clients stay connected.
- Polymarket clients stay initialized.
- geoblock/account/open-order/collateral checks refresh in the background and must be
  fresh at execution.
- the order is pre-signed before the final book read.
- the final sequence is therefore approximately:

  `fresh book -> one-shot marker -> HTTP order POST`

Instrumentation records source-to-receive, quote, sign, post, and quote-to-post latency.

## Price and fill semantics

The implementation deliberately retains the frozen crossing limit price rather than
chasing the book. It does not switch to a protected market FAK/FOK order because the
pinned `polymarket-client==0.7.1` has a documented protected BUY rounding problem at
some price/amount combinations that can itself create non-fills.

Partial fills are acceptable. Existing official fill probing already measures confirmed
shares, confirmed notional, and fill fraction rather than assuming all-or-nothing fills.

## Authorization model

The source truth must later contain a new `fast_live_preauthorization` object. The
current repository intentionally does not contain that object, and tests require the
current state to fail closed.

A root-owned runtime authorization additionally binds:

- the authorization ID;
- exact merged release main SHA;
- exact `PROJECT_STATE.json` SHA-256;
- $5 target;
- one network submission attempt;
- authorization issue/expiry times.

The execution host also has a separate `/etc/bp-fast-live/KILL` switch. A network
attempt is consumed before the POST and re-engages that switch immediately, so duplicate
delivery or process restart cannot produce a second order.

## Preserved V3 policy

This candidate does not change the frozen model or the existing live-risk policy:

- min edge: 0.075;
- target notional: $5;
- max trade: $10;
- max total exposure: $10;
- max daily loss: $10;
- max consecutive losses: 1;
- max spread: 0.10;
- minimum selected liquidity: $5 at risk-decision time;
- max prediction age: 30 seconds;
- minimum time to expiry: 15 seconds;
- cooldown: 86400 seconds;
- resting/cancel TTL after submission: 2 seconds.

The 24-hour cooldown is therefore still a deliberate policy constraint; this speed work
does not silently loosen trade frequency.

## Production boundary

This engineering candidate does not create the source-truth authorization, runtime
authorization, Pub/Sub resources, or remove the fast-live kill switch. It must be staged
and latency-tested first. A real-money canary remains a separate explicit authorization
and reconciliation boundary.
