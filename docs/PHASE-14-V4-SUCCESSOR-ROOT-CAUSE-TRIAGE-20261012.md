# Phase 14 V4 successor — evidence-constrained root-cause triage

**Date:** 12 October 2026. **Scope:** repository-only evaluation under
[Issue #589](https://github.com/Dtwosam/BP/issues/589), at
`main` `497cc3b358237f6f2a8c32230c434a50d9544910`.

**Decision:** Phase 14 **HOLD**, economic acceptance **NOT MET**,
live trading and automatic promotion **disabled**. No model refit, risk or
freshness threshold adjustment, holdout reuse, new paper shadow, production
query, service change, or new source-data capture is authorized.

## Evidence baseline and provenance

The 7–8 October successor's original exact-SHA-256-bound, operator-run
offline coverage reporter reconciled **245 markets**:
**23 predictions**, **116 missed decisions**, **106 source-ineligible**.
Only 14 of 23 predictions were evaluated; nine lacked quotes.
All **116** terminal missed events carry the label
`feature_inference_lag_exceeded`, with end-to-end observed lag:
min **2.007759 s**, median **2.911243 s**, p95 **4.920635 s**,
max **11.73573 s**, against the frozen **2 s** gate.
The labels are not independent stopwatch measurements of inference.

Of **106** ineligible markets, **105** carry a Bybit-linear reason:
**90 only linear**, five linear+spot, three linear+Coinbase, seven
linear+spot+Coinbase; **one Coinbase only**. Among 105 linear-affected:
**51** current-only, **33** market-start-only, **21** both.
Across **00–12h**, 86/144 markets were source-ineligible (59.72%);
across recorded **14–24h**, 20/101 (19.80%). There are **no terminal
decision outcomes recorded** in the 12–14h bucket; this does *not* prove
service downtime or feed interruption.

**Source:** operator-supplied JSON and terminal PASS from the
SHA-bound **private, already downloaded** JSONL, independently
cross-checked for *aggregate arithmetic only*. The original private
file was not obtained/replayed by GitHub or ChatGPT:
- `docs/evidence/phase-14-v4-successor-offline-coverage-forensics-20261012.json`
- `docs/evidence/phase-14-v4-successor-exact-venue-overlap-20261012.json`
- `docs/PHASE-14-V4-SUCCESSOR-STATIC-PATH-FORENSICS-20261012.md`

The 169 skipped non-JSON lines have not been classified. No historical
row-commit times, queue residence, per-market stage clocks, or
transaction-time source-row availability are in the supplied aggregate.
The historical hot raw-event retention caveat remains binding.

## Priority order: information value, NOT likelihood estimates

Priorities below are **which uncertainties to discriminate first** given
impact and observability; they are **not** posterior probabilities or
claims of primary historical cause. Both workstreams are independent:
the 106 ineligible decisions are not the same events as the 116 misses.

### Workstream S — Bybit-linear source eligibility (105 markets flagged)

| Priority | Mechanism / current strength | What current evidence establishes | Missing discriminator |
| --- | --- | --- | --- |
| **S1** | Price-bearing, source-time and receipt-cutoff eligibility; **confirmed contract**, historical cause **unknown** | The frozen reader accepts only eligible `ticker.data.lastPrice` or price-bearing `trade.data[].p`, subject to the original source age, future skew and received-at cutoff. `markPrice`, bid/ask and index prices cannot substitute. Synthetic collector→reader tests in [PR #594](https://github.com/Dtwosam/BP/pull/594) verify price-less tickers can exist on the priority lane. 90 markets had **only** linear flags, with current and start anchors both represented. | Distinguish, for each due anchor, (a) no messages, (b) messages but no parseable price, (c) valid price but outside source/receipt constraints, and (d) eligible rows unavailable to the decision query. No such original-run breakdown exists. |
| **S2** | Recorder bulk/priority write and transaction-commit visibility; **mechanism reproduced**, historical incidence **unknown** | Ticker messages enter the priority writer even without `lastPrice`; price-bearing `publicTrade` enters bulk. [PR #595](https://github.com/Dtwosam/BP/pull/595) proves with isolated synthetic PostgreSQL transactions that a row with `received_at <= decision_at` can remain invisible before commit. A previously prefetched empty cache stays empty until refreshed. | Need **non-prod or separately approved prospective** per-lane queue/flush/commit timing and source eligibility-at-decision metrics. A retrospective `received_at` scan cannot prove when a transaction committed. |
| **S3** | Subscription, collector-feed or partial exchange outage; **not established** | Two recorder connections subscribe to linear bulk and ticker topics. All 105 linear-affected flags are eligibility outcomes, not verified packet gaps. First/second-half frequency changes are descriptive only. | Require contemporaneous, bounded feed-message counts, incident/reconnect state and last eligible event by topic. Do **not** infer an outage from missing flags or 12–14h terminal-event gap. |
| **S4** | Partition/SQL row selection, retention or query-plan issues; **possible but unmeasured** | Candidate selection restricts received/source timestamps and uses partition-specific indexed queries; the actual original plan, row counts and visibility are unknown. Later raw-retention scans are confounded by archival/retirement. | A contemporaneous stage plan/timing or preserved original decision-time snapshot would be required; **do not** issue a historical production query or infer a missing index from source code. |

**Ranking rationale:** S1 is the first distinction to resolve because the
frozen definition of an eligible **price** determines every linear flag.
S2 can make otherwise eligible events invisible and warrants separate
measurement. S3/S4 cannot be elevated as actual causes without independent
contemporaneous evidence. The 90 linear-only count is a **priority signal
for investigation**, not a causal decomposition.

### Workstream T — late decision completion (116 misses)

| Priority | Mechanism / current strength | What current evidence establishes | Missing discriminator |
| --- | --- | --- | --- |
| **T1** | Sequential batch scheduling and **batch-captured `now`**; attribution defect **demonstrated** | `scripts/run_v4_fresh_book_shadow.py` captures `now` before due-market SQL and reuses it for *each* market's initial lag. It processes due markets serially. [PR #591](https://github.com/Dtwosam/BP/pull/591) demonstrates a later market can **already be overdue** at actual processing start while passing the stale initial-lag test and subsequently being labeled `feature_inference_lag_exceeded`. | Record per-row actual start relative to decision time, plus batch capture and row position. Cannot reclassify any of the 116 from the aggregate alone. |
| **T2** | Readiness/full-feature query duplication and query/payload volume; **code/fixture behavior verified**, actual timing **unknown** | The shared reader clears prefetched candidates between the six-anchor readiness and full feature stage; synthetic SQLite tests measured **six repeated anchor candidate selections** (31 total requests in that fixture). Partitioned SQL candidate selection materializes full payloads with no per-request `LIMIT`. | Non-production performance fixtures or separately approved contemporaneous timings for prefetch/SQL, parsing, feature build and total elapsed. Fixture query counts cannot apportion historical seconds. |
| **T3** | Model inference, query contention, quote wait, outer-loop subscription/due-market work; **not isolated** | All can consume batch wall time; a quote wait after a successful prediction can affect **subsequent** due markets. The recorded 116 lags are end-to-end and stage timers were not preserved in the JSONL. | Distinct batch overhead, per-row stage clocks and per-row ordered market IDs; no historical stage-level record currently available. |

**Ranking rationale:** T1 must be measured first to avoid treating a
postprocessing classification as a time-spent-in-inference diagnosis.
T2 is a repeatable implementation cost worth profiling only after
attribution is separable. No evidence assigns a fraction of the 116 to
T1, T2 or T3.

## 12 October 2026: approved one-time Stage A visibility observation completed

**Provenance and safety:** The operator supplied terminal output from the
separately authorized, strictly bounded **stdin-only, no-install** probe
`scripts/report_v4_linear_price_visibility.py`
(Git blob `89444ba6834a5264dca23c7b14a458c5d237854d`),
executed using the existing versioned V4 runtime. Reported UTC
start `2026-10-12T21:07:58.233281+00:00`, completion
`2026-10-12T21:08:35.217565+00:00`.
`DIAGNOSTIC_PREFLIGHT=PASS`,
`PHASE14_V4_LINEAR_PRICE_VISIBILITY_STATUS=PASS` and
`DIAGNOSTIC_EXECUTION=COMPLETE` were returned; the same output attested
database read-only, no writes, service mutations or order submissions.
Original raw rows were **not** shared, and GitHub/ChatGPT did **not**
independently perform or verify the host execution. Immutable supplied
aggregate and explicit caveats:
`docs/evidence/phase-14-v4-linear-price-visibility-prospective-20261012.json`.

The report covered **60 wall-clock samples**, spaced nominally 0.5s
apart, with **three SELECT categories per sample**, 12s lookback,
maximum eight returned candidates per query, 1500ms statement timeout,
and a 300s external execution bound: **180 attempts, 179 successful
category queries, one `ticker_any` timeout**, zero reported
non-timeout query errors. The price-eligibility summary's zero
`inconclusive_due_to_query_error` counts only the two price-bearing
categories, both of which completed all 60 attempts. It does **not**
mean all 180 queries completed.

| Observed category | Eligible samples | No usable row | Query successes | Row limit hit |
| --- | ---: | ---: | ---: | ---: |
| Any Bybit-linear ticker event | 53/59 successful (not necessarily usable price) | 0/59 | 59/60 | 59/59 |
| Bybit-linear ticker with parseable `lastPrice` | 33/60 (55%) | 0/60 | 60/60 | 27/60 |
| Bybit-linear `publicTrade` usable price | 0/60 | 60/60 | 60/60 | 0/60 |

Consequently **33/60** samples had an eligible ticker-price candidate
in the bounded query; **27/60** found neither ticker nor trade eligible.
No trade candidates were visible within the 12s query window in any
of the 60 successful trade lookups. For the price-bearing ticker
category, the `source_age_seconds` median was **1.668792 s**, p95
**6.840410 s** across selected present candidates, which **includes
stale** observations rather than just eligible observations.
`ticker_any` age counts cannot be substituted for price eligibility.

**Measurement limitations that constrain inference:**

1. `ticker_any` saturated its eight-row limit on **all 59 successful**
   queries. `ticker_last_price` saturated on **27/60** queries.
   Because each query loads most-recent-by-receipt candidates before
   Python's source-age eligibility choice, an older-but-fresh-by-source
   candidate may be truncated. The aggregate does not include the
   per-sample overlap between these 27 saturated price queries and
   the 27 `neither` classifications, so **no corrected true
   eligibility percentage** is justified.
2. `ticker_any` eligibility is **timestamp** eligibility of an event
   that might be price-less. The 53/59 value is not proof of an
   eligible `lastPrice`.
3. `trade_price` returned no usable candidate within a bounded
   **12s lookback** for this observed period. This is a source
   availability clue, **not proof of a permanent trade-feed outage**
   or the absence of earlier trades or uncommitted original-run events.
4. These were 12 October **unsynchronized wall-clock observation
   points**, not the 7–8 October frozen market start/current anchors.
   No original-run stage timing, packet history, queue dwell, transaction
   commit-time provenance or per-anchor source availability was
   reconstructed. The 90 linear-only historical ineligibilities and
   116 misses remain **unattributed**.

**Updated investigation ordering:** The Stage A sample demonstrates
nonuniform *currently visible* eligible ticker-price coverage and no
visible qualifying trade candidates in its sampled lookbacks.
Accordingly future questions should distinguish ticker payload price
presence, timing/cutoff eligibility, trade-lane receipt/commit
visibility and row-limit saturation **before** claiming an outage or
retuning the frozen source-age gate. S1/S2 remain unresolved historical
causal mechanisms; a second probe, instrumentation, production
modification or shadow run is **not** approved by completion of Stage A.
Further access needs a fresh separate user authorization and exact
bounded plan.

## Follow-up synthetic LIMIT 8 selection check (12 October 2026)

The approved Stage A run is **complete; no second query was performed**.
Two new SQLite-only tests in
`tests/deploy/test_phase14_v4_linear_price_visibility.py` compare the
existing reporter's receipt-time-sorted **LIMIT 8** candidate slice
with the *unchanged* `V4SourceTimeReader.latest_price` on nine
synthetic price-bearing linear ticker events. In one fixture eight
newer-received but stale-by-source-price observations occupy the
probe's eight slots, while a ninth older-received but source-fresh
price is eligible under frozen V4: the probe says no eligible
price, whereas the real V4 reader selects the ninth row. In another
fixture all nine ticker prices are stale and both readers find no
eligible price. **Therefore saturation permits false negatives but
does not establish them for any actual sample.**

Stage A directly establishes **33 successful detected price-eligible
ticker samples out of 60**. The 27 unsuccessful price-class lookups
remain only *candidate-limited negatives*; because 27 of 60
`ticker_last_price` queries hit LIMIT 8 but their sample-by-sample
overlap with those 27 negative outcomes was not reported, **the number
of missed otherwise eligible ticker samples can range from zero to
27**. The resulting **33–60/60** possible count is only a
conservative logical bound on candidate-limit-corrected eligibility
at those observation points, *not* a measured revised source
availability statistic. The 0/60 `trade_price` result was
unsaturated (no usable row, no LIMIT 8 hits); it remains a bounded
12-second query observation, not evidence of historical or universal
trade-feed outage.

The synthetic tests do **not** estimate how often this pathological
arrival-vs-source ordering occurs, explain the October 7–8 paper shadow,
relax the original 2-second freshness cutoff, modify any executable
recorder/reporter/reader code, or authorize production queries.
The exact historical cause remains unresolved and Phase 14 stays HOLD.

## Offline trade-visibility contract (12 October 2026)

The Stage A report found **0/60** query-visible Bybit-linear trade-price
candidates in its short observations; this is **not a count of all
received or persisted trade messages**. Its SQL explicitly requires
`source='bybit'`, `stream='linear'`, `instrument='BTCUSDT'`,
`event_type='trade'`, **non-null `source_timestamp`**, `payload.data`
of JSON-array type and a bounded 12-second receipt window.
Those filters run *before* the Python price parser. A zero result does
not distinguish no WebSocket trade messages from messages outside the
time window, messages with null source timestamps, malformed/filtered
row payloads or rows that had not committed at query time.

Two repository-only tests in
`tests/v4_paper/test_v4_bybit_linear_collector_price_contract.py`
exercise the **actual parser → routed bulk sink → buffered batch
writer → SQLite recorder repository → frozen V4 source-time reader**
path, without any network connection or production query:

- A synthetic linear `publicTrade.BTCUSDT` with top-level `ts` and
  price-bearing `data[].p` is inserted by the actual bulk writer into
  raw-event storage and selected as a price by the unchanged frozen
  V4 reader; the source-filtered synthetic Stage A candidate query
  also sees it. This checks the end-to-end code contract in a
  controlled case; it is **not** evidence that the live trade feed
  was subscribed, delivered, committed quickly, or healthy on
  7–8 or 12 October.
- A deliberately hypothetical trade envelope **without top-level
  `ts`** but with per-trade `data[].T` and valid `data[].p` is
  successfully routed, buffered and persisted with
  `source_timestamp=NULL`. Neither the existing Stage A SQL's
  non-null timestamp gate nor the unchanged frozen V4 reader
  considers it a price candidate. The parser currently uses
  envelope `ts` for trade source timestamp and does not
  substitute `data[].T`. **No evidence establishes that real
  Bybit trade messages lacked top-level `ts` during the sampled
  observations.** This fixture is a negative control, not a
  recommendation to change timestamp precedence or the frozen
  source-time rules.

**Discriminating implication:** The observed Stage A trade zeros
must be described as **zero matching eligible-query candidates**,
not zero network trade packets or zero stored trade events. A
historically or prospectively contemporaneous breakdown across
subscription/receipt, event-type/schema, timestamp nullability,
enqueue/commit visibility and query cutoff would be needed to
assign live cause. Such a new production query or instrumented
recorder is **not** authorized by this offline investigation.
The 27/60 capped-ticker lookup uncertainty is unaffected.
Phase 14 **HOLD**, negative paper economics unchanged.

## Offline topic-health observability check (12 October 2026)

A further narrow static read and two **synthetic WebSocket runner tests**
(`tests/collectors/test_websocket_runner.py`) identify limits in
the existing recorder's topic-health evidence, without opening a real
WebSocket, reading production or changing the runtime:

1. `build_default_recorder_service` subscribes a single **linear bulk
   connection** to `orderbook.50.BTCUSDT`,
   `publicTrade.BTCUSDT`, and `allLiquidation.BTCUSDT`.
   `WebSocketCollectorRunner` updates `FeedWatchdog` whenever
   **any parsed event** arrives, using the **source/stream pair**, not
   an individual subscribed topic. A deterministic test delivers only
   three order-book events across a synthetic 12-second span and
   zero trade events; the stream-level watchdog is still fresh at
   13 seconds, and goes stale only after the last **order-book** event
   ages beyond the configured threshold. This proves the **bulk
   connection's non-stale status is not positive evidence of a
   healthy public-trade topic**. The separately subscribed
   Bybit-linear ticker socket has its own watchdog instance; this
   finding is *within the multi-topic bulk connection*, not a
   claim that ticker traffic masks a different socket's health.
2. `parse_bybit_message` returns no raw events for control
   acknowledgements with an `op` field. The runner does not
   independently validate the acknowledgement's `success` or
   enumerate accepted/rejected topics. A synthetic
   `success=false` subscription acknowledgement followed by one
   valid order-book snapshot yields normal book processing with
   `connected`/`disconnected` incidents, **without a dedicated
   topic-subscription rejection incident**. This is only a
   demonstration of absent acknowledgement diagnostics; it does
   not imply that any live Bybit acknowledgement failed.

**Consequence:** Stage A's 0/60 matching linear trade-price rows cannot
be ruled out merely because the recorder's **bulk connection** was
non-stale or because order-book data were present. Conversely these
tests cannot establish any actual Bybit trade topic failure, selective
subscription rejection, transport loss, time-window miss or
writer/commit issue. The code still subscribes to `publicTrade`;
message-level health alone never proves per-topic coverage.

**Priority for any future *separately authorized* observation:**
a bounded, aggregate-only breakdown by **topic** of subscribe-ACK
outcome, parsed event counts, non-null source timestamps, enqueued
rows, committed query-visible rows, and eligibility at the requested
cutoff, distinguishing per-topic silence from storage/reader exclusion.
This is a diagnostic design input **only**, not permission to
instrument/deploy or query production. Original October 7–8 causes
remain unknown, source time gates unchanged, and **Phase 14 HOLD**.

## Decision boundary and safe next action

**Completed and repeatable without additional access:** source/reader
parsing contracts; strict source cutoff behavior; bulk-vs-priority
routing semantics; PostgreSQL precommit invisibility; empty prefetch
cache behavior; batch `now` misclassification possibility; duplicate
anchor requests. These are already verified by synthetic tests and
GitHub CI. Repeating identical synthetic tests or old aggregate reporter
runs does **not** reveal the original historical causes.

**What existing evidence cannot answer:** how many individual October
decisions had a price-bearing row that was committed and visible before
the decision query; historical queue/flush/commit latency; whether
a candidate failed price parsing vs age vs receipt cutoff; and how
much of each missed decision's lag arose before row start, during source
queries, in feature build, in model inference, or through serial blocking.

**Next step under the current repository-only boundary:** preserve the
ranked hypotheses and stop historical causal attribution here. If a
distinct *new* prospective paper-only observation is later considered,
review and explicitly authorize a **separate**, bounded proposal
with (i) exact program/commit and duration, (ii) no-order/zero-money
controls, (iii) aggregate-only or tightly access-controlled logs, and
(iv) stop-on-unsafe-state rules. Candidate **minimum discriminators to
review, not activate**:

- decision-time, batch-capture time, actual row-start time and row order;
  readiness start/end, full-feature start/end, inference start/end,
  and recorded-at; distinguish scheduling delay from per-stage work;
- per source/anchor, counts for source timestamp absent/out-of-window,
  receipt after cutoff, unsupported/unparseable price, and price-valid
  candidate present in the committed decision-visible view;
- per recorder lane, received/queued/batch-flushed/committed intervals
  and metadata-only per-topic event categories, without raw payload
  exfiltration or retrospective inference about absent commits;
- explicit control showing no orders, unchanged frozen model/thresholds
  and source age rules, no holdout access, safe service concurrency and
  finite observation boundaries.

**Not authorized by this memo:** implementing, deploying or executing
such instrumentation; retrieving/replaying protected old raw data;
contacting production; restarting shadows; retuning the model; promoting
the strategy; changing any trading setting. Such activities need a
separate explicit user authorization. Historical negative paper economics
(one modeled settled loss of **USD 4.288530911232**) and
**Phase 14 HOLD** remain unchanged.
