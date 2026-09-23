# Phase 15 — Frozen V3 One-Order Live Canary v1

**Date:** 23 September 2026  
**Status:** engineering candidate; no real order submitted at freeze time

## Objective

Run exactly one tightly bounded live-money canary using the exact frozen V3 champion while V3 paper observation and V4 research continue unchanged.

The user explicitly authorized V3 live trading and stated that up to **$10 per market** is acceptable risk. That amount is a **hard ceiling**, not the canary target. The first live canary retains the frozen paper strategy's existing **$5 target notional**.

## Frozen strategy identity

- model SHA-256: `124627e15cab3997b8abe54ec5237450d976ab5682953f45a1399a76b6dae0e7`
- prediction version: `v3-frozen-paper-v1`
- paper execution version: `paper-execution-v3-frozen-v1`
- selected offset: 240 seconds
- minimum edge: 0.075
- strategy target notional: $5

No refit, recalibration fit, threshold change, timing change, feature change, side/regime filter, or paper-sizing change is allowed.

## Master gate

Before this contract:

- all statistical V3 readiness rows passed under predeclared rules;
- explicit user authorization passed;
- the user's ordinary physical-network Polymarket geoblock check passed from `NG/LA`;
- the dedicated Johannesburg execution host `bp-v3-canary-exec` in `africa-south1-a` passed the direct official geoblock check as `blocked=false`, `ZA/GP`.

Therefore the complete Master live gate may be marked `pass` for this controlled canary. This does not authorize a broad rollout.

## Canary risk envelope

The hard live policy is:

```text
policy_version = v3-live-canary-v1
strategy_target_notional_usd = 5
max_trade_size_usd = 10
max_total_exposure_usd = 10
max_daily_loss_usd = 10
max_consecutive_losses = 1
max_accepted_orders = 1
min_edge = 0.075
min_liquidity_usd = 5
max_spread = 0.10
max_prediction_age_seconds = 30
min_time_to_expiry_seconds = 15
cooldown_seconds = 86400
order_ttl_seconds = 2
```

The canary considers only paper orders created **after** the canary preparation activation timestamp. Historical paper trades may never be converted into live orders.

## Architecture

The existing US production host remains the recorder/database/frozen-V3/V4 research host. It must never hold the Polymarket private key and must never make an authenticated Polymarket order request.

The Johannesburg VM is execution-only. It holds the root-only signing environment and the pinned official `polymarket-client==0.7.1` runtime.

The preparation path on the US host reuses the exact frozen paper order request, applies the existing live-risk engine, persists the risk decision, and persists the order intent **before** any possible network submission.

## Required manual sequence

1. **Bootstrap wallet/signer**
   - helper: `scripts/deploy/phase15_v3_canary_bootstrap_cloudshell.sh`
   - requires `PHASE15_ACCEPT_WALLET_SETUP=yes`;
   - private-key input is hidden locally in Cloud Shell and must never be pasted into chat or Git;
   - stores root-only signer material on Johannesburg only;
   - installs the pinned SDK;
   - leaves `/etc/bp-canary/KILL` engaged;
   - submits no order.

2. **Prepare one new canary**
   - helper: `scripts/deploy/phase15_v3_canary_prepare_cloudshell.sh`;
   - waits only for a new frozen-V3 paper trade after activation;
   - creates a zero-order reconciliation baseline if the live ledger is empty;
   - applies the canary risk policy;
   - writes durable risk and intent rows;
   - saves `/tmp/bp-phase15-v3-canary-prepared.json` mode 0600;
   - submits no order.

3. **Review and arm**
   - helper: `scripts/deploy/phase15_v3_canary_arm_cloudshell.sh`;
   - requires `PHASE15_ACCEPT_REAL_MONEY=yes`;
   - revalidates the prepared payload, $5 strategy target, $10 ceilings, Master gate, and market time remaining;
   - writes a short-lived activation manifest valid for at most 45 seconds;
   - removes the Johannesburg kill switch;
   - submits no order.

4. **Manual submission**
   - the user manually pipes only the prepared JSON to the Johannesburg executor;
   - the executor rechecks direct geoblock and the activation manifest;
   - before the SDK network submission it atomically re-engages the kill switch, consuming the one-shot arm;
   - it independently rejects any notional above $10;
   - it submits the bounded limit BUY and, after two seconds, attempts to cancel any unfilled remainder;
   - it returns sanitized JSON only.

5. **Record and stop**
   - save the sanitized executor JSON to `/tmp/bp-phase15-v3-canary-result.json`;
   - run `scripts/deploy/phase15_v3_canary_record_cloudshell.sh`;
   - stop after the first accepted order;
   - reconcile official order/fill state before considering any second order.

## Hard boundaries

- no automated real-money submission;
- no second order is authorized;
- no private key or wallet secret on the US host, in Git, or in chat;
- no VPN/proxy/tunnel geographic circumvention;
- no V3 tuning from live results;
- no V4 mutation or early Gate B action;
- no stake increase from a single successful canary;
- ambiguous submission/cancellation state fails closed and requires reconciliation before any new arm.
