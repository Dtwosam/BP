# Phase 15 — Frozen V3 Single-Order Live Canary v1

**Date:** 23 September 2026  
**Status:** contract frozen before real-money deployment  
**Purpose:** permit exactly one externally submitted frozen-V3 live order attempt under a small, fail-closed risk envelope.

## 1. Preconditions

All Master live-gate rows must be `pass` before activation.

Evidence frozen before this canary includes:

- frozen V3 model SHA `124627e15cab3997b8abe54ec5237450d976ab5682953f45a1399a76b6dae0e7`;
- prediction version `v3-frozen-paper-v1`;
- selection SHA `a088c61b291a67866af1c565ee936d9761e6a2936b49f121f616081284dcd508`;
- statistical readiness PASS at `docs/evidence/phase-15-v3-accelerated-readiness-production-20260923.json`;
- user ordinary physical-network direct geoblock `blocked=false`, `NG/LA`;
- dedicated execution-host direct geoblock `blocked=false`, `ZA/GP`, at `docs/evidence/phase-15-v3-canary-host-geography-20260923.json`;
- explicit user authorization for controlled real-money frozen-V3 trading.

The existing US production host remains geographically blocked and must never perform authenticated Polymarket order submission.

## 2. Frozen strategy identity

The canary changes no forecasting or edge-selection behavior.

```text
prediction_version       = v3-frozen-paper-v1
model_sha256             = 124627e15cab3997b8abe54ec5237450d976ab5682953f45a1399a76b6dae0e7
selected_offset_seconds  = 240
min_edge                 = 0.075
fee_rate                 = 0.07
slippage_buffer          = 0.01
max_selected_book_age_s  = 10
target_notional_usd      = 5.00
order_ttl_ms             = 2000
share_precision          = 6
```

The live request must be derived from the same deterministic frozen-V3 order construction used by paper execution. Only the execution identity changes to `live-execution-v3-canary-v1`.

No refit, recalibration fit, threshold change, timing change, feature change, side/regime filter, or target-notional change is permitted.

## 3. One-attempt boundary

The canary permits **one external order-submission attempt total**, not one trade per market.

Before the Johannesburg executor calls the official SDK submission method, while holding an exclusive local lock, it must atomically:

1. verify no prior canary-attempt marker exists;
2. write the canary-attempt marker;
3. engage the remote kill switch.

That ordering ensures a process crash, SSH ambiguity, or network timeout cannot unlock a second attempt.

The US-side canary worker also engages its local kill switch immediately after a durable live order intent exists and the gateway returns from the external attempt.

Risk-blocked signals that never create a live order intent do not consume the canary.

## 4. Risk policy

The canary risk policy is frozen as:

```text
policy_version                 = live-risk-v3-canary-v1
max_trade_size_usd             = 5.00
max_total_exposure_usd         = 5.00
max_daily_loss_usd             = 5.00
max_consecutive_losses         = 1
min_edge                       = 0.075
min_probability                = 0
min_liquidity_usd              = 1.00
max_spread                     = 0.05
max_prediction_age_seconds     = 10
min_time_to_expiry_seconds     = 45
cooldown_seconds               = 300
max_external_submission_attempts = 1
```

The `min_probability=0` setting is deliberate: the frozen V3 probability is the probability of UP, so a directional probability floor would incorrectly discriminate against valid DOWN selections. Directional eligibility remains controlled by the frozen V3 cost-adjusted edge decision.

## 5. Execution-host isolation

The Johannesburg VM `bp-v3-canary-exec` is execution-only.

The US host retains:

- immutable frozen-V3 prediction production;
- current Polymarket public book/state;
- live risk evaluation;
- duplicate-intent prevention;
- durable live risk/order/event ledger;
- reconciliation evidence.

The Johannesburg host receives only a normalized execution request over SSH from a dedicated key. It holds the trading secret and performs:

- a fresh direct official geoblock check;
- activation-manifest validation;
- independent $5 worst-case notional validation;
- single-attempt marker/kill-switch enforcement;
- official SDK signing/submission;
- a two-second post-submit cancellation attempt to remove any unfilled remainder.

The SSH authorized key must be restricted to the US production VM source address, disable forwarding, and force the executor command. The Johannesburg host must not expose a general trading HTTP proxy.

## 6. Secret boundary

The Polymarket private key must never be committed, pasted into chat, printed, or installed on the US host.

Deployment must prompt for the private key interactively in Cloud Shell with terminal echo disabled, transmit it only to the Johannesburg host over the authenticated GCP SSH channel, and store it in a root-controlled executor environment file.

An optional Polymarket wallet/funder address may be stored beside it.

The US host receives only the non-trading SSH key used to invoke the forced execution command.

## 7. Activation manifest

Both hosts must receive the same short-lived activation manifest bound to the exact deployed Git SHA.

The manifest must:

- have `authorized=true`;
- identify this single-order canary;
- match the deployed Git SHA exactly;
- be issued no earlier than deployment;
- expire within two hours.

A missing, mismatched, future-issued, or expired manifest blocks submission.

## 8. Order lifetime

The frozen paper order TTL is 2000 ms.

After an accepted official order response, the Johannesburg executor waits exactly two seconds and attempts to cancel the external order ID. This cancellation is intended to remove any unfilled remainder; already matched shares remain positions.

The canary stops regardless of whether that post-submit cancellation succeeds, fails, or reports that the order is no longer open. Any uncertainty requires reconciliation before another live attempt can ever be authorized.

## 9. Initial reconciliation

Before activation, the US live ledger must contain zero live order intents and zero live order events.

If the live ledger is empty and no prior live reconciliation exists, the canary worker may create exactly one initial zero-exposure reconciliation record. This is initialization evidence only, not a claim about future account state.

After the external attempt, no second canary may be authorized until the external order and resulting position are reconciled.

## 10. Deployment boundary

The deployment helper must require explicit shell acknowledgement:

```text
PHASE15_ACCEPT_REAL_MONEY_CANARY=yes
PHASE15_CANARY_MAX_LOSS_USD=5
```

It must install both sides with kill switches engaged, verify exact source identity, verify Johannesburg `blocked=false` again, verify the US live ledger is empty, install the short-lived manifest, verify the forced SSH executor health, and remove kill switches only as the final activation step.

The helper may then start the single-order canary worker.

No second order, automatic retry after an external attempt, risk-limit increase, broad live daemon, V3 tuning, or V4 change is authorized by this contract.
