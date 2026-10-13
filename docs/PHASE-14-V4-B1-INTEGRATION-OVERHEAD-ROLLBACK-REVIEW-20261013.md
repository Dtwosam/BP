# Phase 14 B1 — inactive injection / overhead / rollout review

**13 October 2026. Status: GitHub-only preparation; NOT an approved deployment.**
Tracking: [#589](https://github.com/Dtwosam/BP/issues/589). Baseline verified
`main=74583e5be82517dc2e00ba4bf9f1f534e7e3f0e7` before this branch.

## Scope of this review branch

An **explicit dependency-injection parameter** on
`build_default_recorder_service(settings, *, stage_b1_observer=None)` is
the only activation seam. `run_recorder()` continues to invoke the ordinary
builder with **no observer**. There is **no environment switch, flag,
scheduler, report publisher, network probe or recorder service component**.
Importing or running the normal recorder does not instantiate an observer.

With a specifically injected observer, exactly the existing
`bybit_linear` **bulk** runner receives the wrapper for its Bybit parser
and routed enqueue sink. The existing **bulk** writer (or the single writer
if no priority writer exists) receives the post-return wrapper around
`_DatabaseSink.write_events`. The priority ticker writer, separate
Bybit-linear ticker socket, all Bybit-spot sockets, Coinbase and Polymarket
remain unwrapped. In a one-writer configuration ticker events sharing the
writer are explicitly excluded from B1 category counters. Normal buffer,
dedupe, transaction and feed-policy behavior is unchanged. This is tested
offline with both one and three writer workers.

An explicitly injected observer **fails before database creation** unless
settings remain `MODE=research`,
`LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`,
`MAX_DAILY_LOSS_USD=0`, and `MAX_TOTAL_EXPOSURE_USD=0`.
These are necessary but not sufficient guards. They do not independently
prove there is no wallet environment material, no auto-promotion path, or
that the exact production service/VM settings match. Those remain manual
preflight checks before separate authorization.

## Accounting boundaries that cannot be overstated

- `wire_messages_by_topic_class` counts only matching messages passed
  through the runner's parser, not all bytes/frames on the socket.
  ACK success is **socket-level**, not guaranteed topic-level acceptance.
- `successfully_enqueued_events_by_class` increments only after the
  routed sink returns; a cancelled blocked queue operation may have
  ambiguous final outcome, and queue depth is not sampled.
- `events_in_successfully_returned_transactions_by_class` counts
  **attempts**, NOT individually inserted rows. Mixed-source transactions
  and dedupe mean insertion may be zero even when the batch returns.
  The existing `RecorderRepository.insert_events` returns only a
  batch-wide count; the per-class inserted-row discriminator is **not
  implemented** by this branch.
- Cancellation of an `asyncio.to_thread` DB sink **does not prove its
  thread or transaction stopped**. B1 now counts such cases as
  `writer_batches_cancelled_unresolved`, preserves the unresolved
  in-flight accounting and must not assert a rollback.
- The 1–120-second counter collection window bounds new counts. The
  wrappers would still be present after expiration if a future
  instrumented recorder kept running; a controlled restoration of
  normal code would be needed to remove all residual wrapper overhead.
- Observer `snapshot()` is an in-memory aggregate only, with no export
  mechanism. **There is not yet an approved one-shot production output
  path**; do not deploy hoping to recover results later from logs.

## Offline overhead method (not a production benchmark)

Run only on a non-production checkout with synthetic data:

```sh
python scripts/benchmark_stage_b1_observer.py --iterations 2000 --repeats 5
pytest -q tests/recorder/test_stage_b1_observer.py
```

The benchmark alternates baseline and wrapped paths using the real
Bybit-linear parser plus **no-op in-memory enqueue and writer sinks**.
It emits median nanoseconds per parser→enqueue→writer cycle and absolute
and relative differences, with an explicit synthetic-only disclaimer.
The 120-second in-memory counting limit and argument bounds apply; any
expired window is marked inconclusive. This is a rough measurement of
Python wrapper overhead, **not** a live VM benchmark: it omits queue
contention, cross-feed traffic, PostgreSQL and actual commits, reconnects,
disk, process CPU or memory. Do not promote a favorable number into
"production safe" without representative measurements. Do not invent
an overhead result if this script has not run on a real checkout.

**Proposed review/abort limits, NOT empirically accepted thresholds:**
measure baseline and instrumented recorder process CPU, RSS, queue-depth
and enqueue/writer p95 delay under representative load, on the same
host/runtime only after separate authorization. Suggested initial
hard aborts: *any* new writer exception/cancellation, any violation of
research/zero-money gates, any unexpected systemd restart or live-order
capability, recorder not recovering to baseline feed health, sustained
CPU over 10% above the preflight baseline, RSS increase over 64 MiB,
or p95 enqueue/writer latency over 10% above baseline. If baselines,
instrumentation or available host counters cannot measure those conditions
reliably, **NO GO** rather than waiving checks. Instrumentation should not
be activated merely to establish the initial baseline without permission.

## Minimum backport and exact deployment identity: not ready

The *previously operator-reported* deployed checkout was
`a352c66ec0110925727bc40de767ee4ba981f965`; this has **not**
been re-checked on the VM in this work. Do not `git pull main` into
`/opt/bp`. Before requesting permission, a reviewer/operator must
generate a **minimal source-only patch from that verified deployed tree**
for the incident component label (#603), dormant B1 observer (#604)
and this branch's opt-in assembly path, accounting for any differences
between the old deployed `service.py` and new main. On an isolated
checkout, test applying that exact patch to the frozen deployed commit.
Record the complete candidate SHA, patch SHA-256, affected path/blob
SHA-256, installed Python/venv identity, service/unit name, service
PID/start/restart counters and the exact previous deployed tree/rollback
checksum. These are prerequisites, **not statements of current host
state**.

## Separate authorization and one-shot control design

A fresh specific user authorization would have to identify the *exact*
candidate/backport hash, production VM and recorder unit, any staging,
whether a recorder restart is permitted, one **maximum 120-second**
counting period, any baseline process-observation allowance, the
aggregate-only output destination/format, allowed counters, permitted
queries (default **zero**), stop/rollback owner, maximum wall time
and explicit stop criteria. The previously approved Stage A/B0 probes
are consumed and authorize none of these steps.

1. **Preflight before mutation:** verify exact target hash/runtime,
   `MODE=research`, every zero-money gate, live kill switch/order paths,
   no auto-promotion, absence of accessible wallet credentials, unchanged
   frozen V4 model/source-age policies, healthy recorder/storage state,
   and rollback material. Fail closed on any ambiguity; do not read a
   consumed holdout or send private keys into a report.
2. **Stage only the approved patch**, never a whole-main fast-forward.
   The approved one-shot launcher must be reviewed separately to inject
   the observer, mark the monotonic window, emit only a single sanitized
   snapshot after the window, and preserve the existing health/signal
   handling. No such launcher is provided by this branch. A service
   restart must be explicitly authorized; passive deployment does not
   permit restart or observation.
3. **Monitor and abort:** immediately restore the pre-patch service if
   any safety gate or agreed overhead/health limit fails. Never infer
   loss from a count delta until in-flight/cancelled writes and dedupe
   have been reconciled. No live/paper shadow, source-policy changes,
   additional SQL or third-party probe is bundled in B1.
4. **Finish and restore:** after the authorized finite counting window,
   collect only the approved aggregate; remove the injected code from
   the running path using the **exact recorded pre-deployment revision**,
   verify recorder health and PID/restart count and compare a bounded
   read-only health snapshot if explicitly authorized. If rollback fails,
   halt/alert and leave trading disabled. Preserve only sanitized
   evidence with scope, time window, candidate/rollback checksums and
   uncertainty flags.

**Open approval blockers:** real per-class post-commit insert attribution
(if required to answer storage loss), authoritative per-topic subscribe
ACK mapping, queue-depth/backpressure and latency sampling design,
one-shot activation/snapshot export, actual representative overhead
results and explicit backport/restore rehearsal. No current evidence
identifies the actual production fault or the October 7–8 cause.
**Phase 14 HOLD; economic acceptance NOT MET; live trading disabled.**
