# Phase 14 Recorder Backpressure Reliability Repair Design

Date: 2026-09-03
Status: proposed for implementation after user review
Scope: research-only recorder reliability; no V2 policy/model selection and no trading activation

## 1. Problem statement

The Phase 14 V2 forward collector is correctly recording missing/stale Polymarket evidence, but the upstream recorder cannot sustain bursty Polymarket traffic. Production evidence from 2026-09-03 shows repeated `backpressure` incidents with `queue_size=50000`, followed by WebSocket ping timeouts, reconnect loops, and one explicit Polymarket close reason: `slow consumer: send buffer full`.

The failure is load-dependent. During healthy periods the recorder persists normal Bybit, Coinbase, and Polymarket traffic. During the two observed overload episodes, Polymarket reached roughly 60k-100k raw events per five minutes while Bybit and Coinbase continued at their usual rates. At the same time, the shared raw-event queue saturated, Polymarket reconnects increased, and captured Polymarket traffic then collapsed to a few hundred events per five minutes. The V2 feature collector subsequently recorded missing or stale Polymarket books and last trades. This is therefore an ingestion reliability failure upstream of V2 feature semantics, not evidence that V2 timestamp calculations are wrong.

## 2. Safety boundary

This repair must preserve all project safety controls exactly:

- `MODE=research`
- `LIVE_TRADING_ENABLED=false`
- `MAX_TRADE_SIZE_USD=0`
- `MAX_DAILY_LOSS_USD=0`
- `automatic_promotion=false`
- Phase 15 remains blocked
- no live-order or real-money path is added or enabled
- no geography bypass
- no V2 model, calibrator, timing, freshness, edge, or `min_edge` policy selection
- no V2 paper execution
- no V1 retuning from failed V1 trades
- no changes to the existing selected-book 10-second freshness rule

The repair changes recorder reliability only. Existing immutable `market_features` and raw-event evidence are never rewritten to manufacture missing data.

## 3. Goals

1. Sustain the observed Polymarket burst rate with headroom without starving the WebSocket receive and heartbeat path.
2. Preserve lossless raw-event ingestion semantics: accepted parsed market events are not silently dropped.
3. Increase database drain throughput using bounded concurrency rather than an unbounded task fan-out.
4. Prevent a saturated queue from generating hundreds of synchronous incident writes that amplify database pressure.
5. Preserve reconnect/resubscribe correctness and current dynamic token-rotation behavior.
6. Make overload episodes observable with explicit start/recovery evidence and useful counters.
7. Add deterministic stress tests that reproduce the production failure mode and prove the repaired path drains all events.

## 4. Non-goals

- Do not increase the queue size as the primary fix. A larger queue only delays the same failure if sustained drain throughput remains below ingress.
- Do not introduce event dropping, lossy sampling, or feature-specific filtering.
- Do not add a durable disk spool in this repair. A spool remains a follow-up option only if the repaired bounded writer still cannot sustain production bursts.
- Do not change V2 feature definitions, last-trade semantics, outcome-blindness, or Gate B requirements.
- Do not alter prediction, execution, dashboard performance metrics, or trading gates.

## 5. Current failure chain

The current recorder uses one shared bounded `EventBuffer` with `RECORDER_QUEUE_MAXSIZE=50000`. Each WebSocket runner parses a received frame and awaits the shared event sink for every resulting `RawEvent`.

The event sink first calls `put_nowait`. When the queue is full, it synchronously records a `backpressure` incident in PostgreSQL and then awaits `buffer.put(event)`. Because this happens inside the WebSocket receive loop, a full queue stops that connection from continuing to receive frames. Heartbeat work is serviced by the same runner loop, so prolonged event-sink blocking can prevent timely heartbeat processing. Polymarket then closes or times out the slow connection. Reconnects can deliver fresh book snapshots and increase burst pressure again.

The current `BatchWriter` uses one consumer task and one database transaction at a time. It collects at most `RECORDER_BATCH_SIZE=500` events and awaits one synchronous SQLAlchemy insert, executed via `asyncio.to_thread`, before draining the next batch.

The observed production behavior is consistent with sustained ingress exceeding this single-writer drain rate.

## 6. Selected architecture

### 6.1 Bounded parallel database writers

Replace the single serial raw-event database write path with a bounded worker pool while retaining one shared bounded input buffer.

The `BatchWriter` abstraction will support a configurable worker count. Each worker independently drains bounded batches from the same `EventBuffer` and invokes the existing lossless `insert_events` repository path. PostgreSQL already provides idempotency through the immutable `dedupe_key` unique constraint and `ON CONFLICT DO NOTHING`, so concurrent batches are safe even if ordering across separate batches is not identical to arrival ordering.

The setting is exactly `RECORDER_WRITER_WORKERS`, with an application default of `1` to preserve current behavior unless deployment explicitly opts into concurrency. The implementation must validate `>= 1` and must not spawn an unbounded database task per event or per frame. A later production rollout may set a higher bounded value only after stress/CI verification; the rollout value is not selected by this design.

Raw event timestamps and dedupe keys remain authoritative for chronology. Consumers must not infer global arrival ordering from auto-increment IDs across concurrent transactions.

### 6.2 Backpressure incident coalescing

Backpressure must remain fail-closed and observable, but the recorder must not write one incident row per blocked event.

Introduce an in-memory overload episode state in the buffered event sink:

- on the first full-queue observation, emit one `backpressure` incident containing queue size and episode start metadata;
- while the episode remains active, increment a local blocked-event counter without additional `backpressure` incident writes;
- the first subsequent event that succeeds through `put_nowait` closes the episode and emits one `backpressure_recovered` incident containing episode duration and blocked-event count;
- if the process exits during an active overload episode, lossless event handling still takes priority; the missing recovery summary is acceptable because the start incident remains durable.

This deterministic rule intentionally avoids a separate queue-depth recovery threshold. Incident coalescing must never suppress actual WebSocket `error`, `reconnect`, `connected`, `disconnected`, `stale`, or `recovered` incidents.

### 6.3 Preserve receive-path semantics

The repair does not create an unbounded secondary memory queue and does not silently drop events. If all writer workers are unable to keep up and the bounded queue fills, the receive path may still eventually block. The objective is to raise sustainable drain throughput above the observed peak and remove self-amplifying incident writes so this bounded fail-closed state is not reached under demonstrated production load.

This design intentionally prioritizes evidence integrity over pretending the system can absorb unlimited traffic.

### 6.4 Follow-up trigger for durable spool

A durable local spool is not implemented now. It becomes justified only if post-repair stress or production soak still produces any of the following under expected market traffic:

- repeated queue saturation;
- Polymarket slow-consumer closes attributable to local ingestion throughput;
- sustained writer backlog that does not recover promptly after a burst.

If triggered, the spool must be designed as a separate project because it changes persistence topology and crash-recovery semantics.

## 7. Configuration

Add `RECORDER_WRITER_WORKERS` to recorder configuration.

Requirements:

- integer >= 1;
- application default `1`;
- represented in `.env.example`, `deploy/bp.env.example`, bootstrap configuration, and config tests;
- no safety/trading setting changes.

Queue size, batch size, and flush interval remain independently configurable. This repair must not change their production values merely to make tests pass.

## 8. Data integrity and ordering

Each parsed `RawEvent` remains immutable and carries its own `received_at`, `source_timestamp`, sequence information when available, payload, and `dedupe_key`.

Concurrent insertion can change the order in which rows receive database IDs. Therefore:

- causal readers must continue ordering by event timestamps plus existing tie-breakers, not assume ID alone is global chronology;
- tests must confirm all submitted unique dedupe keys are present after drain;
- duplicate events must remain idempotent through `ON CONFLICT DO NOTHING`;
- shutdown must drain the queue before writer workers terminate.

No existing raw events or `market_features` rows are deleted or rewritten by this change.

## 9. Error handling

If any writer worker raises an unexpected exception, the writer component must fail as a supervised recorder component rather than silently losing queued data. Recorder service supervision will then stop sibling components according to the existing fail-closed behavior.

Backpressure episode bookkeeping must be safe under the asyncio event loop and must not conceal database errors while recording incident start/recovery rows.

WebSocket reconnect policy remains unchanged unless a test demonstrates an independent bug.

## 10. Testing strategy

Implementation follows TDD.

### 10.1 Unit tests

Add tests proving:

- multiple writer workers can drain one `EventBuffer` concurrently;
- every unique event submitted in a burst is eventually delivered to the sink exactly once at the batch-writer interface;
- worker count is bounded and validated;
- shutdown drains all queued events;
- one worker failure propagates and fails the component;
- backpressure emits one start incident per continuous episode rather than one per event;
- recovery emits a summary with blocked-event count and duration;
- a second later overload produces a distinct new episode;
- no event is dropped when backpressure occurs.

### 10.2 WebSocket/recorder stress test

Create a deterministic fake Polymarket WebSocket burst materially above the observed production peak. The test should drive enough parsed events to overflow the old single-writer design while using a deliberately slow fake database sink.

The repaired design must prove:

- receive processing continues while writer workers drain at the tested sustainable rate;
- heartbeat/outbound handling is not starved at that rate;
- all unique raw events reach the sink;
- incident count remains bounded rather than scaling one-for-one with blocked events;
- no reconnect is caused by local slow-consumer behavior in the synthetic sustainable-load case.

Tests must avoid asserting unrealistic infinite-load behavior. A separate overload test should prove that if ingress exceeds configured bounded capacity indefinitely, the system remains fail-closed and does not silently drop data.

### 10.3 Existing regression gates

Run at minimum:

- targeted recorder writer tests;
- WebSocket runner reliability/reconnect tests;
- Polymarket reconnect/resubscribe tests;
- recorder state tests;
- V2 feature coverage/forward tests;
- config tests;
- full project test suite if feasible in CI;
- existing recorder smoke/soak workflows before production rollout authorization.

## 11. Production rollout gate

Implementation and PR do not authorize production deployment.

A separate production rollout must be explicitly authorized and must include:

1. exact from/to commit verification;
2. safety environment verification (`research`, live trading false, zero trade/loss limits);
3. config verification for writer worker count;
4. recorder-only controlled restart/rollout;
5. confirmation that dynamic Polymarket subscriptions recover correctly after restart;
6. monitoring of queue/backpressure/reconnect incidents during a natural high-volume interval;
7. post-rollout V2 coverage report confirming four-offset completeness, zero future cutoff violations, zero invalid nonfinite values, `policy_selected=false`, and `automatic_promotion=false`;
8. rollback that reverts code/config only and never deletes or rewrites collected evidence.

## 12. Acceptance criteria

Engineering acceptance requires all of the following:

- deterministic stress test at or above the observed peak passes without event loss;
- writer concurrency remains bounded;
- backpressure incident amplification is removed;
- existing reconnect/resubscribe behavior remains green;
- all safety and V2 outcome-blind boundaries remain unchanged;
- no production deployment occurs as part of the implementation PR.

Production acceptance, if separately authorized later, additionally requires a successful natural-load soak with no local slow-consumer close attributable to queue saturation and no regression in V2 causal integrity.
