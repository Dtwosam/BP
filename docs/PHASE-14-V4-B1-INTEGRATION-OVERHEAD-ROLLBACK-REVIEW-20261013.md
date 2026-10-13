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

## CI synthetic result (not an approval gate)

PR #605 head `335a2d7d753eb14f9380a1f2dfbc15b9a0d57b07`, CI run [38400492344](https://github.com/Dtwosam/BP/actions/runs/38400492344): 2,000 iterations per repeat, five repeats, baseline median **25,965 ns/cycle**, wrapped median **34,797 ns/cycle**, +8,832 ns (**+34.02%**) with a real parser and no-op queue/writer sinks. All four workflow groups passed, 2,527 tests with four warnings. This measurement does not cover actual producer rates, PostgreSQL or production resource and latency impact. Provisional overhead and rollback gates below remain unaccepted.

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

## Exact offline source patch rehearsal (new, not a deployment)

`scripts/rehearse_stage_b1_backport.py` now pins **immutable Git source blobs** for
the previously operator-reported `a352c66ec0110925727bc40de767ee4ba981f965`
source tree and reviewed `75281f2b97f19d903f576b973bb7c2236179839e`
source tree. Its path allowlist is exactly:

- `src/bp_engine/collectors/websocket_runner.py` — component-specific incident metadata
- `src/bp_engine/recorder/service.py` — explicit dormant B1 bulk-only injection
- `src/bp_engine/recorder/stage_b1_observer.py` — new bounded aggregate-only observer

Only local Git blobs are read; the script does not fetch. A targeted CI-only
`git fetch` retrieves these two exact Git commits before the source check.
The script verifies both old/new `git blob` IDs, generates a unified patch
and SHA-256, applies it in a **disposable temporary directory**, verifies the
three target byte identities, reverse-applies it, and verifies the old byte
identities (including absence of the observer). The stdout manifest explicitly
marks production host revision **unverified** and access/activation **false**.
An optional `--write-patch <unused-offline-file>` saves the exact patch
exclusively (refuses overwrite); do not run it on the production host.
CI saves only the JSON manifest, not an installable artifact.

```sh
# Offline operator/reviewer checkout only; never on the VM without approval.
git fetch --no-tags --depth=1 origin a352c66ec0110925727bc40de767ee4ba981f965 75281f2b97f19d903f576b973bb7c2236179839e
python scripts/rehearse_stage_b1_backport.py
```

This is **source patch reversibility**, not systemd rollback, database rollback,
an on-target state check, or a working one-shot observation. It does NOT
independently prove the original `a352c66e` revision remains deployed.
It also does not import extra main changes or test the patch against the
complete frozen service environment. The production plan stays **NO GO**
until exact service/PID/venv verification, restoration rehearsal, one-shot
aggregate-only readout, representative resource/latency overhead and fresh
authorization are complete.

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

## One-shot library rehearsal candidate (NOT an operator launcher)

`src/bp_engine/recorder/stage_b1_one_shot.py` is a new **library only**;
it is never imported by `bp_engine.recorder.__main__` and cannot
autostart via an environment flag, cron, systemd or any CLI. It accepts
a **preconstructed, explicitly injected service** in tests; all tests use
fake services. `StageB1BulkObserver.remaining_seconds()` measures the
real monotonic budget left after construction; it cannot reset the
clock or extend the observer's 1–120 second recording window.

The lifecycle rehearsal waits only for the remaining counter window,
signals the service's normal stop, and allows a bounded 0–10 second
cooperative shutdown grace. After that it cancels the **Python task**
with a separate 0–2 second wait, and marks a nonterminating task
`unresolved_task`. Service failure, premature exit and forced
cancellation are **INCONCLUSIVE**. The only positive lifecycle record
`COMPLETE_AGGREGATES_ONLY` requires a full expired counter window,
cooperative service return and zero writer in-flight / unresolved
cancelled batches. It is never a claim that trading is ready, rows were
inserted, or a process was killed.

The returned **JSON string is not written or printed** by the library.
Its exporter copies only explicitly enumerated topic-group and batch
integer counters and fixed enums; unknown object keys, error messages,
raw frames, prices and payloads are discarded. It validates each group,
sets `inserted_rows_by_class_proven=false`,
`subscribe_ack_per_topic_proven=false`, and
`process_terminated_proven=false`, and refuses any report above
4,096 UTF-8 bytes. Tests cover a cooperative exit, silent early exit,
exception detail non-disclosure, forced cancellation, stubborn task,
bad counter injection and an already expired window.

**Major safety distinction:** cancelling an asyncio task **cannot kill**
an `asyncio.to_thread` DB transaction or an operating-system process.
This library has no separate-process watchdog, hard wall-time
termination, or authorized aggregate output sink. A future operator
candidate must use an independently supervised, exact runtime process
with explicit safe interruption/forced termination and verified
service restoration; those mechanisms are NOT provided here. A normal
recorder restart/stop must never be taken as permission to run this
candidate. Whether additional actual inserted-row and per-topic ACK
diagnostics are worth that deployment must be reviewed first.

## POSIX synthetic process watchdog: local kill/reap rehearsal only

The separate `scripts/rehearse_stage_b1_process_boundary.py` is
**intentionally NOT a general subprocess supervisor or operator launcher**.
It self-executes only its own fixed fake child modes
(`cooperative`, `hang`, `ignore_term`, `crash`, `poisoned`,
`oversized`). It accepts **no recorder command or external executable**.
The child runs in a new POSIX session under the same Python interpreter
with `-I -B`; standard input, output and errors are discarded.
An inherited temporary anonymous file descriptor carries only a fixed
synthetic aggregate fixture and is read up to **4,097 bytes**, failing
closed on oversized/malformed/injected content. The parent **never copies
arbitrary child output** into its JSON summary.

The parent bounds its initial wait (at most 3 seconds), sends SIGTERM
to the new child process group, waits up to 1 second, then escalates
to SIGKILL with a bounded 2-second reaping check. If cancellation or
interruption occurs, it attempts a final SIGKILL and wait in `finally`.
The output differentiates clean fake exit, TERM, KILL and an unresolved
leader and never claims all descendants have stopped. The code cannot
claim a production hard-wall deadline, safe shutdown of a live DB writer,
or actual on-host rollback; no such process is launched.

Offline CI exercises both clean exit and an uncooperative fake process;
unit tests cover crashes and attempts to smuggle secret-like data in
stderr or an output document. A good result demonstrates only the
**test subprocess leader** was reaped and a fixed fixture passed the
bounded schema check. A larger operator package would still need
a separately reviewed process tree/cgroup lifetime guarantee, retained
service health, exact on-host runtime provenance, meaningful counter
output, safe restoration, acceptable overhead, and **new authorization**.
The #607 patch hash is unchanged and does **not include** either
this test-only watchdog or the #608 one-shot library.

```sh
# Never a production or on-target command.
python scripts/rehearse_stage_b1_process_boundary.py --scenario cooperative
python scripts/rehearse_stage_b1_process_boundary.py --scenario ignore_term
pytest -q tests/recorder/test_stage_b1_process_boundary.py
```

## Offline-only exact-source preflight manifest (NOT deployment approval)

`scripts/review_stage_b1_preflight_manifest.py` accepts a bounded local JSON
file and, optionally, the exact **local source-only patch** from PR #607.
It invokes no Git fetch, network, SSH, systemd, recorder, Python service,
database or process. There are no execute/deploy/rollback subcommands.

It pins the **old, three-file, non-operational source patch** from #607,
from operator-*reported* commit `a352c66ec0110925727bc40de767ee4ba981f965`
to reviewed source commit `75281f2b97f19d903f576b973bb7c2236179839e`,
SHA-256 `e0a15b1f890233fa6b1228695170f64967b3e9c2899580951242b78ef7ea9b10`
with **20,045 bytes** and exact before/after Git blob IDs.
The old patch has **no one-shot activation or OS-process supervisor**.
It cannot be used to obtain a Stage B1 collection as-is, and **must not**
be re-described as covering the later #608/#609 modules.

A strictly allowlisted JSON template is stored at
`docs/evidence/phase14-b1-INCOMPLETE-offline-preflight-template-20261013.json`.
Its on-target identity, runtime, zero-money, rollback material, actual
restore rehearsal, output contract, independent process isolation and
overhead evidence fields are **intentionally all null**. The verifier:
- rejects unexpected/missing fields, source file reordering, any source hash
  mismatch, an implied one-shot launcher, malformed hash/UTC/PID,
  unsafe research/money/credentials conditions, absent rollback or service
  health assertions, durations above 120 seconds, or any permitted SQL count
  other than **zero**;
- can optionally hash the exact local source patch with a bounded 64KiB
  read and fixed length/digest equality; **without bytes this check fails**;
- never echoes supplied host values, paths, raw input or errors. Only
  fixed check names/booleans and canned caveats are returned.
- always emits `PRODUCTION_NO_GO` semantics: incomplete
  `INCOMPLETE_OR_MISMATCHED_NO_GO` (CLI exit 2) or complete-but-
  *self-attested* `STRUCTURE_COMPLETE_UNVERIFIED_NO_GO` (CLI exit 0
  for schema review **only**). Both explicitly set
  `production_execution_authorized=false`,
  `host_claims_independently_verified=false`, and
  `production_deploy_or_restart_permitted=false`.

This is a **review checklist**, not an independent on-host attestation.
An operator could falsely fill every field with valid-looking digests;
the output is still NO GO. No user authorization, credential access
assessment, operational approval, full process-tree proof, or representative
host overhead is conferred by this script. Do not place secrets, actual
environment file contents or private keys in a manifest or in chat.

```sh
# CI / private non-production checkout only. A failed/blocked exit 2 is EXPECTED.
python scripts/review_stage_b1_preflight_manifest.py \
  --manifest docs/evidence/phase14-b1-INCOMPLETE-offline-preflight-template-20261013.json
```

The current source patch identity is **fixed and dormant**. Before requesting
production activation, any usable one-shot package needs a separately
reviewed new candidate+rollback SHA/patch, independent host evidence and
fresh specific permission, not merely this source-only manifest.

## Reproducible four-source inert review bundle (offline only)

The next optional review artifact is built by
`scripts/rehearse_stage_b1_review_bundle.py`. It reads **only local**
Git objects at `a352c66ec0110925727bc40de767ee4ba981f965` (the
previously **operator-reported**, currently **not on-host reverified**
source baseline) and reviewed `347db08ee47d113f35fd6a9bc0e4bd85610aea0d`
(pre-this-work, immutable checkout). It fixes old and new `git blob`
hashes for **four** Python paths: `websocket_runner.py`,
`service.py`, `stage_b1_observer.py` and
`stage_b1_one_shot.py`. It deliberately excludes the synthetic-only
POSIX fake-process watchdog and host preflight scripts from the runtime
source set.

The script verifies blob identities, builds a deterministic unified
**source-only** patch, validates forward apply and byte-identical
reverse apply against an **isolated temporary source directory**,
then constructs a deterministic ZIP with the four reviewed files, patch,
fixed-schema source manifest and mandatory `B1-NO-GO-READ-ME.txt`.
The ZIP is <=256KiB, uses fixed entries and timestamps, and can be
verified offline **without extraction or execution**. By default only
a summary JSON of patch/bundle hashes and checks is printed. Writing
actual source ZIP bytes requires the explicit
`--write-review-only-zip <new-local-file>` argument (exclusive-create);
CI does **not** write or upload the ZIP.

```sh
# Only a non-production/offline checkout, after the pinned Git objects exist.
python scripts/rehearse_stage_b1_review_bundle.py
# Optional separate offline reviewer artifact; not an installation package.
python scripts/rehearse_stage_b1_review_bundle.py --write-review-only-zip ./B1-REVIEW-ONLY.zip
python scripts/rehearse_stage_b1_review_bundle.py --verify-review-only-zip ./B1-REVIEW-ONLY.zip
```

**This ZIP is inert.** The `one_shot.py` library has no operator entry
point or approved aggregate output sink; `run_recorder()` does not
inject it. The archive cannot run a real Bybit diagnostic, terminate
a live recorder process tree, establish host runtime or resource
overhead, or restore the service. Source reversibility does NOT mean
systemd/venv/PostgreSQL rollback, and no current on-host identity is
asserted. The original #607 source-only patch SHA remains a separate
immutable record; this newer four-path review packet is not a retroactive
approval or updated installation instruction. A genuine operational
package would need more engineering, independent host verification,
rehearsed service restoration, representative load/latency measurements,
and specific renewed authorization. **Phase 14 HOLD.**

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
