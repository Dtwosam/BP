# Phase 14 Stage B1 — postcommit receipt delivery reliability review

**14 October 2026 · fixed synthetic only · production NO GO**

## Confirmed repo behavior and open engineering gap

The existing `insert_same_batch_attributed_offline()` helper in
`src/bp_engine/recorder/stage_b1_insert_attribution.py` is limited to
a **local test PostgreSQL instance** and preserves the repository's
existing partitioned dedupe-claim/raw-insert SQL shape. It invokes an
optional synchronous callback **after** successful transaction commit,
passing a read-only `Mapping[str, int]` of per-class inserted counts.
The callback is not active in the deployed recorder. An exception in
the callback becomes `FAILED_AFTER_COMMIT`; the database commit
**cannot be undone by that notification failure**.

This currently implemented callback has **no batch/transaction
correlation identifier, no durable delivery acknowledgement, no
exactly-once guarantee and no independently authenticated host
transaction proof**. Thus even a future same-recorder instrumentation
design cannot treat a missing callback as an unsuccessful commit, or
sum duplicate callbacks as newly inserted rows.

## In-memory failure cases

`scripts/rehearse_stage_b1_fake_receipt_delivery.py` uses fixed
in-memory timestamps, fake batch identities and fake class counts.
Every scenario assumes a *fabricated* transaction with two attempted
trades and one inserted row, for illustration only.

| Fixture | Illustrated mistake | Necessary design rule |
|---|---|---|
| `normal` | A callback-shaped notification appears after a fake commit | This alone is still not an authentic commit receipt |
| `lost_callback` | Zero observed callbacks despite a fake completed commit | Absence of callback must remain **unknown**, not zero insertion |
| `duplicate_callback` | Two identical deliveries incorrectly sum one fake inserted row as two | Stable trustworthy batch identity and idempotent dedupe are necessary |
| `conflicting_retry` | Reused fake batch identity carries conflicting counts | Fail closed; never silently accept first/last conflicting value |
| `before_commit` | Notification delivered before fake commit boundary | Reject uncommitted or prematurely disclosed receipt |
| `late_after_readout` | Callback arrives after the observation-window readout | Time-window close cannot equate to DB-worker/receipt completion |
| `cancelled_await_late_commit` | Awaiting coroutine cancellation precedes fake commit and notification | Cancellation cannot prove SQL rollback or DB thread termination |

The fake batch IDs exist **only** in the model so that the
idempotence problem can be demonstrated. The present local callback
cannot provide those IDs. The model creates **no real transaction,
network connection, recorder service, database thread, receipt sink,
file, host read-back or persistent output**; its JSON consists solely
of allowlisted fixture aggregate integers and fixed explanatory text.

All scenarios explicitly return an enum ending in `NO_GO`;
even the `normal` scenario never claims actual insertion,
production authorization, OS lifetime proof or recovery. The numbers
are not measured production rows or a root-cause probability.

## Before any future same-recorder postcommit implementation

A new proposal would have to bind each receipt to the **correct
recorder instance, committed transaction/batch and measurement window**,
prevent disclosure before commit, track lost/late/conflicting
delivery, deduplicate retries without counting a second insert,
and reconcile stop/cancellation with outstanding
`asyncio.to_thread` work. It must decide how receipt identities are
created and protected without exposing dedupe keys, raw events or
credentials. Durable exactly-once notification cannot be assumed from
an in-process synchronous callback; independent verification and
back-pressure/overhead effects require separately reviewed design.

These requirements are **additional to**, not substitutes for, the
existing [17-gate independent lifecycle/restoration contract](PHASE-14-V4-B1-OPERATOR-LIFECYCLE-RESTORE-CONTRACT-20261014.md).
No on-host identity proof, representative CPU/RSS/queue/writer p95
or independent restoration is supplied. This change does **not**
recommend enabling source patches, querying the live DB or launching
a parallel recorder.

## Offline verification

```sh
python -m pytest -q tests/recorder/test_stage_b1_fake_receipt_delivery.py
python scripts/rehearse_stage_b1_fake_receipt_delivery.py --scenario normal
python scripts/rehearse_stage_b1_fake_receipt_delivery.py --scenario lost_callback
python scripts/rehearse_stage_b1_fake_receipt_delivery.py --scenario duplicate_callback
python scripts/rehearse_stage_b1_fake_receipt_delivery.py --scenario conflicting_retry
python scripts/rehearse_stage_b1_fake_receipt_delivery.py --scenario before_commit
python scripts/rehearse_stage_b1_fake_receipt_delivery.py --scenario late_after_readout
python scripts/rehearse_stage_b1_fake_receipt_delivery.py --scenario cancelled_await_late_commit
```

CI exercises the fixed scenarios, ensures the normal report is still
NO GO and checks that the lost, repeated, premature, late and
cancellation cases never become claims of real inserted rows.

## New real local PostgreSQL concurrency proof (nonproduction)

A follow-up regression now exercises the **existing**
`insert_same_batch_attributed_offline()` helper against the CI-owned
partitioned PostgreSQL fixture with two genuine concurrent Python
writers that start together. Both submit the **identical** four-event
batch (two copies of a trade, one book, one liquidation). The intended
and asserted SQL outcome is **three inserted rows total** and three
dedupe claims, not six, even though both transactions return normally
from the local helper. Per-class inserted counts across both
transactions sum to exactly one trade, one book and one liquidation;
the losing writer's callback still executes after its zero-insertion
commit. These are real **local test transactions**, not the production
database.

One callback deliberately raises after seeing committed local rows.
Its report marks `FAILED_AFTER_COMMIT` while the other callback is
`DELIVERED`; both the raw and dedupe tables retain their three
committed rows. A separate connection inside each callback checks
committed-row visibility, callback maps are immutable, and no dedupe
key, payload or arbitrary exception string leaves the fixed numeric
local result. This supplements the earlier synthetic notification
examples with actual SQL contention behavior; it does **not** provide
a batch ID, exactly-once callback delivery, an independent commit
witness, host identity, process termination or production authorization.

The dedicated CI-local execution is:

```sh
pytest -q tests/storage/test_partitioned_raw_postgres.py::test_b1_same_batch_concurrent_duplicate_replay_callback_failure_keeps_commit
```

This requires the existing isolated CI PostgreSQL fixture and its
`BP_TEST_DATABASE_URL`; it is not a script to run against production.

## Concurrent local PostgreSQL rollback-versus-replay boundary

An additional fixed local-PG regression starts a failing writer,
waits until its transaction holds real raw rows and dedupe claims
**without commit**, then starts a competing writer using the same
three bulk topic event identities plus its own fourth event. The
failing transaction rolls back after the raw insert. The surviving
writer must insert the shared trade, order book and liquidation
and its own extra order book; final local raw and dedupe table counts
must both equal **four**. The rolled-back writer's unique trade
must be absent from both tables; the surviving writer's unique
order book must be present in both. Its postcommit callback checks
a separate connection's committed-row visibility and immutable counts;
the failing transaction's callback must **never** run.

This exercises real local PostgreSQL lock/rollback/dedupe interplay,
not only independently sequenced fake callback outcomes. It remains
**nonproduction**; one controlled race is not an exhaustive concurrency
proof, a source identity attestation, or evidence about the live
recorder's actual queue/DB behavior. It does not establish that
async cancellation kills underlying database writes. The helper is
not enabled in production, and Phase 14 stays HOLD.

## Repository disposition

[Issue #589](https://github.com/Dtwosam/BP/issues/589) was
**closed by the repository owner on 14 October 2026**. This work
does not reopen it or reverse that tracker decision. The issue's
closure does not independently establish why Bybit-linear market
events have not met production evidence requirements.

**Phase 14 HOLD; economic acceptance NOT MET; live trading disabled.**
This is a strictly offline review. There was no production host,
service, database, exchange, deployment, shadow, holdout, source/model
promotion, wallet or trading action.
