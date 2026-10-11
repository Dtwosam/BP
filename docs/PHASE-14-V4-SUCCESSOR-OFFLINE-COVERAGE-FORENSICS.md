# Phase 14 V4 successor — offline coverage forensics

## Scope

This is a **repository-only diagnostic candidate** for the separately completed
7–8 October 2026 V4 successor shadow. It is **not a new paper shadow,
production query, source replay, threshold search, or profitability test**.

The existing strict closeout established 245 markets: 23 predictions,
116 missed decisions, 106 source-ineligible. A separate canonical P&L report
observed one settled paper loss (USD -4.288530911232). Those results remain
unaltered. Phase 14 remains **HOLD**; the holdout is consumed, the model
and global min-edge 0.05 are frozen, and live trading and promotion stay off.

The script reads one existing local JSONL into a byte snapshot, verifies the
snapshot's SHA-256 **before** parsing those exact same bytes, refuses the
wrong filename/run/model/decision contract, enforces
one terminal outcome per condition, a matching prediction ID for each
quote-or-evaluation outcome (with no reused prediction IDs across markets),
and checks counts against the completed record. It reports:

- Missed decisions split between decision_lag_exceeded (late at first
  poll) and feature_inference_lag_exceeded (late after source feature
  construction/inference), with min/median/p95/max recorded lag.
- Source-ineligible reason occurrences and **distinct markets affected
  per venue** (venues can overlap for a market).
- Two-hour decision-outcome buckets relative to the original run start.
- Terminal source retry outcomes reconstructed from each source-ineligible
  event: **retry-exhausted versus immediate ineligible**, retry-count
  distributions, and terminal retry status by affected venue. Exhausted
  terminal count must reconcile with completion. When the completion reports
  zero recovered and zero pending retries (as in this successor), the sum
  of terminal retry counts must equal its recorded deferral total.
  Venue buckets can overlap; event-level deferrals were not individually
  emitted and nonzero recovery may not be reconstructible after late
  inference.

It never imports database libraries, opens a database, accesses labels or
the holdout, invokes network/execution code, or changes the strategy.

## Running after separate review of the preserved evidence

The script is prepared for a local/offline copy of the **already-preserved**
JSONL. This note does not authorize a new production read, Cloud Shell
session, shadow activation, or database query. Do not upload raw payloads
or private credentials to a public PR.

~~~sh
python scripts/report_v4_fresh_book_offline_coverage.py \
  --evidence-file /path/to/offline/v4-fresh-book-shadow-20261007T200650Z-d58b2cbedc3f.jsonl \
  --expected-run-id v4-fresh-book-shadow-20261007T200650Z-d58b2cbedc3f \
  --expected-sha256 d0478b4e8b16a0fa07a850ada583095d09f1c1e81dcfb407abeff07877fd3324 \
  --expected-model-sha256 6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf
~~~

Expected aggregate counts are 245/23/116/106/14/9, but the reason-level
late-decision split **has not yet been established** from public merged
aggregate evidence. Do not claim the tool executed on the preserved run
until a separately reviewed operator report actually exists.

## Interpretation boundary

A high decision_lag_exceeded count would focus investigation on due-market
polling, scheduling, market discovery, or upstream query time. A high
feature_inference_lag_exceeded count would focus attention on read-only
source feature query and inference latency. Neither outcome alone proves
cause; recorded lag is retrospective metadata.

Bybit linear contributed 126 **reason occurrences** in the existing
source-ineligible summary, not 126 distinct markets. The new distinct-market
venue breakdown helps remove this denominator confusion, but still does not
prove feed outages, source timestamps, DB commit visibility, retention gaps,
or parser correctness. The earlier timing diagnostic tested only three
samples per missing-reason category and cannot establish a population result.

The tool must not be used to increase the two-second decision tolerance,
relax the two-second source freshness requirement, change the frozen model
or threshold, justify a new paper run, or reopen the consumed holdout.
Successful diagnostic reconciliation is **not** an economic promotion gate.

## Validation

- Offline fixture tests: pytest -q tests/v4_paper/test_v4_fresh_book_offline_coverage.py
- Formatting/lint: ruff check scripts/report_v4_fresh_book_offline_coverage.py tests/v4_paper/test_v4_fresh_book_offline_coverage.py

Runbook status: repository implementation only. No operational execution
or observed reason counts are claimed.

## Historical timing diagnostic retention caveat

The 7–8 October 24-hour successor was assessed on 11 October. Under the
repository's normal 24-hour **hot PostgreSQL raw event** retention, those
market cutoffs were already outside the expected queryable raw window. The
additional 24-hour archive is separate and the P&L timing diagnostic did
not read archives. Its 18 sampled 'no_usable_event_near_cutoff' results
**must not be used as evidence that live feed events did not exist**:
retirement, lag, parser coverage and historical commit visibility remain
unresolved. Actual deletion or archive state is not independently proven.

A follow-up repository-only timing guard explicitly marks out-of-hot-window
samples and avoids querying expired hot raw history. This does not reinterpret
or rewrite the archived source-ineligible flags or single settled paper loss.
The offline decision-funnel tool in this runbook uses **only preserved JSONL**
and therefore does not depend on historical raw PostgreSQL retention.
