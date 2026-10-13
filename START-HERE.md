# START HERE

This folder is the handoff pack for the **BTC Polymarket Prediction Engine**.

If you are opening a new ChatGPT/Codex chat, upload/add this pack to the project and say:

> Read `docs/MASTER-SOURCE-OF-TRUTH.md`, `PROJECT_STATE.json`, `docs/BUILD-ORDER.md`, `docs/DECISION-LOG.md`, `AGENTS.md`, `docs/superpowers/specs/2026-09-13-phase-14-btc-first-v3-challenger-design.md`, `docs/superpowers/specs/2026-09-14-phase-14-v3-gate-b-successor-preregistration.md`, `docs/evidence/phase-14-v3-preregistration-handoff-20260913.json`, and `docs/evidence/phase-14-v3-gate-b-successor-preregistration-20260914.json`. Continue the project from the current phase. Do not redesign or restart it from memory unless the source of truth explicitly requires a change.

## Authority order

1. `docs/MASTER-SOURCE-OF-TRUTH.md` — canonical project definition
2. `PROJECT_STATE.json` — where the build currently stands
3. `docs/BUILD-ORDER.md` — what to build next
4. `docs/DECISION-LOG.md` — why key decisions were made
5. `docs/CHANGELOG.md` — what changed over time
6. `AGENTS.md` — working rules for AI/developers
7. `docs/superpowers/specs/2026-09-13-phase-14-btc-first-v3-challenger-design.md` — BTC-first V3 research design
8. `docs/evidence/phase-14-v3-preregistration-handoff-20260913.json` — historical V3 Gate A / Gate B v1 handoff
9. `docs/superpowers/specs/2026-09-14-phase-14-v3-gate-b-successor-preregistration.md` — approved Gate B successor design
10. `docs/evidence/phase-14-v3-gate-b-successor-preregistration-20260914.json` — durable successor preregistration decision evidence

## Current next step

**13 October 2026 — PR #616 external Historical Backfill Smoke blocker:**
The original historical smoke selected the *first* recently closed BTC
Up/Down market and failed twice when one or both authentic CLOB outcome
histories were empty. A bounded diagnostic fix now tries up to three
verified recently closed **5m** markets, then three **15m** markets,
requiring both real one-minute outcome histories for the *same*
market. All-empty probes or network/schema errors still fail closed;
failure output includes aggregate counts but no outcome token IDs.
The collector / writer benchmark in draft PR #616 passed 2,623 tests,
but that PR stays unmerged until all four workflows pass.
No production/VM/DB/shadow/trading action. Phase 14 HOLD; economics
NOT MET; live trading disabled. See Issue #589.

**13 October 2026 — Stage B1 local after-commit hook and non-gating
PostgreSQL overhead review (#589):** The dormant
`insert_same_batch_attributed_offline()` path now supports a synchronous
counts-only observer invoked **after successful DB commit**; it reports
callback delivery failures separately from transaction failures, without
echoing row IDs, dedupe keys, payloads or exception contents.
Local PostgreSQL tests cover commit visibility, immutability, rollback,
hook failure and async rejection. A bounded 18-transaction, alternating
8/32/64-event local CI benchmark compares baseline versus same-batch
attribution wall time, process CPU, and `tracemalloc` allocation peaks,
**without an overhead acceptance threshold**. It is not representative
VM/recorder performance; regular recorder behavior is unchanged.
No production VM/DB/restart/deploy, shadow, model/source gate or
trading action. Issue #589 stays OPEN; Phase 14 HOLD, economics NOT MET,
live disabled.

**13 October 2026 — Stage B1 same-SQL, per-class after-commit insertion receipt
rehearsal (#589):** An **opt-in, not-production-wired** repository method
now exposes only the dedupe keys already returned by partitioned
PostgreSQL's `ON CONFLICT DO NOTHING RETURNING` after its original
two INSERT statements. An offline test adapter maps these keys
to trade/orderbook/liquidation and excluded non-bulk categories,
and returns only aggregate counts after transaction commit, without
splitting batches or increasing SQL statement count. Local isolated
partitioned PostgreSQL tests compare exact SQL against normal insert,
validate dedupe and in-transaction rollback. This does NOT establish
real VM CPU/queue/DB latency, an operational one-shot exporter or
safe service restoration. Production remains untouched, Issue #589
OPEN; Phase 14 HOLD, economics NOT MET, live trading disabled.

**13 October 2026 — Stage B1 true per-class insertion attribution, local test DB only (#589):**
The new opt-in, never-production-wired
`stage_b1_insert_attribution.py` uses the unmodified
`RecorderRepository.insert_events()` inside one bounded transaction
to count **actual inserted rows after commit** by bulk class, distinct
from successful batch *attempts*. It excludes ticker and other feed
rows from B1 bulk counts, detects cross-class duplicate-key conflicts,
and fail-closes transaction failures. Local SQLite tests and local
partitioned-PostgreSQL CI coverage verify dedupe/rollback behavior.
Grouping into separate INSERT statements changes SQL/ID ordering,
so it **must not be treated as a representative production-speed
instrument**; no host is queried and the real production fault
remains unknown. No recorder wiring, deployment, DB mutation on VM,
model/source gate, shadow or trading change. Phase 14 HOLD,
economic acceptance NOT MET, live trading disabled. See the B1
integration/overhead/rollback review and Issue #589.

**13 October 2026 — Stage B1 real Python pipeline / fake external I/O rehearsal (#589):**
The new repository-only `scripts/rehearse_stage_b1_real_pipeline_offline.py`
integrates the actual `RecorderService`, Bybit parser via
`WebSocketCollectorRunner`, event queue, batch writer and bounded
one-shot observer with **only in-memory synthetic socket frames and
a fake no-DB sink**. Test scenarios cover graceful drain, writer failure
and unresolved cancellation; reports remain aggregate-only and never
prove inserted rows or a real transaction. No default recorder switch,
process kill/service rollback, production endpoint, SQL or host access.
See the Stage B1 integration review. The genuine production fault
remains unidentified; economic acceptance NOT MET, Phase 14 HOLD,
live trading disabled.

**13 October 2026 — Stage B1 reproducible *inert* source review packet (#589):**
The next GitHub-only change makes an offline, digest-pinned, **four-source**
candidate ZIP containing `websocket_runner.py`, `service.py`,
`stage_b1_observer.py` and the uninvoked
`stage_b1_one_shot.py` library at immutable reviewed commit
`347db08ee47d113f35fd6a9bc0e4bd85610aea0d`.
It checks source hashes and forward/reverse source patching against
previously operator-reported `a352c66e`, and verifies the archive
without running its contents. This is **not** a live one-shot launcher,
confirmed host rollback, production resource approval or credential
safety attestation. No ZIP is produced/uploaded by default in CI;
the artifact is available by explicit *offline-only* creation.
This does not supersede the earlier three-file #607 patch digest.
No VM, database, service, trading, model or shadow contact; Phase 14
HOLD, economics NOT MET, live disabled. See the Stage B1 integration
review and Issue #589.

**13 October 2026 — Stage B1 fail-closed offline preflight manifest (#589):**
A new pure-local validator
`scripts/review_stage_b1_preflight_manifest.py` checks the fixed
three-file **source-only** #607 SHA-256 and Git blob identities,
strictly structured self-reported host/runtime/research/zero-money
settings, rollback material, restoration and observation limits.
Its checked-in template intentionally has **no on-host evidence**,
and CI must assert the result is `INCOMPLETE_OR_MISMATCHED_NO_GO`.
Even fully populated synthetic claims yield only
`STRUCTURE_COMPLETE_UNVERIFIED_NO_GO`, **never authorization**.
The previously operator-reported VM SHA is not reverified; the #607 patch
does not enable a timed observation or include #608/#609.
No production contact, deployment, restart, SQL, shadow, model or
trading change. See
`docs/PHASE-14-V4-B1-INTEGRATION-OVERHEAD-ROLLBACK-REVIEW-20261013.md`.
Phase 14 HOLD; economics NOT MET; live trading disabled.

**13 October 2026 — Stage B1 isolated synthetic process-termination rehearsal (#589):**
A new **fixed-fake-child-only** POSIX script
`scripts/rehearse_stage_b1_process_boundary.py` rehearses bounded
SIGTERM/SIGKILL escalation and direct process-leader reaping without
ever invoking the recorder, database, real network or arbitrary command.
Its entire output is sanitized, strictly synthetic metadata; child stderr
and malformed/oversize report data never surface. This does **not**
prove full descendant/systemd lifetime, give a production operator
command or output sink, verify VM state, satisfy representative overhead,
establish actual inserted rows, or authorize any rollout.
The previous source patch from #607 is unchanged and does not include
#608/this script. CI validation is necessary before marking rehearsal
successful. Issue #589 OPEN; Phase 14 HOLD, economic acceptance NOT MET,
live trading disabled.

**13 October 2026 — Stage B1 one-shot lifecycle/readout library candidate (#589):**
A **repository-only, never auto-invoked** `stage_b1_one_shot.py`
library now sketches finite collection against an explicitly passed
service and a validated <=120s observer, returns only fixed-category
aggregate JSON (max 4 KiB), and marks premature exits, errors, task
cancellations and outstanding writes as INCONCLUSIVE. It does **not**
provide a production operator CLI, hard process kill, service rollback,
current host identity, representative CPU/RSS/queue benchmarks or per-class
insert proof. A normal production recorder still cannot activate the
observer. Synthetic fake-service tests and workflow review are required.
See `docs/PHASE-14-V4-B1-INTEGRATION-OVERHEAD-ROLLBACK-REVIEW-20261013.md`.
Issue #589 remains open; no VM access/restart/query/new shadow;
Phase 14 HOLD, economics NOT MET, live trading disabled.

**13 October 2026 — Stage B1 exact source backport rehearsal preparation (#589):** A new **repository-only** offline script, `scripts/rehearse_stage_b1_backport.py`, pins the old operator-reported `a352c66e` and reviewed `75281f2b` Git source blobs for exactly the websocket runner, recorder assembly and dormant Stage B1 observer. It fail-closes on unexpected blobs or paths, rehearses **forward and reverse unified-patch application in a temporary directory**, and produces an aggregate-only source manifest with patch SHA-256. An exact GitHub CI rehearsal is required before claiming this passes. It does **not** attest current on-host service/PID/venv identity, perform a systemd restart/rollback, activate B1, provide the one-shot 120s output, or authorize host access. See `docs/PHASE-14-V4-B1-INTEGRATION-OVERHEAD-ROLLBACK-REVIEW-20261013.md`. Phase 14 HOLD, economic acceptance NOT MET, live disabled.

**13 October 2026 — Stage B1 inactive integration/overhead preparation (#589):** [PR #605](https://github.com/Dtwosam/BP/pull/605) merged to `main=dff7b85d106dd3c2a458646348ff536c8bc918c2` with all four workflows green (**2,527 tests, four warnings**). The ordinary recorder entry point still **does not inject** B1; source-only optional injection now covers the SAME linear bulk socket/parser/enqueue and bulk-or-single writer under explicit RESEARCH/live-disabled/zero-money checks. Dedicated ticker events are excluded from bulk writer counts; cancelled threaded DB work is marked unresolved, not definitely rolled back. A CI-only synthetic microbenchmark measured **25,965 ns baseline vs 34,797 ns instrumented per cycle (+8,832 ns / +34.02%)** using a real parser and no-op sinks, **not representative production CPU/queue/commit overhead**. The exact on-target minimal backport, representative overhead budget, one-shot sanitized report/output, rollback rehearsal and any inserted-row/ACK additions remain unapproved. See `docs/PHASE-14-V4-B1-INTEGRATION-OVERHEAD-ROLLBACK-REVIEW-20261013.md`. No production access, query, restart, activation, shadow, live trade or model/source modification; Stage A/B0 approvals consumed, root cause still unknown, Phase 14 **HOLD**, economic acceptance **NOT MET**.

**13 October 2026 Phase 14 Stage B1 offline observer implementation (#589):** A dormant, **not wired or deployed**, `StageB1BulkObserver` is prepared for safe synthetic tests of the **same existing Bybit-linear bulk WebSocket**. It exposes bounded (maximum **120-second**) aggregate-only wrappers for topic-class wire receipt and socket ACK, parsed events, *successfully returned* bulk queue enqueues, and category attempts in **successfully returned DB writer transactions** with failed/in-flight tracking; it never logs raw frames or event values. Successful transaction attempts **do not equal actual per-category inserted rows**, because deduplication and mixed-source batches remain. The module is **not called by `build_default_recorder_service`**, so merging it does not instrument the VM or fix the feed. See `docs/PHASE-14-V4-LINEAR-BULK-STAGE-B1-PLAN-20261013.md` for the still-required exact backport, actual inserted-row discriminator, performance/safety budget, timed observation, rollout/rollback and **fresh separate production authorization**. Stage A/B0 consumed, old October 7–8 root cause unproven, one losing settled modeled paper trade, economic acceptance NOT MET, **Phase 14 HOLD**, live trading disabled.

**13 October 2026 — Stage B1 Bybit-linear bulk isolation preparation (#589):** After the approved Stage A/B0 read-only probes returned **33/60** sample-detected eligible ticker prices (LIMIT 8 censoring possible) and then **zero committed/query-visible trade plus zero orderbook raw rows** in a separate one-minute Bybit-linear **bulk** receipt window, repository-only review found a specific observability gap: the bulk and ticker sockets share the same `source=bybit,stream=linear` incident identity and URL, so historical connected/reconnect/stale/recovered records cannot reliably distinguish them. A small, **not-deployed** source patch adds optional `details.recorder_component` to incidents for the four Bybit runners, including watchdog and clock-skew reports, without changing raw-event parsing, queues, reader, model, or trading controls. Synthetic tests and a bounded Stage B1 design at `docs/PHASE-14-V4-LINEAR-BULK-STAGE-B1-PLAN-20261013.md` distinguish bulk-socket subscription ACK, wire receipt, parsing, enqueue, post-commit visibility and event cutoffs. The labels apply only to future incidents **after** a separately authorized deployment; existing incident histories cannot be retrospectively disambiguated. **No Stage B1 instrumentation/deployment, prod query or restart authorized; Stage A and B0 approvals are exhausted.** Historical cause unknown, economic acceptance NOT MET, one losing modeled paper settlement, **Phase 14 HOLD** and live trading disabled.

**12 October 2026 Stage B0 Bybit-linear bulk read-only completed (#589):** The user separately authorized **one** fixed and bounded production read-only diagnostic, and the operator provided verified-script/no-install preflight and completion markers. Stage B0 sampled the single receipt window **22:26:43.681001–22:27:43.681001 UTC**: **0 committed/query-visible Bybit-linear BTCUSDT trade rows** and **0 orderbook_snapshot/orderbook_delta rows**, each from an independent uncapped `LIMIT 1024` category. This narrows attention to **common bulk ingestion/persistence visibility and filtering**, not only `publicTrade` price parsing; it does **not** prove packet absence, an exchange outage, queue failure, delayed COMMIT, or the historic 7–8 October original shadow cause. Previous Stage A ticker data were from **21:07–21:08 UTC**, not synchronous with B0; neither check establishes timing/causality. Evidence: `docs/evidence/phase-14-v4-linear-trade-storage-b0-20261012.json`, interpretation in `docs/PHASE-14-V4-SUCCESSOR-ROOT-CAUSE-TRIAGE-20261012.md`, review closeout in `docs/PHASE-14-V4-LINEAR-TRADE-STAGE-B0-REVIEW-20261012.md`. Both Stage A and Stage B0 one-time authorizations are now **exhausted**. **No more production queries, bulk recorder instrumentation, deployment, service restart, paper shadow, model/freshness-policy change or live orders authorized.** One negative modeled settlement, economic acceptance NOT MET, **Phase 14 HOLD**, live trading disabled.

**12 October 2026 Phase 14 Stage B0 prepared for separate approval (#589):** After completed Stage A and the offline topic-health/trade-writing regressions, a new **review-only, not-authorized-for-production** `scripts/report_v4_linear_trade_storage_b0.py` candidate and `docs/PHASE-14-V4-LINEAR-TRADE-STAGE-B0-REVIEW-20261012.md` specify a more informative **single-current-timepoint** read-only storage classification: two event-class-specific SELECTs (trade and orderbook separately), 60s received-at window, up to 1,024 rows per class with explicit cap/censoring flag, 1.5s statement timeout, aggregate-only JSON, no null-source-timestamp exclusion, and research/zero-money/readonly preflight plus explicit approval acknowledgement. Synthetic test fixtures and real partitioned PostgreSQL CI-only test verify the SQL/classification contract. A separately approved future operator invocation would require an independent 30s wall cap and exact script/runtime identities. **Do not run it under the consumed Stage A authorization or treat a stored-row check as proof of WebSocket receipt, transaction commit timing, or 7–8 October original root cause**. Stage B1 in-recorder topic instrumentation would require another distinct approval and is **not implemented**. One settled losing paper trade, economic acceptance NOT MET, **Phase 14 HOLD**, live trading disabled.

**12 October 2026 offline Bybit-linear bulk topic-health test (#589):** The bulk Bybit-linear WebSocket subscribes to order-book, public-trade and liquidation on one socket. The existing `WebSocketCollectorRunner` refreshes its **source/stream-level watchdog** on *any* parsed event. New synthetic test shows order-book-only traffic can leave that **bulk** watchdog healthy although **no trade** events are received; a separate synthetic rejected `subscribe` ACK is ignored by the Bybit parser and does not generate a topic-specific failure incident before subsequent book processing. The dedicated Bybit-linear ticker socket uses a separate watchdog; this is **not** cross-socket masking. Neither test proves historical subscription failure or actual trade absence. Read full boundaries in `docs/PHASE-14-V4-SUCCESSOR-ROOT-CAUSE-TRIAGE-20261012.md`. Stage A's 0/60 matching trade-price rows remain unexplained, and its sole read-only authorization is spent. Any new prospective topic-level production measurement needs explicit separate authorization. One losing paper trade, economic acceptance NOT MET, **Phase 14 HOLD**, live trading disabled.

**12 October 2026 offline Bybit-linear trade pipeline contract (#589):** The completed Stage A observed **0/60 matching trade-price candidates**, but its SQL filters `source_timestamp IS NOT NULL`, a 12s received-time lookback and JSON-array trade payloads **before** Python eligibility testing. Do **not** equate zero matching rows with zero received/recorded trade messages. Two new synthetic end-to-end regression cases follow the actual `publicTrade.BTCUSDT` parser → bulk routing → buffered writer → SQLite recorder repository → frozen V4 source reader: a valid `ts`/price trade is persistently eligible, while a **hypothetical** trade with only per-trade `data[].T` (no top-level `ts`) is persistently stored but excluded by the frozen reader and Stage A non-null timestamp filter. This is a possible contract mechanism, **not** evidence actual Bybit feeds omitted `ts` or explanation of 7–8 October misses. Read `docs/PHASE-14-V4-SUCCESSOR-ROOT-CAUSE-TRIAGE-20261012.md`; continue repository-only evidence review unless another bounded production diagnostic is explicitly authorized. Stage A authorization is exhausted, one losing settled paper trade, economic acceptance NOT MET, **Phase 14 HOLD**, live trading disabled.

**12 October 2026 Stage A LIMIT 8 false-negative characterization (#589):** Two **synthetic SQLite-only** tests reproduce the visibility probe's eight-row, receipt-time-ordered sample returning no eligible Bybit-linear ticker lastPrice even though the unchanged frozen V4 reader accepts a ninth, older-received/source-fresh ticker; a control fixture with nine stale rows shows that LIMIT saturation alone does **not** imply a false negative. Existing authorized Stage A measurements remain **33/60 detected eligible ticker-price samples**, 0/60 usable trade-price lookups and 27/60 neither; since 27/60 price-bearing ticker lookups hit LIMIT 8 and the overlap with those 27 negatives was not reported, **zero to 27** ticker false negatives remain logically possible (not measured). No repeat probe, host access, code/policy change or shadow is authorized. Evidence: `docs/evidence/phase-14-v4-linear-price-visibility-prospective-20261012.json`; test/bound interpretation: `docs/PHASE-14-V4-SUCCESSOR-ROOT-CAUSE-TRIAGE-20261012.md`. Original 7–8 October causes still unknown. One settled losing paper trade, economic acceptance NOT MET, **Phase 14 HOLD**, live trading disabled.

**12 October 2026 Phase 14 V4 Bybit-linear prospective visibility, approved Stage A completed (#589):** The operator supplied a successful, one-time, hash-verified, **no-install stdin** execution of `scripts/report_v4_linear_price_visibility.py` against read-only production PostgreSQL using the frozen V4 release. In **60** short wall-clock samples, **33/60** had an eligible Bybit-linear `ticker.data.lastPrice`, **27/60** had neither ticker nor trade price eligible, and **0/60** found a usable price-bearing `publicTrade` row in the 12-second lookback. A general ticker event met *timestamp* checks in **53/59** successful `ticker_any` samples but might not contain price. **One ticker-any query timed out** (180 attempts/179 successes); `ticker_any` reached its eight-row cap in **59/59** successful queries and `ticker_last_price` in **27/60**, so measured eligibility may be undercounted through truncation. The read-only/no-write/no-order safety markers and execution completion passed. This is **new 12 October sampling**, not reconstructed 7–8 October decision-time availability, commit visibility, or evidence of an exchange outage. Details and provenance: `docs/evidence/phase-14-v4-linear-price-visibility-prospective-20261012.json`, interpretation in `docs/PHASE-14-V4-SUCCESSOR-ROOT-CAUSE-TRIAGE-20261012.md`. The user authorized **one** bounded probe only; it has been completed. No second probe, runtime change, new shadow, source-age relaxation or trading is authorized. **Phase 14 HOLD**; one losing paper trade, economic acceptance NOT MET.

**12 October 2026 Phase 14 V4 successor root-cause triage (#589):** With exact offline 90/106 Bybit-linear-only source-ineligible markets, 105/106 Bybit-linear-affected, and 116 end-to-end missed decisions already reconciled, the completed synthetic tests confirm **mechanism possibility**, not historical causal allocation. Ranked evidence/discriminators and stop boundaries: `docs/PHASE-14-V4-SUCCESSOR-ROOT-CAUSE-TRIAGE-20261012.md`. Highest-value source question: was an eligible Bybit-linear price parseable, cutoff-compliant and **committed/visible** at query time, versus genuinely absent? Highest-value timing question: what was the **actual per-market processing start** versus batch-captured `now`, and how much time belonged to readiness, feature selection and inference? The immutable old event summary cannot answer those; do not invent allocations or rerun old production raw queries. **Next permitted action:** repository-only review and no more speculative historical attribution. Any new prospective paper-only measurement requires a separately reviewed, expressly authorized and bounded request. One settled paper loss, economic acceptance NOT MET, **Phase 14 HOLD**, no live trading.

**12 October 2026 exact offline Bybit venue-pattern closeout (#589):** The operator fast-forwarded the Mac to `main` `b3c2b72f` and re-ran the already merged exact-SHA-bound coverage reporter on the **previously authorized local** 7–8 October JSONL; supplied terminal output reconciled with `PHASE14_V4_OFFLINE_COVERAGE=PASS_EVIDENCE_ONLY`. Among **106** source-ineligible markets, **90 had only Bybit-linear source flags** (exact observed count, superseding the 90–91 aggregate bound), five had linear+spot, three linear+Coinbase, seven all three, and **one Coinbase only**. Of **105 Bybit-linear-affected**, anchor patterns were **51 current only, 33 market-start only, 21 both**. Early 00–12h was **86/144** source-ineligible versus **20/101** over 14–24h; the 12–14h bucket has no recorded terminal decisions. These descriptive patterns do **not** prove feed outage, parser failure, recovery or timing root cause. Operator-output provenance and exact combinations: `docs/evidence/phase-14-v4-successor-exact-venue-overlap-20261012.json`; no private raw JSONL committed or independent CI replay. Next: bounded repository-only assessment of timing/Bybit source-event hypotheses; **no second server access, raw DB queries, new shadow, trading, holdout or frozen-policy change** without separate authorization. One losing paper trade, economic acceptance NOT MET, **Phase 14 HOLD**.

**12 October 2026 V4 successor static-path follow-up (#589):** With the exact-hash-bound operator-supplied offline funnel evidence now merged, targeted repository-only review found that `scripts/run_v4_fresh_book_shadow.py` captures `now` **once before the whole sequential due-market batch** and reuses it for each market's `initial_lag`. Thus a market that actually starts after its two-second deadline can be tagged `feature_inference_lag_exceeded` rather than `decision_lag_exceeded`; the 116 terminal labels remain valid records but do **not** prove 116 independently slow feature/inference executions. Also, the core readiness probe's six source-anchor prefetches are invalidated by the full feature builder's prefetch cache clear, allowing duplicated lookup work; grouped SQL candidate rows contain full payloads with no per-request LIMIT. Bybit-linear missing flags (105/106 source-ineligible markets) can arise from source-time/received-at cutoff, unsupported/unparseable price data or absent/hidden events; **no feed outage or DB query root cause is established**. Static details, code references, limitations and safe next verification: `docs/PHASE-14-V4-SUCCESSOR-STATIC-PATH-FORENSICS-20261012.md`. Next safe step: synthetic offline classification and source-preload fixture tests, without modifying frozen runner, contacting production, replaying holdout or restarting a shadow. Phase 14 **HOLD**, trading disabled, economic acceptance NOT MET.

**12 October 2026 Phase 14 V4 successor offline coverage closeout (operator-reported, exact SHA bound):** After a one-time, explicitly authorized **read-only** host-file retrieval to the operator's Mac, the operator verified the preserved October 7–8 JSONL SHA-256 `d0478b4e8b16a0fa07a850ada583095d09f1c1e81dcfb407abeff07877fd3324` and ran the already-merged coverage reporter. Its supplied JSON and `PHASE14_V4_OFFLINE_COVERAGE=PASS_EVIDENCE_ONLY` marker reconcile 245 markets, 23 predictions, 116 missed, 106 source-ineligible, 14 evaluated, nine quote unavailable; **all 116 missed** were classified `feature_inference_lag_exceeded` (median lag 2.911243 s, p95 4.920635 s, max 11.73573 s vs frozen 2 s). **Bybit linear affected 105 of 106 distinct source-ineligible markets**, with venue overlaps (Bybit spot 12, Coinbase 11). Retry-deferrals 208, recovered zero, exhausted 101 (100 with Bybit linear flags), pending zero. The exact cause of feature/inference time and Bybit-source ineligibility is **not proven**. This was **not independently re-executed by ChatGPT or CI**; durable operator-reported aggregate evidence: `docs/evidence/phase-14-v4-successor-offline-coverage-forensics-20261012.json`. Previous 79–96 Bybit-linear-only range is superseded by the tighter **90–91** bound from measured venue overlap. Follow-up is **repository-only, bounded review of the frozen source-time feature read/inference path** and any existing archived (non-production) timing evidence; do not issue new production queries, replay consumed holdout, restart paper shadow, change thresholds/model, or authorize real trading. Economic outcome remains a single settled paper loss of USD 4.288530911232. **Phase 14 HOLD**.

**11 October 2026 successor source-only bounds and evidence handoff:** The frozen source-ineligible totals (106 markets; Bybit linear 126 reason occurrences; 27 Coinbase/Bybit spot reason occurrences including 10 distinct Bybit spot market-start-missing occurrences) mathematically bound the markets with **only Bybit linear reasons to 79–96** (74.5%–90.6% of source-ineligible markets). This is an **aggregate-derived bound**, not an observed market-level count or proof of a feed outage. The separately observed **116 missed decisions** are still unresolved. Repository-only offline coverage reporter strengthened through merged PRs #581–#585 can split missed-decision types, venue overlap and retry-exhaustion patterns, but **the original exact-hash successor JSONL is not supplied in the current workspace**, so the report has not been run on the preserved file. **Next action requires an independently authorized offline copy or a safe report produced from that copy.** Do not contact production to recover evidence under this note; no new raw query, shadow restart, final-holdout reuse, threshold change, live trading, or promotion. Phase 14 **HOLD**.

**11 October 2026 post-successor retention-forensics correction (repository-only):** A retrospective raw-event diagnostic run on 11 October over 7–8 October source cutoffs is outside the normal configured 24-hour hot PostgreSQL raw retention, while the additional 24-hour archive is not queried by the P&L reporter. Thus the prior 18 sampled `no_usable_event_near_cutoff` results **cannot establish missing live price feeds**; archival/retirement and actual DB visibility at diagnosis time were not independently verified. The October 7–8 shadow's real `source_ineligible` reason flags, negative one-trade paper P&L, and 24h completion evidence are unchanged. PR #581 has already merged an exact-hash-bound, evidence-only decision-funnel diagnostic, but the full successor JSONL is not currently present in the ChatGPT workspace; its 116 missed-decision split and distinct Bybit-linear affected market count remain **unmeasured**. A new repository-only guard makes future raw timing probes explicitly classify cutoff windows beyond configured hot retention without querying them. No new production access, model change, holdout reuse, new shadow, or trading action. Phase 14 HOLD.

**11 October 2026 V4 successor prospective paper-economic result:** The separately completed 7–8 October 24-hour V4 shadow `v4-fresh-book-shadow-20261007T200650Z-d58b2cbedc3f` passed strict duration/safety completion but a **single-run, read-only, exact-evidence-hash-matched** official-settlement P&L report yielded **one** modeled paper trade, **one** settled loss, `realized_pnl_usd=-4.288530911232` on `settled_fill_cost_usd=4.288530911232` (return on fill cost -100%, profit factor 0), **zero wins and zero pending settlements**. Among 245 seen markets, only 23 predictions occurred (14 evaluated, 9 quote unavailable); 116 decisions missed and 106 source-ineligible. Missing-source reason counts were especially concentrated in Bybit linear (126 reason occurrences of 153 total, with overlaps across markets). Six small source-timing reason samples of three apiece each reported `no_usable_event_near_cutoff`; this does not establish root cause. **The P&L reporter's PASS indicates successful safe execution, NOT profitable performance.** The single loss does not support population-level performance estimates; frozen-policy prospective economic acceptance is **NOT MET**. The completed shadow's 24-hour safety/duration acceptance remains PASS, while the original October 6 shadow remains incomplete. Keep Phase 14 **HOLD**, research-only zero money, live trading disabled, automatic promotion disabled, consumed holdout untouched, and frozen policy unchanged. Durable operator-reported assessment: `docs/evidence/phase-14-v4-successor-24h-paper-economics-20261011.json`. Next safe objective: bounded evidence-based source/funnel diagnosis; **no additional production query or mutation authorized by this note**.

**11 October 2026 V4 successor 24-hour shadow addendum:** A **separate** October 7–8 V4 fresh-book shadow `v4-fresh-book-shadow-20261007T200650Z-d58b2cbedc3f` has now **PASSED** the repository's strict 24-hour closeout verifier, including SHA-256 `d0478b4e8b16a0fa07a850ada583095d09f1c1e81dcfb407abeff07877fd3324`, exact frozen model `6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf`, duration 86,401.676702 seconds, source policy, all safety fields and terminal completion accounting. Of 245 markets seen, 23 predictions were produced, 116 decisions missed and 106 were source-ineligible; 14 predictions were evaluated and 9 had unavailable quotes; 208 retry deferrals produced zero recovered, 101 exhausted and zero pending. Database read-only, zero writes, zero order submissions, no wallet, no holdout reads, no refit or tuning all verified. **This passes only full-duration completion and runtime-safety acceptance, NOT paper profitability or prospective model quality.** Actual modeled fills/trades, official-label settlement, after-cost economics, quote availability and regime-level evaluation remain unassessed. The previously reported **original October 6** run remains separately **early-stopped and incomplete**. Evidence: `docs/evidence/phase-14-v4-successor-24h-shadow-strict-closeout-20261011.json`. Next: a bounded, single-run read-only paper-economics evaluation, **not** a shadow restart, consumed holdout rerun, threshold adjustment, or trading activation. Phase 14 HOLD, research-only zero money, live disabled and no automatic promotion continue.

**11 October 2026 V4 original-shadow forensic addendum:** The original October 6 source-time fresh-book paper shadow `v4-fresh-book-shadow-20261006T140323Z-f5c76576619c` **did not complete 24 hours**. A strict read-only closeout verifier found **0 completion records**. Follow-up operator-provided forensics found one start record, **58 source-ineligible events, 0 predictions, 0 evaluated paper trades**, last JSONL event at 18:49:01Z, and a systemd **Stopping** event at 18:53:57Z followed by successful deactivation at 18:53:58Z, about 4h 51m after service start. Systemd `Result=success` reflects clean service stop, **not** a complete paper run. The identity of the stop requester is not verified; an existing supersede-stop helper is consistent with the behavior but is not proven to have run. Original evidence remains preserved. The original 24h shadow and prospective performance acceptance **FAIL_INCOMPLETE**, with no P&L or quality conclusion. Do not fabricate a completion record, restart this run, rerun the consumed final holdout, or infer anything about a separate possible successor shadow without its own evidence. See `docs/evidence/phase-14-v4-original-shadow-early-stop-forensics-20261011.json`. The independent V4 collector stability/integrity and four fingerprint-replay checkpoints are separate and remain passed within their stated scopes. Phase 14 **HOLD**, live-disabled, zero-money and disabled automatic promotion continue.


**5 October 2026 superseding status:** the latest continuous fast-live v2 authorization `phase15-v3-fast-live-auto-continuous-v2-12h-9824a0b1-20261001T201044Z` was activated in production, expired at `2026-10-02T08:10:44Z`, and is now inactive/fail-closed. Read-only reconciliation found zero publications, zero network submission attempts, and zero real orders bound to that authorization; `bp-phase15-fast-live-source.service` and the Johannesburg receiver are inactive, the executor kill switch is engaged, and there are no unresolved result or settlement records. The one preserved historical fast-live attempt/fill belongs to the earlier 30 September authorization and is already recorded by `docs/evidence/phase15_v3_fast_live_completed_session_cleanup_20261001.json`. Durable reconciliation for the expired October session is `docs/evidence/phase15-v3-fast-live-expired-session-readonly-reconciliation-20261005.json`.

**5 October cleanup PASS:** the exact expired-zero-attempt cleanup ran from main `d0b1412023849ede8054beb9650d713037573730` using helper blob `0046d6a3636bcd3ffb917813cfc29de38404ee74`. It reconfirmed zero publications, zero network attempts, zero execution results, zero real orders, a clean ZA account with zero open orders, preserved the Johannesburg kill switch, removed the session Pub/Sub/runtime material, and verified that material absent afterward. Both fast-live services remain inactive. The one-shot cleanup authorization is consumed. Durable reviewed evidence is `docs/evidence/phase15-v3-fast-live-expired-zero-attempt-cleanup-20261005.json`.

**No new live session is currently authorized, and V3 live is not the active objective.** The expired cleanup must not be rerun. The active strategy-development path is the already-collected V4 Gate B v2 cohort: 1,662 frozen-plan markets and a frozen `full_v4_xgboost` / 240-second / identity / `min_edge=0.05` global policy. The 288-market final holdout was evaluated exactly once on authorized main `8c5e6018690f8c48dfcdb8280cc81610d0acf235` at `2026-10-06T10:55:38Z`; the guarded runner passed, wrote the durable attempt marker first, performed no DB writes/refit/tuning/reselection/activation, and kept real money at zero. That holdout is now permanently consumed and must not be rerun or replaced. Its immutable result has now been reviewed and recorded at `docs/evidence/phase-14-v4-gate-b-final-holdout-review-20261006.json`: 90.625% forecast accuracy, 84 frozen-policy trades, 77.38% traded accuracy, +11.675646 after assumed costs, and profit factor 2.3144. Bull and sideways/mixed were positive, while the evaluable bear regime was negative (-0.520976, PF 0.8682), so uniform regime robustness is not established. The [0.05,0.075) edge band was also negative, but the consumed holdout must not be used to raise the frozen `min_edge=0.05`. The run exposed a scikit-learn `SimpleImputer` artifact/runtime mismatch (1.9.1 artifact loaded under 1.9.0). The first explicitly authorized zero-money V4 paper-shadow attempt on main `8a472b752625477cf26969e776823a620a39c519` reached production runtime staging but failed closed before shadow start because the frozen-model loader observed scikit-learn 1.9.0 instead of required 1.9.1. No paper shadow, live trading, wallet/signing path, or real-money action started; that authorization is consumed. The staging/version-validation defect was fixed on main `e8d03a4b852449038a55cd428ece52a1d294ae7f`, but the second authorized attempt bound to `c1d9c9a34f826f075b8fff292d31d576676707c8` was launched from a stale local helper body: its internal `git pull` updated the checkout, while Bash continued executing the older script already loaded in memory. That stale body successfully installed scikit-learn 1.9.1, XGBoost 3.4.1, and joblib 1.5.3 into a temporary venv, then moved the venv and failed at `pip check` because the pip shebang still referenced the old temporary path. No paper shadow, live trading, wallet/signing path, or real-money action started; the second authorization is consumed. The launcher is fixed on main `5fe1d8782b2c9f95e71c3d5d297372a26dca4070`, but the third authorized attempt bound to `62dbf0d653d7434b6d0bc610009228efff29cb29` failed during exact runtime-version validation: the venv successfully installed scikit-learn 1.9.1, `xgboost-cpu` 3.4.1, joblib 1.5.3, and project dependencies, then `importlib.metadata.version("xgboost")` raised `PackageNotFoundError` because the installed distribution is named `xgboost-cpu`. The failure occurred before frozen-model validation or shadow start; the third authorization is consumed. The metadata verifier is fixed and the fourth explicitly authorized zero-money paper-shadow attempt, bound to exact main `f5c76576619c35e65fc8a317d47c8a31dd263950`, returned startup PASS. It validated the frozen scikit-learn 1.9.1 / `xgboost-cpu` 3.4.1 / joblib 1.5.3 runtime, loaded the exact frozen model SHA `6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf`, and started bounded run `v4-fresh-book-shadow-20261006T140323Z-f5c76576619c` for 86,400 seconds. This is a startup PASS only: the full 24-hour observation is not yet complete and no P&L/quality conclusion is available. The start record confirms read-only database access, no DB writes, no holdout-label read, no refit/tuning, no order submission, no wallet material, fast-live source inactive/disabled, live trading disabled, and real money $0. The fourth authorization is consumed. Durable startup evidence is `docs/evidence/phase-14-v4-fresh-book-shadow-start-20261006.json`.


**Historical context below is superseded by the 5 October status above.** Phase 15 previously authorized an additional frozen-V3 live canary through the private Telegram approval path; those earlier one-shot/session authorizations are consumed or expired and do not authorize a new live session now. The first canary remains officially reconciled at zero fill and its submission attempt remains consumed.

The private Telegram transport is now **production-active**. Exact activation from main `469049a9f6361f9c656e3d176029b10db7359689` with repaired helper blob `83157c6c04b9a996bab51012b4bda4dd31062320` passed after listener, stage, Pub/Sub, and activation-readiness checks all returned PASS. The existing topic/subscription were reused; publisher and all four Johannesburg transport services are active; global and Phase-15 `LIVE_TRADING_ENABLED` remain false; the executor is safe-idle; and no real order was submitted. The activation authorization is consumed, so activation must not be rerun without fresh explicit authorization.

There is **no pending second-canary intent**. Prepare-only watcher run `phase15-prepare-watch-20260926T144337Z-c2034bee` is no longer active: it stopped safely before preparing a candidate because the generic canary preparation layer still counted the reconciled first canary against the original lifetime one-attempt limit. The stop did not consume the second-canary network attempt and did not perform Telegram `APPROVE`, executor arm/invocation, or order submission. A repository fix now keeps legacy/default limits at one while letting only the source-truth-gated second-canary watcher recognize the reconciled first canary plus one authorized second slot. The corrected prepare-only watcher restart completed from exact main `f3de8c008d6a50584dc71a172ca4dcb4275f73d8` with all four authorized corrected blobs matching. Read-only status later showed run `phase15-prepare-watch-20260926T155010Z-f3de8c00` expired safely at `2026-09-26T17:50:48.084941Z` after its 7200-second window with reason `no_eligible_v3_trade_within_wait_window`. The service is inactive, no arm was attempted, no real order was submitted, and the second-canary submission attempt remains unconsumed. That one-shot authorization has now been exercised successfully. New run `phase15-prepare-watch-20260926T195409Z-271b35ff` started PASS from exact main `271b35fffdb3ccff1490e213672dfa98b7583d75`; the service is active on `bp-recorder`, `NO_REAL_ORDER_SUBMITTED=true`, `ARM_AUTOMATED=false`, and `SUBMISSION_AUTOMATED=false`. The restart authorization is consumed.

A later read-only audit found **three fresh frozen-V3 paper trades** during that watcher window. All three were `trade=true` and `executable=true`, but each live-canary risk decision failed only with `reconciliation_blocked`. The missing first-canary post-submission ledger row has now been repaired in production from exact main `38b6081621eab59aecafcdb0628e1d68e9497827` using helper blob `b21456d222f8256b381d43d70c1373bbbee6d78d`. The helper freshly re-verified official zero fill, then persisted reconciliation `live-reconciliation-81489372163985723f74f2003c7ef1d7` with `unresolved_count=0` and `critical_count=0`. No Telegram `APPROVE`, executor arm/invocation, or order submission occurred, and the repair authorization is consumed.

The second canary preserves the frozen V3 **$5 target notional** under the existing hard **$10 per-market ceiling**. Hard limits remain $10 max trade, $10 total exposure, $10 daily loss, one consecutive loss, one accepted order, one network submission attempt for the authorization, and a 2-second order TTL/cancel attempt. No stake growth, V3 tuning, V4 mutation, or broad autonomous live rollout is authorized.

The dedicated execution host remains `bp-v3-canary-exec` in GCP `africa-south1-a` (Johannesburg). Wallet/signing material is permitted only on that host, never on the existing US BP host, in Git, or in chat.

The active path is now: fresh NEW frozen-V3 candidate → prepare-only watcher → private Telegram notification → exact Telegram APPROVE → signed source-truth/dispatch verification → authenticated Pub/Sub transport → one-shot Johannesburg claim → exact executor handoff verification → one executor invocation → stop and officially reconcile before any third live action. Global `LIVE_TRADING_ENABLED` remains false so unrelated execution paths stay closed.

### Historical Gate A acceptance

V3 Gate A remains accepted **PASS**. Sanitized production evidence is frozen at `docs/evidence/phase-14-v3-gate-a-production-20260913.json`. It covers 17 markets / 68 immutable V3 rows beginning at `2026-09-13T13:45:00Z`, with coverage hash `32c283a7769681ebe5b2e0d1fe255ad6c38aa5b0301303f8fe86f4e7b2278ffb`, zero future-cutoff violations, zero Polymarket predictor keys, and complete current-state availability for Coinbase, Bybit spot, and Bybit linear. No model training or activation occurred as part of that acceptance.

The historical Gate B v1 preregistration design was approved at commit `c9e179c91ea990ca4a25a13f69fc5932811fb32a` and froze the original epoch ending `2026-09-16T13:45:00Z`. That historical evidence remains immutable even though v1 is now retired for policy-selection execution.

### Gate B v1 is retired for policy selection

The historical `v3-gate-b-preregister-v1` contract froze epoch `2026-09-13T13:45:00Z` through `2026-09-16T13:45:00Z` and required separate hash-bound `diagnosis` and `consumed_v2_final_holdout` exclusion manifests.

The exact 84-trade diagnosis identities were not durably frozen before that epoch began. Repository and host-side recovery found the diagnosis methodology but not a valid exact identity list, cohort hash, or replay-safe snapshot. Do not reconstruct the cohort from current database state, later settlements, outcomes, inferred timestamps, approximate date windows, or a synthetic/empty manifest.

Therefore `v3-gate-b-preregister-v1` is **non-executable for model/policy selection**. Do not run its readiness or planning path. Data from its `2026-09-13T13:45:00Z` to `2026-09-16T13:45:00Z` epoch may be retained only as immutable engineering, source-availability, leakage, and coverage evidence.

The complete consumed-V2 final-holdout contamination boundary has separately been recovered as 48 unique historical condition IDs with zero overlap across the two consumed plans. Those identities remain permanently non-reusable historical contamination evidence.

### Approved Gate B successor

The approved successor design is `docs/superpowers/specs/2026-09-14-phase-14-v3-gate-b-successor-preregistration.md`, with durable decision evidence at `docs/evidence/phase-14-v3-gate-b-successor-preregistration-20260914.json`.

Its exact identity is:

```text
research_plan_version = v3-gate-b-preregister-v2
dataset_version       = supervised-core-v3-btc-native-v1
feature_version       = core-v3-btc-native
label_version         = official-outcome-v1
epoch_start           = 2026-09-16T13:45:00Z
epoch_end             = 2026-09-19T13:45:00Z
```

The successor preserves the v1 predictor set, model candidates, calibration/economic search rules, 24h/6h/6h/6h train-validation-test-step geometry, exactly five ordinary folds, one-market embargo, and final 12h reserved holdout.

Its contamination boundary is structural: only markets with `market_start_at >= epoch_start AND market_start_at < epoch_end` may participate. Every pre-epoch market is ineligible for successor train/validation/test/final-holdout membership. Historical diagnosis and consumed-V2 identities remain evidence but are not successor runtime selection inputs.

Readiness remains outcome-blind and planning remains feature-only/read-only. The successor preregistration authorizes neither model fitting nor final-holdout access.

### Successor runtime implementation checkpoint

The repository implementation of `v3-gate-b-preregister-v2` is complete at runtime checkpoint `659d9524fe8bfeba182b7cf7c8d9b664280f7562`. Exact-head CI run `34841954823` passed the full suite, deployment-asset validation, research-mode health check, and dashboard checks. The runtime removes historical exclusion manifests from successor readiness/plan APIs and CLI, while preserving the structural half-open epoch boundary, outcome-blind readiness, feature-only planning, PostgreSQL read-only transactions, no-clobber plan output, and the frozen search/economic contract.

This checkpoint is repository-only. It did not run readiness or planning against production, inspect successor outcomes/labels, fit models, access/evaluate the final holdout, activate a model/paper policy, mutate production, enable live trading, or change money limits.

## Immediate next task

1. Keep the October V3 fast-live session **expired, inactive, and fully cleaned**. Do not restart it, rerun its cleanup, or generate a replacement live authorization while V4 paper validation is the active objective.
2. Treat the V4 Gate B v2 final holdout as **permanently consumed and reviewed**. Do not rerun it, replace it, refit/recalibrate from it, raise `min_edge`, or add side/regime filters from its results.
3. Preserve the exact frozen V4 policy: `full_v4_xgboost`, 240 seconds, identity calibration, global `min_edge=0.05`. The reviewed aggregate holdout supports prospective zero-money paper observation, but the negative evaluable bear regime means this is not uniform-regime proof and not automatic promotion.
4. Treat the first three failed paper-shadow authorizations as consumed historical failures and the fourth authorization as consumed by the successful startup on exact main `f5c76576619c35e65fc8a317d47c8a31dd263950`.
5. Preserve the currently running bounded V4 shadow exactly as started: 86,400 seconds, frozen model/policy unchanged, read-only database access, no holdout reads, no refit/tuning, no order submission or wallet path, live trading disabled, and real money $0.
6. **Do not launch a fifth shadow, restart this one, or change model/timing/calibration/edge/sizing while the bounded run is pending completion.** The startup PASS is not a completed paper result.
7. After the bounded unit ends, use the strict read-only closeout bridge `bash scripts/deploy/phase14_v4_fresh_book_shadow_closeout_cloudshell.sh`. It is exact-run/model/runtime-bound, refuses while the shadow unit is active, requires the terminal `v4_fresh_book_shadow_completed` record plus the full bounded duration and zero-money safety fields, then runs the existing official-label P&L report on that exact JSONL only. The closeout verifier remains backward-compatible with this already-running pre-retry runtime. Do not run it before the unit ends. No automatic promotion or live authorization follows from startup or from a future paper result. If a later fresh paper shadow is separately justified and authorized after closeout, its launch preflight must verify the bounded source-visibility retry contract and the unchanged strict `received_at <= decision_at` cutoff before production contact.
8. Preserve the **historical production pass** and its fail-closed observation/storage context: frozen `v3-frozen-paper-v1` with `paper-execution-v3-frozen-v1` remains **active after concurrent-partition-retirement rollout PASS** at model SHA `124627e15cab3997b8abe54ec5237450d976ab5682953f45a1399a76b6dae0e7`, `min_edge=0.075`, and `real_money = $0.00`. The **V4 regime-aware feature collector** and **frozen V4 Gate B v1 future cohort** remain **prospective observation only** historical context; this does not reopen or replace the consumed V4 v2 holdout. The historical read-only observation command remains `bash scripts/deploy/phase14_observation_cloudshell.sh`. **Do not rerun the storage rollout**, do not tune V3 from paper results, and **keep all promotion/live boundaries closed**.
