# Phase 14 B1 — proposed lifecycle and restoration acceptance contract

**14 October 2026 · REVIEW ONLY · production NO GO · Issue #589**

This is a proposed evidence-and-decision contract for diagnosing the
unresolved Bybit-linear **bulk** ingestion failure. It is **not** an
operator playbook, approved change ticket, executable service procedure,
deployment artifact or permission for production queries. It introduces
no recorder activation, process controller, systemd action, PostgreSQL
access or live trading capability. Prior Stage A/B0 approvals are
exhausted. Phase 14 remains **HOLD**, economic acceptance **NOT MET**,
live trading disabled.

This contract complements—not supersedes—the existing
[Stage B1 integration/overhead/rollback review](PHASE-14-V4-B1-INTEGRATION-OVERHEAD-ROLLBACK-REVIEW-20261013.md),
offline source-only
`scripts/review_stage_b1_preflight_manifest.py`, synthetic process
boundary (#619/#620), fake DB-thread witness (#621) and fake independent
restoration read-back (#622). Those artifacts **do not** attest present
VM identity, independent process quiescence or real service recovery.

## Decision states and separation of roles

- **NO GO — insufficient evidence:** default. Missing, stale, conflicting
  or solely self-reported conditions never imply authorization.
- **REVIEWABLE PROPOSAL ONLY:** a reviewer can inspect a fully described
  candidate, evidence origins, abort plan and rollback material. This is
  still **NO GO** until a new, specific human authorization boundary is
  cleared.
- **AUTHORIZED LATER (not implemented here):** only a separately approved
  target, exact code/rollback digests, specific operations, observation
  duration, stop owner and narrow output can be executed by a **future**
  reviewed operator mechanism. Nothing in this branch creates one.
- **POST-RESTORE REVIEW (future only):** the diagnosis cannot be labeled
  safely closed by a launcher exit, successful counter report or
  self-reported restore ACK. External read-back and independent receipt
  review are required. A failed/unknown restore is an **incident and NO GO**,
  not an invitation to keep restarting or collecting.

Required independent roles (may not merely be the launcher reporting on
itself): **authorization owner**, **operational stop/restore owner**,
**separate runtime/service observer**, **database-worker/transaction
safety reviewer**, **resource/health reviewer**, and **evidence custodian**.
A person may hold more than one administrative title only if independent
evidence still originates outside the running diagnostic. No existing
appointment or consent is inferred.

## Proposed strictly bounded operation, if ever separately authorized

Target only the Bybit-linear **bulk** socket diagnostics, not ticker,
Polymarket, Coinbase, Bybit spot, model inference, trading or existing
maintenance. The current observer can count wire messages reaching its
parser, parsed events, successful enqueues, writer attempts and attempts
in successfully returned transactions. A returned batch does **not**
prove insertion because duplicates may be skipped. Socket-level ACKs
do **not** establish per-topic acceptance. The current source-only
candidate is dormant and has no approved one-shot exporter or independent
real-process watchdog: **do not deploy it to obtain data**.

Proposed counting budget: at most **120 seconds**, measured by an
independent monotonic supervisor after setup. This is a *counter* upper
bound, **not** a hard OS process-lifetime guarantee. The independent
wall-time bound, cleanup grace, maximum restarts, systemd/cgroup boundary,
SIGTERM/SIGKILL policy and disallowed operations must be specified and
approved separately before host contact. There is **no authorization**
for new production SQL (`database_queries_allowed=0`), raw frames,
prices, credentials, wallet access, environment dumps, persistent raw
payloads, full production checkout replacement or service mutation.
An immutable bounded **aggregate-only** output contract and retrieval
path must be individually reviewed before execution. Do not copy secrets
or host identifiers into issue comments/CI artifacts.

## Evidence gates (fixed labels in the offline template)

| Phase / gate | Proposed independent evidence source and reviewer | Stop/NO-GO if |
|---|---|---|
| Before: `fresh_exact_scope_authorization` | Authorization owner: fresh signed scope bound to candidate digest, host identity, permitted operation and expiration | Absent, expired or inherited Stage A/B0 approval |
| Before: `independent_operator_and_stop_owner` | Named operations owner and separate stop authority; approved emergency path | Missing owner, no escalation or stop permission |
| Before: `current_host_service_venv_code_identity` | Read-only host/service inventory, immutable deployed tree/unit/venv digests and active PID/start/restart state collected under separate consent | Based on old operator-reported `a352c66e` only, or identity mismatch |
| Before: `research_zero_money_no_wallet_auto_promotion` | Security/config reviewer, allowlisted non-secret attestation of research, no wallet, no promotion, zero-money policy | Unknown values, live/orders enabled, nonzero budget, credentials accessible |
| Before: `exact_candidate_and_rollback_material` | Source/change reviewer, exact forward/reverse digests, unchanged runtime baseline, locally reproduced source reverse | Unpinned blobs, overbroad patch or missing restoration material |
| Before: `independently_evidenced_restoration_rehearsal` | Stop/restore owner plus separate witness, faithful approved rehearsal under safe, isolated conditions | Only an offline Git patch reversal, ACK or test fixture is available |
| Before: `external_process_group_cgroup_hard_timeout_design` | Process isolation reviewer: verified whole boundary, deadlines, escaped-descendant policy, cancellation and forced termination | Task cancellation or single PID reaping offered as total cleanup |
| Before: `db_worker_transaction_quiescence_design` | DB safety reviewer: exact fate of outstanding `asyncio.to_thread` writes, connection/transaction fence and uncertainty policy | Cancelled coroutine treated as rollback, active worker cannot be accounted |
| Before: `bounded_aggregate_only_output_and_zero_sql` | Output/security reviewer: allowlisted metrics, size cap, independent retention, no raw values or new SQL | Unapproved exporter, arbitrary logging, absent counters, unknown query path |
| Before: `same_host_baseline_cpu_rss_queue_writer_p95` | Resource reviewer: contemporaneous *unmodified* recorder baseline from separately authorized telemetry | Baseline absent, stale, synthetic or on different host/workload |
| Before: `baseline_service_feed_and_storage_health` | Independent health witness: systemd, recorder feed continuity, queue/writer and DB/storage health through already authorized signals | Recorder already unhealthy or health source unverifiable |
| During: `during_run_independent_deadline_and_abort_watch` | External supervisor owner: monotonic deadline and external CPU/RSS/queue/writer/health abort readouts, no self-report dependence | Any watch fails, counters or cleanup unresolved, scope exceeded |
| After stop: `after_stop_process_tree_quiescent` | Separate OS/cgroup observer: verified diagnostic tree empty, all descendants accounted, no restart race | Leader reaped but orphan remains, isolation unknown, probe unavailable |
| After stop: `after_stop_db_workers_transactions_resolved` | DB safety witness: no outstanding diagnostic writes/threads/connections or unknown late commits | Cancellation-only evidence or unknown transaction outcome |
| Restore: `source_unit_venv_and_expected_restart_restored` | Independent post-restore identity read-back of unit, code, venv, intended service state and recorded restart delta | Restore ACK alone, source/unit drift, surprise restart, stale read-back |
| Restore: `after_restore_feed_writer_queue_storage_health` | Independent post-restore fresh telemetry of Bybit bulk plus unaffected feeds, queue/writer, connection and storage-health continuity | Feed still stale, queue/write stalled, DB state uncertain, source excluded |
| Close: `immutable_sanitized_rollback_and_review_receipts` | Evidence custodian: separately signed/timestamped before/after snapshots, stop/restore receipt, bounded aggregates | Incomplete receipts, raw secrets, untrusted timestamps or mismatched attempt IDs |

**Temporal rule:** future after-stop/restore gates cannot be satisfied
*in advance*. Every stage is a separate acceptance barrier. A reviewer
must reject evidence collected before the corresponding action or from
the same unverified self-report that initiated it. Lack of an authorized
way to obtain evidence is a blocker, **not** evidence of a passed gate.

## Hard abort and restoration rules for future review

Abort proposed observation immediately (subject to an independently
enforced stop that itself has been reviewed) upon: expired deadline,
unexpected process tree, unresponsive stop owner, lost read-back,
live-trading/zero-money violation, unplanned SQL or output, unexpected
unit/process restart, any new writer exception/cancellation/unresolved
transaction, worsening stale-feed or write backlog, or sustained
overhead beyond agreed thresholds. The current review proposes—but has
**not accepted or verified**—CPU above baseline by over 10%, RSS delta
above 64 MiB, or p95 enqueue/writer latency above baseline by more than
10%; unknown baseline/measurement is an immediate **NO GO**, not a
waiver. Lower thresholds may be required after evaluation.

Restoration must be external to the instrumented recorder's own
event loop, bound to the independently verified **previous** source,
unit and interpreter, and independently checked after any interruption,
timeout, exception, cancellation or diagnostic result. A successful
counter snapshot, process leader exit, or `systemctl` success message
would not suffice. The independent witness must recheck actual
process/cgroup membership, residual DB worker/transaction ambiguity,
source/unit/venv fingerprint, permitted restart delta and feed/storage
health. If any item is unverified, record an incident, preserve
sanitized evidence and escalate to the human stop owner under a
separately authorized recovery procedure; do not endlessly retry,
silently waive it or label the operation restored.

No future diagnostic should be attempted if the current production
recorder cannot be restored without a broad deployment, if real
DB-worker quiescence remains unobservable, or if an approved report
cannot be retrieved safely. A minimum-downtime goal is not itself a
verified stop guarantee.

## Offline-only review checker

The fixed incomplete claim template is
`docs/evidence/phase14-b1-INCOMPLETE-lifecycle-review-template-20261014.json`.
All **17** claims are deliberately null. The offline checker reads only
a bounded local JSON file; it contains no launch, query, collect, kill,
restart, deploy, rollback or network code:

```sh
python scripts/review_stage_b1_lifecycle_contract.py \
  --manifest docs/evidence/phase14-b1-INCOMPLETE-lifecycle-review-template-20261014.json
```

It accepts exactly the fixed proposed scope; per-gate values may only
be null or the literal `claimed_reviewed`. That literal is an
**unverified assertion**, not a reviewer signature or an evidence
locator. Unknown fields, altered scope, bad statuses and any missing
gate fail closed. It returns `INCOMPLETE_OR_INVALID_NO_GO`, or
`CLAIMS_FORMAT_COMPLETE_UNVERIFIED_NO_GO` if *every* self-reported
claim is present. **Both exit code 2, both always deny** any host
operation, and the report echoes no user-supplied values. A separate
future evidence mechanism would be needed to prove real independent
conditions; this linter intentionally does **not** pretend to do that.

## Review conclusion — 14 October 2026

**NO GO.** There is no fresh production runtime attestation, complete
approved operator candidate/output path, real independent stop and DB
worker quiescence mechanism, representative overhead acceptance,
or independently verified restore/health evidence. The Bybit-linear
failure root cause remains **UNKNOWN**. Reassess this contract with
exact candidate details and a new, narrowly scoped human authorization
**before** any production host/service/database action. Do not change
Phase 14 economics or activate live trading on the basis of this
document or the synthetic rehearsals.
