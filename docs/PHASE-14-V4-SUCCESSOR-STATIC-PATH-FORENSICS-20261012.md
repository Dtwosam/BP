# Phase 14 V4 successor: repository-only execution-path forensics (12 October 2026)

**Status:** bounded static-code investigation of [Issue #589](https://github.com/Dtwosam/BP/issues/589).
This is **not** an operational replay, query-plan benchmark, live feed diagnosis,
production check, or proposed change to frozen V4 acceptance policy.
Source reviewed on `main` at `0e37549e03993da95675d74218aa2b9240bed340`.
Frozen economics remain **NOT MET**; Phase 14 **HOLD**.

## Separating observations from interpretations

The operator-reported, exact-SHA-bound offline decision-funnel reconciliation
in `docs/evidence/phase-14-v4-successor-offline-coverage-forensics-20261012.json`
records 245 markets seen, 23 predictions, 116 terminal decision misses,
106 source-ineligible, 14 evaluated and nine quote-unavailable.
All **116** decision-missed terminal events are labeled
`feature_inference_lag_exceeded`, with recorded end-to-end
`decision_lag_seconds` min 2.007759, median 2.911243, p95 4.920635,
max 11.73573 versus the frozen 2-second tolerance.
Of 106 source-ineligible markets, 105 have a Bybit-linear missing-source
flag (12 Bybit spot, 11 Coinbase, overlapping). The distinct Bybit-linear-
**only** count is bounded 90–91 from observed venue-overlap aggregates,
not resolved to a unique number.

These are **terminal classifications**; no substage stopwatch breakdown was
embedded in the original JSONL. The 169 non-JSON lines excluded by the
offline reporter were not independently characterized. ChatGPT/GitHub
did not independently retrieve/replay the private raw JSONL.

## Finding A: a captured `now` can misattribute lateness

In `scripts/run_v4_fresh_book_shadow.py`, the outer loop captures `now =
datetime.now(UTC)` **before** its read-only connection safety query,
nearby-market subscription, due-market SQL query and the sequential
`for row in rows` processing loop (approximately lines 307–322).
Inside each row, `initial_lag = (now - decision_at).total_seconds()`
(approximately line 329) reuses that **batch-level** timestamp. There
is no per-row fresh wall-clock read before this initial deadline test.

After the initial test, the runner attempts core-source readiness, full
feature building and frozen-model inference. It then measures
`recorded_at = datetime.now(UTC)` and emits
`feature_inference_lag_exceeded` when the *end-to-end* lag exceeds 2 s
(approximately lines 371–524). This event name tells us where the
second deadline check was made, **not** what spent the time. An earlier
market's feature queries, a subscription/due-markets query, or a wait
for a previous successful prediction's fresh quote (up to the configured
one-second wait) can make a later market already late before its own
readiness starts, while its `initial_lag` still looks on time.

**High-confidence static finding:** the pre-feature lateness test is
not a reliable measurement of per-market start lateness under backlog.
**Unknown:** how many, if any, of the 116 observed misses were caused
by this timestamp reuse; no per-market start timestamp appears in
those terminal events. Do not relabel or redistribute the 116 without
new evidence. It is inaccurate to conclude all 116 were caused by
feature SQL or model inference.

The same deadline accounting must be interpreted cautiously for
readiness retry/ineligible branches: a source-ineligible terminal label
does not by itself establish that it completed within the decision
deadline. `_due_markets` orders markets by start time; all due markets
run serially inside the same connection and polling loop.

## Finding B: read amplification is structurally possible

In `src/bp_engine/v4_paper/source_time_features.py`:
- `probe_core_source_time_v4_readiness` prefetches six core anchor
  requests: market start and decision time for Coinbase, Bybit spot,
  and Bybit linear (approximately lines 936–974).
- `build_source_time_v4_features` prefetches eight short/regime anchor
  times per venue (24 requests), plus a Bybit-linear ticker-only state
  request (approximately lines 1013–1053).
- Both call `V4SourceTimeReader.prefetch_candidate_rows`. That function
  begins with `self._prefetched_candidate_rows.clear()` (approximately
  line 385). Even though the runner passes the *same reader instance*
  within a decision, the second stage discards its first-stage candidate
  cache. The six repeated anchor requests are consequently not reused
  by this cache across stages. Deduplication occurs **within** a single
  prefetch call, not across both.
- On partitioned PostgreSQL, requests are grouped by venue/type/partition.
  LATERAL candidate selections return full event payloads, with an
  `ORDER BY` but **no per-request LIMIT** (approximately lines 500–580).
  The runner creates a fresh reader for each attempted market, so its
  partition metadata/cache is not persistent across markets.
- The repository provides a dedicated per-partition source-lookup index
  on `(source, stream, instrument, received_at DESC, id DESC)` for
  non-null source timestamps, in
  `src/bp_engine/storage/partitioned_raw.py`; an index definition and
  rollout scripts do **not** prove a particular historical plan, index
  availability, scan size or query duration.

**High-confidence static finding:** repeated core-anchor selection and
unbounded-in-the-SQL candidate materialization are possible sources
of latency. **Unknown:** their measured cost, planner choices, row
counts, Python parsing time, and contribution to the 116 recorded
deadline misses. Do not claim the indexes were absent or slow.

The existing `scripts/report_v4_feature_inference_latency.py` already
has readiness, feature, inference and SQL-stage timing support and a
shared-reader mode. **Do not run it against production** under this
repository-only investigation: it opens a database and replays selected
historical conditions, which are out of scope. Historical hot-retention
also precludes assuming it would reproduce the original decision-time
data view.

## Finding C: Bybit linear `missing` is not a feed-outage diagnosis

The required Bybit-linear market-start/current anchors use exactly
`source='bybit'`, `stream='linear'`, `instrument='BTCUSDT'`.
`latest_price` considers only accepted `ticker` or `trade` event
types and a positive price parsed from `data.lastPrice` or from the
last entry's `data[].p`. It checks **source timestamp** proximity
within the frozen 2-second maximum age and 1-second future skew,
`received_at <= requested_at`, a short received-time query window,
and transport skew limits (approximately lines 678–885).

The reader returns `None` when no eligible, parseable price remains;
an observation it does return is marked `fresh=True`. Therefore an
older or unparsable price can appear as a `*_missing` flag, rather
than proving that no network messages existed. `received_at` alone
does not prove the original transaction had committed and was visible
to the live runner at the cutoff. The original market dates also
precede the normal 24h hot-event retention by the later retrospective
raw timing audit.

**Observed:** 105/106 source-ineligible markets contain a
Bybit-linear missing flag, with 100/101 retry-exhausted markets also
flagged Bybit linear. **Not established:** Bybit market-data outage,
collector subscription faults, incorrect parser, hidden rows,
incomplete partition/index migration, transaction visibility or
an overly strict policy. The frozen two-second freshness policy
must **not** be relaxed as a diagnostic shortcut.

## Minimal safe verification and prioritization

1. **Offline regression design, no runner mutation yet:** model two or
   more due markets with a controlled clock and slow first-market step;
   demonstrate the distinction between batch `now` and actual per-row
   processing start. Cover the quote-wait-blocking scenario. This
   establishes a classification defect's possibility, not its frequency
   in the preserved run.
2. **Offline fixture profiling:** using synthetic/non-production
   SQLAlchemy data and the existing `test_source_time_features.py`
   fixture patterns, count prefetch calls and repeated candidate keys
   across readiness and feature build with a shared reader; estimate
   Python parsing and query-call overhead *only for the fixture*.
3. **Source-eligibility fixtures:** independently test Bybit linear
   ticker delta without `lastPrice`, price-bearing trades,
   stale source times, received-at cutoff and both start/current
   anchors. Explicitly keep missing-versus-outage distinctions.
4. **Evidence review:** use only already preserved/approved offline
   aggregate records and any existing checked-in query-plan artifacts.
   Do not contact production to reconstruct discarded raw history or
   assert an on-host query plan from code.
5. Before *any* future production instrumentation or new shadow, request
   a **separate** explicit approval with bounded scope, identity,
   safety limits and acceptance criteria. No automatic rerun follows
   from static evidence.

**No changes made** to model weights, threshold/min-edge, decision lag,
source age, holdout, paper-shadow runner, services, production schema,
database or order settings. One settled paper loss
(`realized_pnl_usd=-4.288530911232`) remains the frozen economic
result; Phase 14 **HOLD** and live trading disabled.
