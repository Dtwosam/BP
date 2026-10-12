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

## 12 October 2026: operator-reported exact-hash offline execution

The authorized operator retrieved the preserved JSONL **read-only**, checked
the exact SHA-256 on the Mac, and ran this reporter there. The user provided
the aggregate JSON and terminal `PHASE14_V4_OFFLINE_COVERAGE=PASS_EVIDENCE_ONLY`
marker. Review and attribution (not an independently rerun host/CI analysis)
are preserved in
`docs/evidence/phase-14-v4-successor-offline-coverage-forensics-20261012.json`.
The operator-reported local `EVIDENCE_SHA256=PASS` and matched
`d0478b4e8b16a0fa07a850ada583095d09f1c1e81dcfb407abeff07877fd3324`.

The 116 missed decisions were **all**
`feature_inference_lag_exceeded` (zero `decision_lag_exceeded`);
lag min **2.007759 s**, median **2.911243 s**, p95 **4.920635 s**,
maximum **11.73573 s**, against the frozen 2 s tolerance.
This means the first poll was not the classified failure for those 116
markets; it does **not** isolate database query, feature construction,
inference execution or sequential scheduling delay.

Bybit linear affected **105 of 106 distinct source-ineligible markets**
(not 126, which is the Bybit linear **reason occurrence** count).
Bybit spot affected 12 and Coinbase 11; venue counts overlap:
91 markets had one affected venue, eight had two, seven had three.
From these aggregates, **90–91** markets are constrained to have
**only** Bybit linear reasons; the report does not distinguish those
two possibilities. The prior 79–96 bound below was legitimate but
has been superseded by the tighter observed overlap counts.
Of 101 exhausted retries, 100 involved Bybit linear. All 208 deferrals
reconciled, with zero recoveries and zero pending.

The 169 non-JSON lines were counted as ignored, not assessed for their
contents; accounting of parsed terminal decision events reconciled.
No root-cause determination or venue/feed outage proof is warranted
from this outcome alone. The two-hour series has no 12–14h bucket
and only five terminal decisions in 14–16h, but gaps in counted
decisions alone do not prove a service or feed interruption.
**Phase 14 remains HOLD**, economic acceptance NOT MET, no new
run, no model change, no holdout access, no live trading.

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

## Source-only market bounds from existing aggregates

The 11 October immutable economics summary reports 106 source-ineligible
markets and six **missing** source-flag counts: Bybit linear
`current_missing=72` and `market_start_missing=54`; Bybit spot
`current_missing=3` and `market_start_missing=10`; Coinbase
`current_missing=6` and `market_start_missing=8`. The individual
reason occurrences are not distinct market counts, and the original
runner emits each required missing flag at most once per market.

Let `N_non_linear` be the number of distinct source-ineligible markets
with **any** Coinbase or Bybit spot reason. The four non-linear flags
have 3 + 10 + 6 + 8 = 27 total occurrences, so
`N_non_linear <= 27`. The single Bybit spot market-start flag occurred
on 10 distinct markets, so `N_non_linear >= 10`. Every one of the 106
source-ineligible markets has one or more required-source reasons; a
market with **no** non-linear reason therefore has only Bybit linear
reason(s).

Consequently, the number of source-ineligible markets flagged
**exclusively** by Bybit linear is `106 - N_non_linear`, bounded
**79–96 inclusive** (about **74.5%–90.6%** of the 106).
This is a *deduction from previously verified aggregates*, not an
observed market-by-market count, and it does not prove a Bybit feed
outage or establish that remedying one venue would yield predictions.

The separate 116 missed decisions (47.35% of all 245 seen markets)
remain substantial, independent of the 106 source-ineligible cases
(43.27%). A full market-level breakdown, missed-decision reasons,
two-hour timing and source overlap still require the exact SHA-verified
preserved JSONL; they cannot be inferred from the aggregate totals.

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
