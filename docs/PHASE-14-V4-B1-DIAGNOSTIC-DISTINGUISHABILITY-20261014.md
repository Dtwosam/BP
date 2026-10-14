# Phase 14 Stage B1 — diagnostic distinguishability decision

**14 October 2026 · Issue #589 · synthetic proof / production NO GO**

## Decision

**Do not present a separately connected wire-only probe as a root-cause
diagnostic for the existing recorder.** It may show that its **own**
socket can receive and parse Bybit-linear bulk frames in a proposed,
separately authorized experiment, but it does not establish that the
production socket received the same frames, let alone that they were
enqueued, committed or inserted. The fixed in-memory runner seam from
[#625](https://github.com/Dtwosam/BP/pull/625) demonstrates only
technical composition of parser and fake socket, not real market
observation.

A dormant B1 observer on the **same recorder path** could narrow
hypotheses across wire, parser, enqueue and transaction-return
boundaries, but **successful return counts attempted events, not inserted
rows**; dedupe can yield zero inserts. True per-class insertion
attribution would need independently verified, after-commit same-batch
receipts; the existing separate local-only opt-in PostgreSQL test
method is **not** deployed. No existing option has the independent
termination/restoration, host identity, overhead or output safety
required by the [17-gate lifecycle
contract](PHASE-14-V4-B1-OPERATOR-LIFECYCLE-RESTORE-CONTRACT-20261014.md).

## Counterexample: six synthetic worlds, one identical independent probe

The bounded offline script
`scripts/review_stage_b1_observability_equivalence.py` defines six
explicitly **hypothetical** states. Each state assumes an entirely
independent probe received and parsed one trade, one orderbook and one
liquidation frame, plus a positive **socket-level** ACK. The actual
production-recorder stage counters differ as follows:

| Fixed hypothetical production-recorder state | Same-recorder bulk wire | Same-recorder parsed | Same-recorder enqueued | Attempted events in returned DB transactions | Independently verified inserted rows (hypothetical) |
|---|---:|---:|---:|---:|---:|
| No bulk frames received | 0 | 0 | 0 | 0 | 0 |
| Bulk frames but no parsed events | 3 | 0 | 0 | 0 | 0 |
| Parsed events but no completed enqueue | 3 | 3 | 0 | 0 | 0 |
| Enqueued but no successful transaction return | 3 | 3 | 3 | 0 | 0 |
| Transactions returned, every attempt deduplicated | 3 | 3 | 3 | 3 | 0 |
| Transactions returned and new rows inserted | 3 | 3 | 3 | 3 | 3 |

All numbers are **fabricated fixed test constants**, not samples,
actual DB rows, live Bybit ACKs or the current production baseline.
They illustrate **possible observational ambiguity**, not a
comprehensive fault taxonomy or a claim that these patterns identify
a unique underlying cause. Even the label "no bulk frames received"
describes a counter state in a window, not proof that the exchange
published frames or that a connection was healthy.

The comparison groups worlds by which of their synthetic counters a
view would reveal:

| Observation view | Identical-hypothesis pairs | Distinguishable groups | Critical gap |
|---|---:|---:|---|
| Separate wire-only socket | **15/15** | **1** | Its own successful samples tell nothing about the production recorder |
| Dormant observer on the same recorder | **1/15** | **5** | Cannot distinguish all-deduped attempts from inserted rows |
| Same-recorder counters **plus hypothetical authenticated after-commit inserted-row receipt** | **0/15** | **6** | Requires as-yet unapproved actual receipt, authentic same-batch correlation and lifecycle guarantees |

This is not a probability, sensitivity/specificity score, diagnostic
accuracy estimate or production performance benchmark. The partition
merely demonstrates a *counterexample*: identical independent-probe
observations can coexist with different failures in the actual
recorder. More counters refine the simplified partition but still
do not prove a unique cause, especially with overlapping windows,
different source sockets, async queues, batches, retries, dedupe and
unresolved threads.

An apparently successful socket ACK is **not per-topic acceptance**.
A zero wire count is not necessarily a feed fault. A positive parsed
count on the independent probe is not a positive parsed count on the
production recorder. A returned transaction count is not an
inserted-row count. Cancellation of the awaiting DB coroutine does
not prove thread termination or rollback.

## What to prioritize next (design only)

The narrow wire-only option might be useful for a **separate question**:
can a distinct, independently managed diagnostic socket receive
supported bulk Bybit topics at that time? It is **insufficient as the
standalone investigation of #589**, which concerns missing or
ineligible actual recorder observations.

A prospective root-cause investigation should prioritize **genuinely
same-recorder, matched-window evidence** for subscription/socket
receipt, parser, enqueue, writer attempt/return and reliable
postcommit per-class insert receipts. It must not smuggle a second full
recorder or a source/schema mutation into production. The current
dormant B1 observer can cover some of those stages **only after**
independent host identity, CPU/RSS/queue/writer overhead, bounded
aggregate-only report, whole-process/cgroup kill and DB-worker
quiescence, faithful service restoration and a **new explicit
authorization** have all been resolved.

If those stop/restoration and authorization conditions cannot be
satisfied, the outcome remains **NO GO / root cause UNKNOWN**,
rather than selecting a weaker observation as a substitute.

## Offline test and output contract

```sh
python -m pytest -q tests/recorder/test_stage_b1_observability_equivalence.py
python scripts/review_stage_b1_observability_equivalence.py --view separate_wire_only
python scripts/review_stage_b1_observability_equivalence.py --view same_recorder_dormant_b1
python scripts/review_stage_b1_observability_equivalence.py --view same_recorder_plus_verified_postcommit
```

The CLI accepts only the three fixed view identifiers. It creates no
sockets, data files, database engines, threads or processes and reads
no input data or current settings. Output contains only fixed world
labels, aggregate equivalence-class counts, hardcoded caveats and
`FIXED_SYNTHETIC_DECISION_REVIEW_NO_GO`; it always sets production
authorization, real-root-cause identification and actual host/restore
verification to **false**. CI checks all three partitions and the
fail-closed interpretation. No production evidence is generated.

**Status remains unchanged:** Issue #589 OPEN; Bybit-linear production
root cause UNKNOWN; Phase 14 HOLD; economic acceptance NOT MET; live
trading disabled. This decision is not authorization to contact the
VM, exchange, service, production database or wallet, modify the
recorder, deploy a launcher, query real SQL or begin shadow/trading.
