# Phase 15 — V3 Continuous Live Loss Policy v2

**Date:** 1 October 2026  
**Status:** repository engineering only; not deployed; fresh production authorization required

## Problem

The production continuous-live v1 contract inherited the canary limit
`max_consecutive_losses = 1`. After the first settled live loss, the account
snapshot recorded `consecutive_losses = 1`, and every later candidate failed
final live risk with `consecutive_loss_limit_reached`. Because no later trade
could execute, the counter could never reset through a winning settlement.

Observed production evidence on 30 September 2026 showed one settled live loss
of `-1.35221376` USD followed by eight Telegram candidate cancellations for
`consecutive_loss_limit_reached`.

## V2 contract

Continuous-live v2 disables the consecutive-loss gate explicitly:

```text
authorization_mode = manual-telegram-continuous-v2
                  or auto-telegram-continuous-v2
max_consecutive_losses = 0   # disabled
max_daily_loss_usd = 10      # unchanged and still enforced
max_trade_size_usd = 10      # unchanged
max_total_exposure_usd = 10  # unchanged
target_notional_usd = 5      # unchanged
min_edge = 0.075             # unchanged
network attempts per intent = 1
```

For `LiveRiskPolicy`, zero now has one precise meaning for
`max_consecutive_losses`: that single rule is disabled. Zero remains fail-closed
for the monetary limits where zero already means no new exposure/loss capacity.

The canary/legacy v1 authorization modes remain supported with
`max_consecutive_losses = 1`; historical authorization is not reinterpreted.

## Runtime binding

The runtime authorization carries `max_consecutive_losses` copied from the
source-truth authorization. Runtime verification requires an exact match.
The source passes the verified runtime value into preview/final live preparation,
so an old v1 session continues using 1 and a new v2 session uses 0.

## Unchanged safety invariants

This change does not alter the frozen V3 model, prediction timing, target notional,
daily-loss limit, total-exposure limit, minimum edge, fresh-book/final-risk checks,
Telegram approval requirement, geographic executor requirement, or the one-network-
submission-attempt-per-intent rule.

## Authorization boundary

Merging this implementation does not authorize production deployment, restart,
runtime authorization replacement, kill-switch change, or a live order. Production
use requires a fresh exact-release v2 continuous-live authorization.
