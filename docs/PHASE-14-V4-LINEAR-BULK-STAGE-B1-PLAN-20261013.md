# Phase 14 V4 — Bybit-linear bulk Stage B1 isolation plan

**Status: REPOSITORY-ONLY OBSERVABILITY PREPARATION. PRODUCTION DEPLOYMENT AND EXECUTION NOT AUTHORIZED.**

Date: 13 October 2026. Tracking: [Issue #589](https://github.com/Dtwosam/BP/issues/589).

## Decision: investigate the existing recorder's bulk connection, not the model

Original October 7–8 Phase 14 V4 successor produced 23 predictions from 245 markets,
106 source-ineligible markets (105 involving Bybit linear) and 116 missed
decisions. It did not meet economic acceptance; **Phase 14 HOLD**.

The operator-reported one-time **Stage A** probe around 21:08 UTC on
12 October found 33/60 eligible ticker prices with possible LIMIT 8
false negatives and zero query-visible trade-price candidates in 60
short lookbacks. The separately authorized one-time **Stage B0**
probe at **22:27:43.681001 UTC** found **zero** currently
query-visible `trade` rows **and zero**
`orderbook_snapshot`/`orderbook_delta` rows in its independent,
uncapped 60-second Bybit-linear BTCUSDT classes. These were distinct,
non-synchronous observations, neither a packet capture nor a commit
timeline nor evidence of an exchange-wide outage. Both approvals are
**consumed**.

Both B0 categories belong to the same **linear bulk socket**, while
the ticker path has a separate socket. The highest-value open question
is whether that bulk socket actually receives and routes messages,
and if so why those messages do not appear as committed,
query-visible rows within the relevant cutoff.

## A newly verified incident-provenance limitation

Current deployed code (previously reported `/opt/bp` checkout
`a352c66ec0110925727bc40de767ee4ba981f965`) and baseline main
create distinct `bybit_linear` (orderbook/publicTrade/liquidation)
and `bybit_linear_ticker` (ticker) WebSocket runners. Both report
`source='bybit'`, `stream='linear'`, and the same endpoint URL.
Although their `FeedWatchdog` instances are distinct, existing
`FeedIncident` records have **no stable socket/component identity**
in their persisted metadata for connected, disconnected, reconnect,
clock-skew, stale or recovered incidents. A `bybit/linear connected`
record therefore **cannot identify whether the bulk socket was up**.

This branch fixes *that observability contract only*:
`WebSocketCollectorRunner` accepts optional `component_name` and
adds `details.recorder_component` to **all incidents it emits**.
The default `None` preserves unchanged behavior for existing other
runners. Recorder assembly supplies explicit names for the four
Bybit sockets (`bybit_spot`, `bybit_spot_ticker`,
`bybit_linear`, `bybit_linear_ticker`). This changes **only
incident metadata**, not raw-event routing, source timestamps,
writer batching, DB schema, the predictor or safety gates.
New regression tests prove distinct incident provenance even
though linear bulk and ticker source/stream/URL are identical and
cover clock-skew and watchdog recovery.

**Important:** Newly labeled incidents would be emitted **only after
a separately authorized deployment** of this code and a future
socket event. Old unlabeled records cannot be reclassified with
certainty. Merging source to GitHub is **not** deploying it.

## Required B1 discriminator sequence (design, not executable authorization)

A useful Stage B1 would be one **finite prospective, zero-money,
paper/research-only** observation of the *same running recorder's*
Bybit-linear **bulk** socket; a second independent WebSocket client
would not prove what the recorder received.

1. **Safety and exact identity preflight.** Review exact main and
   minimal backport from deployed `a352c66e` rather than advancing
   `/opt/bp` wholesale. Verify immutable code/runner/unit fragments,
   target runtime and active database/recorder, storage health and
   on-disk configuration. Require `MODE=research`,
   `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`,
   `MAX_DAILY_LOSS_USD=0`, no automatic promotion, no wallet
   material, unchanged V4 model/2-second source-age gate, and
   no holdout access. Record PID and restart counters.
2. **Record socket-specific lifecycle and ACK outcome.** Observe
   `bybit_linear` separate from `bybit_linear_ticker`,
   subscription topic names and success/failure ACK categories.
   Do not capture raw ACK payloads, errors containing credentials,
   frames, signatures, account identifiers or timestamps of
   individual transactions in shared output.
3. **Count pipeline stages by safe event category.** For bounded
   time buckets, count (a) received WebSocket data frames by
   topic class, (b) parsed `trade`/`orderbook_*` events and
   source-timestamp-null events, (c) successfully enqueued
   bulk items and backpressure episodes, (d) writer attempts
   and *post-commit* inserted rows; distinguish benign dedupe
   conflicts from errors. Counters must never be labeled
   committed until the transaction context has exited
   successfully. Do not read raw event payloads to a log.
4. **Correlate counts within the same monotonic observation
   window**, without equating `RawEvent.received_at` with
   PostgreSQL transaction visibility time. Include queue
   depth at boundaries and pending events, accounting for
   in-flight work so differences do not automatically imply loss.
5. **Aggregate-only output:** only topic/component names, integer
   counters, clock-aligned window boundaries, query/commit
   success-failure counts, and preflight/stop markers.
   No raw wire frames, price values, payloads, order IDs,
   credentials, wallets, trade execution, or third-party
   account material.

### Decision table

| New observation | Next supported conclusion |
| --- | --- |
| Bulk socket not connected, frequent errors/reconnects | Investigate bulk connection availability, not trade-price parsing |
| Connected but ACK explicitly rejects topic(s) | Investigate subscription negotiation; distinguish partial from full rejection |
| ACK successful, no bulk data frames | Bulk socket silent during window; cannot assign exchange vs transport cause yet |
| Frames >0, parsed events =0 | Parser/topic/schema path becomes leading suspect |
| Parsed/enqueued >0, committed =0 after accounting for in-flight work | Writer/queue/commit/dedupe path becomes leading suspect; not automatically data loss |
| Committed >0 while an *aligned, authorized* query sees zero | Predicate/partition/clock/visibility issue requires bounded review |
| Only ticker socket receives data | Confirms ticker cannot serve as a positive bulk-connection control |
| No events across all sockets | Consider shared host/network/recorder interruption; more context required |

### Rollout/rollback boundaries

**No Stage B1 deployment, process restart, SQL probe, socket capture,
live paper shadow or observer activation is authorized yet.** The
incident-label change alone is not full B1 instrumentation. Before any
production change, a separate PR should supply a **minimal backport**,
on-target code/venv identity checks, metrics overhead budget, finite
observation time, explicit no-orders/no-money safety verification,
service restart/health-rollout plan, abort conditions and
rollback to the exact previous deployed commit. All changes must pass
existing CI and synthetic recording with missing/rejected ACK,
topic silence, backpressure, dropped connection and commit failure.
A production restart or changed recorder service requires **fresh
explicit user authorization**, even if this GitHub branch merges.

**No second Stage A or B0 run. No model adjustment before the data
path is isolated. No shadow rerun, holdout or trading promotion.**
Phase 14 **HOLD** and economic acceptance **NOT MET**.
