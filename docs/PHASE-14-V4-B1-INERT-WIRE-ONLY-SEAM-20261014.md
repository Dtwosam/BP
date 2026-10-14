# Phase 14 Stage B1 — isolated wire-only seam feasibility

**14 October 2026 · #589 · SYNTHETIC ONLY / PRODUCTION NO GO**

## Engineering question

After PR #624 ruled out restarting the existing recorder or running a
second full recorder as safe shortcuts, can the existing Bybit-linear
bulk collector/parser run without creating a database, recorder service,
storage queue, writer or report exporter? **Yes in a fixed in-memory
rehearsal**; this does not establish on-host feasibility, successful
real exchange subscription or a production-safe independent launcher.

The dedicated offline script
`scripts/rehearse_stage_b1_inert_wire_only.py` composes the real
`WebSocketCollectorRunner`, `parse_bybit_message(venue="linear")`,
`build_bybit_subscription` and dormant `StageB1BulkObserver.wrap_parser`
with **only a hardcoded fake socket**. The connector accepts one
non-network sentinel, its fake frames are in memory and it provides no
connection to the real Bybit endpoint. A local sink **discards** all
parsed `RawEvent` objects; there is no `RecorderService`,
`build_default_recorder_service`, `EventBuffer`, `BatchWriter`,
SQLAlchemy engine, production settings, database schema, persistent
logs, network connector, process child, service action or trading.

The synthetic scenario choices are a fully received bulk trio
(trade, order book and liquidation); ACK only with no bulk frames;
a malformed source timestamp; an out-of-scope ticker frame; and a
rejected socket-level subscription ACK. Those cases independently
exercise real runner decoding, subscription serialization, parser
success/failure, bulk topic classification and exclusion of ticker.
The output is a small, fixed aggregate-only JSON. Its classification
is always prefixed `fake_`; all reported statuses end in **NO_GO**.

## What the data can and cannot distinguish

| Synthetic observation | Limited possible insight if real, separately authorized evidence later exists | What remains unknown |
|---|---|---|
| Bulk topic frames observed, parsed events present | Socket/parser delivery for those frames worked in that sampling window | Queue insertion, dedupe and DB commit/insertion, production feed freshness |
| Bulk frames observed, parser errors | Candidate parser/decoding boundary deserves inspection | Cause outside that sampled input, writer/DB outcome |
| Socket-level ACK present, no bulk topic frames | ACK alone does not prove arrival of required topic classes | Each topic's acceptance, exchange availability, silent market, topic routing, actual receiver lifecycle |
| Rejected ACK | Socket subscription response reports rejection | Per-topic scope or downstream state |
| Out-of-scope ticker observed | A ticker message is not bulk trade/orderbook/liquidation evidence | Bulk-socket continuity or inserts |
| No local frames | In a bounded observation, cannot prove events were sent or received elsewhere | Network, subscription, parsing, exchange, disconnect, flow control |

The actual `StageB1BulkObserver` already counts socket-level
subscription replies; this rehearsal does **not** upgrade its counts
to per-topic acceptance proof. Bybit `subscribe` response flags are
not proof that each requested `orderbook.50.BTCUSDT`,
`publicTrade.BTCUSDT` or `allLiquidation.BTCUSDT` feed subsequently
delivered frames. The runner does not expose external production
network telemetry; a fake frame observation is not a genuine event.

Critically, even an authentically successful **wire-only** diagnostic
could **not** determine whether a packet vanished at enqueue, writer,
transaction commit or dedupe. Therefore it cannot by itself close the
open Bybit-linear insertion/root-cause investigation. The existing
pipeline-instrumented observer is still dormant and unapproved.

## Local offline exercise

```sh
python -m pytest -q tests/recorder/test_stage_b1_inert_wire_only.py
python scripts/rehearse_stage_b1_inert_wire_only.py --scenario bulk_all
python scripts/rehearse_stage_b1_inert_wire_only.py --scenario ack_only
python scripts/rehearse_stage_b1_inert_wire_only.py --scenario parse_failure
python scripts/rehearse_stage_b1_inert_wire_only.py --scenario ticker_excluded
python scripts/rehearse_stage_b1_inert_wire_only.py --scenario subscribe_rejected
```

No CLI accepts a URL, host address, source file, live source,
credentials, database, arbitrary command, outbound topic list, output
path or runtime duration. The fixed non-network connector cannot
contact Bybit. The synthetic runner has a 2-second **coroutine**
timeout for test hanging, not independently verified OS process
termination. CI only exercises this fixed fake and checks reports do
not leak private-looking fixture values. No real production data is
ingested or persistently stored.

## Go/no-go interpretation

**NO GO** for any production activation. No approved operator
implementation or network target exists; no actual cgroup deadline,
service restore mechanism, independent host identity, approved
telemetry, evidence retrieval or real Bybit subscription test exists.
The exact **17-gate lifecycle/restoration contract** remains intact
and unfulfilled. A future **separately approved** narrow wire-only
probe may help rule in or out upstream wire/parser conditions on its
own socket, but it would still not substitute for actual
per-class DB insertion evidence in the production recorder.

The cleanest current technical tradeoff is clear: the fake wire-only
option demonstrates absence of DB side effects in this **isolated
fixture**, but sacrifices queue/write/commit visibility. A full
instrumented recorder might provide more information but requires
far more invasive lifecycle and restoration guarantees, and may not
be executed under current authorization. Neither option justifies
changing Phase 14, economic acceptance or live-trading status.
