# Phase 14 — Frozen V3 Live-Gate Reassessment

**Date:** 23 September 2026  
**Status:** engineering implemented; production reassessment not yet run  
**Mode:** RESEARCH; live trading disabled; real-money limits remain zero

## Decision

The user explicitly authorized pursuing a controlled real-money transition for the exact frozen V3 while V4 research continues in parallel. This records the human-authorization gate only. It does not override any other Master live-gate row and does not authorize a geographic bypass, wallet-secret disclosure, live enablement, or nonzero money before the complete gate passes.

The frozen V3 identity remains immutable:

```text
model_sha256       = 124627e15cab3997b8abe54ec5237450d976ab5682953f45a1399a76b6dae0e7
prediction_version = v3-frozen-paper-v1
execution_version  = paper-execution-v3-frozen-v1
offset_seconds     = 240
min_edge           = 0.075
```

No refit, recalibration, threshold search, timing change, or paper-sizing change is permitted by this reassessment.

## Read-only evidence contract

`bp_engine.v3_live_gate.cli` must isolate V3 evidence by the frozen execution identity and read PostgreSQL with `default_transaction_read_only=on`. It reports:

- settled trades, wins/losses, win rate, and Wilson 95% interval;
- realized total/mean after-cost P&L and deterministic bootstrap 95% interval for mean P&L;
- gross profit/loss and profit factor;
- maximum realized-P&L drawdown and maximum losing streak;
- largest winner, its share of total P&L, and P&L with the largest winner removed;
- V3-only prospective calibration metrics;
- V3-only order/fill/settlement reconciliation.

The report is evidence only. It must always return `automatic_promotion=false`, `live_trading_enabled=false`, `real_money_mutation_performed=false`, `master_live_gate_mutated=false`, and `phase15_permitted=false`.

## Remaining live-gate boundary

Before any controlled live launch, the project must freshly reassess every Master live-gate row, including current direct Polymarket geographic eligibility, risk/kill-switch readiness, execution/reconciliation, paper-sample uncertainty, profitability robustness, and calibration. Any failed or insufficient row keeps Phase 15 blocked.

V4 collection continues unchanged under `v4-gate-b-preregister-v1`; the V3 live-gate work must not read V4 labels, train V4, select a V4 policy, or alter the frozen V4 cohort.
