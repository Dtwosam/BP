# Phase 14 V4 — candidate Stage B0 trade-storage diagnostic

**Status: REVIEWED IN REPOSITORY ONLY; NOT AUTHORIZED FOR HOST EXECUTION.**
12 October 2026, scoped to [Issue #589](https://github.com/Dtwosam/BP/issues/589).
Source of truth at proposal start: `main`
`5924bb9ed8bbc3d3331dae6caab94a4e83174a53`.

## Execution closeout — 12 October 2026

**Status: ONE-TIME Stage B0 APPROVED AND REPORTED COMPLETE.**
The user separately approved the exact bounded read-only diagnostic.
The operator reported `DIAGNOSTIC_PREFLIGHT=PASS`,
`EXECUTION_MODE=VERIFIED_STDIN_NO_INSTALL`, one fixed 60-second
cutoff (`2026-10-12T22:27:43.681001+00:00`), 2 capped data
SELECT queries (`LIMIT 1024` each), 1500ms statement timeout
and 30-second external wall cap. The result returned
`PHASE14_V4_LINEAR_TRADE_B0_STATUS=PASS_DATA_ONLY` and
`DIAGNOSTIC_EXECUTION=COMPLETE` with read-only/no-write/no-order/
no-service-mutation markers. **Both trade and orderbook classes had
0 query-visible matching rows** in the minute ending at the
captured cutoff, neither capped. The result was supplied by the
operator and was **not independently executed by ChatGPT or CI**.
See `docs/evidence/phase-14-v4-linear-trade-storage-b0-20261012.json`
for full aggregate counts and explicit limitations.

This does not prove packet absence, a subscription failure, lost
DB writes or historic October 7–8 causality. The earlier Stage A
observation at 21:07–21:08 UTC is not synchronous with Stage B0.
No same-window ticker/control-lane measurements were taken.
The **separate Stage B0 authorization has been consumed**.
The design below is preserved as an archival review of what was
approved, **not as permission to rerun**.

## Why Stage B0, and the limited question it can answer

The **single, already consumed** Stage A authorization yielded
**0/60 query-visible, price-bearing Bybit-linear trade candidates**
in 60 short 12-second lookbacks. Those lookups excluded rows with
`source_timestamp IS NULL`, non-array trade payloads and events outside
the window. Offline tests in merged PRs #599–#600 showed the normal
trade-to-storage contract and demonstrated that the **bulk connection's
stream-level liveness is not public-trade topic health**.

A new **one-time, read-only, current-time** data view could make
this narrower distinction:

> During a single recent 60-second window, did the **committed,
> currently query-visible** raw-event table contain trade rows for
> Bybit-linear BTCUSDT **including those Stage A's predicate excludes**,
> compared with the order-book class on the same bulk connection?

It cannot show actual WebSocket packets, subscription acknowledgements,
in-memory queue events, rejected inserts, or transaction commit latency.
It cannot reconstruct source events or decision-time visibility in
the **7–8 October** historical shadow. It must not be used to declare
exchange outages, profitable economics or production readiness.

## Repository candidate (prepared, not executed on a host)

`scripts/report_v4_linear_trade_storage_b0.py` is a bounded
**read-only PostgreSQL** candidate. It is not a deployment script
and is not called by any recorder, service, scheduler, CI production job,
or paper runner. It emits only aggregate fields; no raw payloads,
trade prices, row IDs, event signatures, credentials or account IDs.

- A single captured `observed_at` in UTC; `received_at` between
  `observed_at - 60s` and `observed_at`, Bybit `linear`,
  instrument `BTCUSDT` only.
- **Two data SELECTs total**: (a) `event_type='trade'`, including null
  source timestamps, and (b) order-book snapshot/delta rows. They
  are **independently** sorted by `received_at DESC, id DESC` and
  capped at **1,024 rows each**. A normal read-only `SHOW` checks
  `default_transaction_read_only=on` before either data SELECT.
  The session requires PostgreSQL, autocommit and
  `statement_timeout=1500ms`.
- Each class aggregates sampled row count, null/non-null
  `source_timestamp`, JSON-array payload count, nonblank final
  `data[-1].p` **field presence**, and source timestamp/transport
  constraints **as of that one `observed_at` only**. A nonblank
  `p` field is **not** proof of a valid positive decimal price.
  When the 1,024-row cap is hit, `capped_inconclusive=true`:
  the counts are lower bounds on that class and the report must not
  interpret them as complete.
- A run should be guarded by the existing exact-file/venv SHA,
  approved branch/commit, `MODE=research`,
  `LIVE_TRADING_ENABLED=false`,
  `MAX_TRADE_SIZE_USD=0`, `MAX_DAILY_LOSS_USD=0`,
  `RECORDER_BATCH_SIZE=100`, and active critical services.
  The script itself checks the settings before its database connection and requires an explicit `--approval-ack AUTHORIZED_ONE_TIME_STAGE_B0` CLI acknowledgement; this is an operator reminder, **not** proof that approval was actually granted.
- If separately approved, the proposed **external process cap is
  30 seconds**, with no automatic retries, no schema changes, no
  snapshot writes, no live orders, no service restart, and no
  holdout/model access. Any mismatch, statement timeout or capped
  count remains an **inconclusive/stop** outcome, not a reason to
  increase limits and rerun under the same approval.
- The operator would retain only the aggregate JSON and the
  `PHASE14_V4_LINEAR_TRADE_B0_STATUS=PASS_DATA_ONLY` and
  read-only/no-write/no-order markers, with exact script hash,
  start/cutoff time and a confirmation of the external wall bound.
  Sensitive host values and raw events stay on the host.

### Interpretation matrix — separate observation from cause

| B0 result (uncapped classes only) | Supported, narrow statement | What is still unknown |
| --- | --- | --- |
| Trade rows >0, some timestamp null | Recent **committed** trade rows exist; Stage A would exclude null-timestamp rows | Whether live packets were rejected, when rows committed, why Stage A returned zero earlier |
| Trade rows >0, timestamp nonnull | Currently committed trade storage worked for some rows | Whether prices are valid, trade eligible at each actual historical decision, or prior probe sample times |
| Trade rows =0, book rows >0 | No **matching committed trade rows within this selected 60s window**, while book rows were query-visible | Whether trade topic is quiet, ACK failed, messages were in memory, dropped, rejected, committed outside the window, or subject to retention |
| Both classes =0 | No matching committed rows in either bounded class/window | Whether recorder was running, connection subscribed, storage delayed, query timing/window mismatch, etc. |
| Any class reaches 1,024 rows | Its sampled counts are **censored** | Total rows/class proportions beyond the cap |
| SQL timeout/error or preflight failure | Diagnostic inconclusive, stop | No permission to relax limits, mutate services, or repeat |

The **existing Stage A 0/60 trade candidate** result and the Stage B0
single-timepoint evidence, if ever separately approved and run, are
**not paired synchronized measurements**. A later row in B0 does not
prove it was committed or visible at an earlier Stage A cutoff.

## Why this is not a full Stage B

Only an **in-recorder, per-topic** prospective observation with
newly authorized instrumentation can distinguish actual subscribed
messages from parser/enqueue/commit stages in the same socket session.
A second consumer subscribing to Bybit separately would observe a
**different connection**, not prove what the existing recorder received.
Read-only SQL cannot reconstruct packet ingress or transaction commit
timelines; `received_at` is not commit time.

Such a future Stage B1 would need **separate review and authorization**
for exact recorder change/commit, service-restart risk, aggregate-only
topic counters, active health checks, finite duration, stop/rollback,
no-order/no-wallet controls and no preserved holdout data.
**Do not implement, deploy or activate B1 under this proposal.**

## Authorization / hold gates

**No new production access is authorized.** Stage A's one execution is
complete. Merely merging a candidate script, having passing CI, or
saying "continue" does not authorize a new database SELECT.
If Stage B0 is approved later, the exact *then-current* reviewed script
hash, deployed runtime identity, explicit one-time approval and
fail-closed operator command must be established anew. The reporter
must not be invoked as a substitute for a prior approval.

Original October 7–8 historical root causes remain unknown,
economic acceptance **NOT MET** (one settled modeled paper loss),
**Phase 14 HOLD**, live trading and automatic promotion disabled.
