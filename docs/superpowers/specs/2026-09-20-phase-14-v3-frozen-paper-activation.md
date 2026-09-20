# Phase 14 — Frozen V3 Zero-Real-Money Paper Activation

**Date:** 20 September 2026  
**Status:** repository implementation + explicit production activation authorization  
**Mode:** RESEARCH only

## Purpose

Run the already-frozen V3 Gate B successor prospectively against live recorder evidence without sending real orders or changing the model.

This paper epoch measures how the frozen V3 policy behaves after its final holdout was consumed. Its results are observational evidence only and may not be used to rewrite the frozen V3 holdout.

## Frozen strategy identity

```text
research_plan_version = v3-gate-b-preregister-v2
plan_sha256            = f1480734cb4accf08fe0b3cfd0f15a733786dd008f8e76df75944e9dd39a9022
selection_sha256       = a088c61b291a67866af1c565ee936d9761e6a2936b49f121f616081284dcd508
model_sha256           = 124627e15cab3997b8abe54ec5237450d976ab5682953f45a1399a76b6dae0e7
candidate              = single_feature_btc_logistic
feature_version        = core-v3-btc-native
label_version          = official-outcome-v1
horizon_seconds        = 300
offset_seconds         = 240
edge_policy            = trade_threshold
min_edge               = 0.075
fee_rate               = 0.07
slippage_buffer        = 0.01
max_book_age_seconds   = 10
```

No field above may be selected again from paper results.

## Prospective signal contract

The production activation timestamp is a hard lower bound on market start time. The paper predictor must not create a V3 paper signal for a market that started before activation. Pre-activation markets are permanently outside this paper epoch.

At market start + 240 seconds:

1. rebuild the frozen `core-v3-btc-native` feature row using recorder state available as of the scheduled timestamp;
2. run the exact frozen V3 model + calibration;
3. select Up when calibrated probability is at least 0.5, otherwise Down;
4. read the selected Polymarket token book as of the same scheduled timestamp;
5. calculate fee, slippage and cost-adjusted edge using the frozen policy;
6. set `trade=true` only when the selected book is fresh/executable and adjusted edge is at least 0.075;
7. discard the signal if the full computation finishes more than 10 seconds after the scheduled time.

Polymarket state is execution-only. It must never enter the forecast predictor vector.

## Paper execution identity

```text
prediction_version = v3-frozen-paper-v1
execution_version  = paper-execution-v3-frozen-v1
virtual_cash       = $100.00
target_notional    = $5.00
simulated_latency  = 250ms
order_ttl          = 2000ms
real_money         = $0.00
```

The virtual sizing values preserve the established Phase 12 paper-execution defaults. They are not model or edge-policy tuning.

V3 virtual cash, orders, fills and settlements are isolated by execution version. The legacy paper service must exclude the V3 prediction version while otherwise preserving its previous prediction population and historical execution semantics.

## Outcome and settlement

Official Polymarket resolution remains the sole outcome truth. The existing prospective outcome sync may append the canonical `official-outcome-v1` label/evaluation for V3 paper predictions after resolution.

The V3 paper executor may then settle simulated fills against that immutable evaluation.

## Safety

Production activation requires:

- `MODE=research`;
- `LIVE_TRADING_ENABLED=false`;
- `MAX_TRADE_SIZE_USD=0`;
- `MAX_DAILY_LOSS_USD=0`;
- exact frozen model SHA verification before services start;
- isolated versioned runtime under `/var/lib/bp/runtime`;
- deployed `/opt/bp` checkout unchanged;
- recorder PID unchanged;
- V4 forward collector remains active;
- no private key, wallet, signer, allowance, live-order submission or real-order cancellation path;
- no model refit, recalibration, threshold tuning or automatic promotion.

Rollback may restore service/unit state, but it must never delete append-only research evidence already created.

## Parallel V4 research

V4 regime-aware prospective feature collection continues unchanged. V3 paper observations do not authorize V4 model fitting and cannot be used to tune the consumed V3 final holdout.
