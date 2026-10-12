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
