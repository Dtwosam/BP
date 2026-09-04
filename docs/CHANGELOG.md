# Changelog

## 0.14.7 — 4 September 2026

Phase 14 partitioned raw-retention reliability is **engineering-verified on draft PR #50 and not production-migrated**. The 4 September storage incident showed that the Phase 3 archive-before-delete contract bounded logical rows but did not physically bound PostgreSQL relation files at the observed recorder rate: `raw_market_events` reached approximately 157 GB total (about 113 GB heap plus 44 GB indexes), root free space fell to roughly 15 GiB / 93% used, and the hourly maintenance timer had been inactive for several days. The recorder was intentionally stopped fail-closed and remains stopped during storage recovery.

The replacement PostgreSQL physical-retention design keeps the existing 24-hour hot raw plus 24 additional archive-hour policy but changes retirement to hourly `RANGE(received_at)` partitions. Global replay dedupe is preserved through a 16-way hash-partitioned `raw_event_dedupe` ledger and one shared event-ID sequence. Recorder writes claim dedupe identity and insert raw payload rows transactionally; missing target partitions fail the transaction without leaving orphaned ledger claims. Closed partitions may be retired only after the exact canonical gzip/manifest verifies, required compact feeds have advanced beyond the interval, and the live partition row count still matches the verified archive. Retirement then drops the child relation before bounded dedupe-ledger cleanup, so expired raw relation files can actually leave PostgreSQL storage accounting.

Storage supervision now persists maintenance runs in PostgreSQL and combines the unchanged 25 GiB warning / 15 GiB critical disk thresholds with maintenance freshness, current writable-partition availability, and raw-retention lag. One delayed hourly cycle remains tolerated; more than one extra cycle beyond the normal 24–25 hour hot window fails closed. `STORAGE_HEALTH_PATH` is portable and optional; production configuration is intended to point it at `/mnt/bp-data` so health checks cover the filesystem containing PostgreSQL rather than an unrelated root/archive path.

Explicit migration/rollback tooling was added. The migration retains the prior monolithic table as `raw_market_events_legacy`, validates aggregate and per-feed parity, streams exact raw-row and dedupe mapping comparisons in bounded memory, verifies sequence position and current + two future partitions, and runs duplicate/routing probes inside a transaction that is rolled back. The detached Cloud Shell rollout helper is exact-SHA guarded, requires the recorder already stopped, validates research/zero-money safety, the recovered 24–48h archive evidence, protected data-filesystem identity and headroom, and leaves `RECORDER_RESTARTED=false` with rollback material retained. It is intentionally self-blocking if PostgreSQL is not actually on the protected `/mnt/bp-data` filesystem.

Exact implementation head `44ba80fc9e6fdafb6e29c40b0d634d22c86d12e4` passed Ruff, deployment validation, health check, dashboard tests/typecheck/build, **903 Python tests**, Historical Backfill Smoke (`33874446822`), Live Recorder Smoke (`33874446877`), and Recorder Short Soak (`33874446903`). This is engineering evidence only. The partitioned production migration has not run and is not authorized by this PR. Production remained at `c29fe227f959305f67031e922ca659869a826c4f` when storage recovery began. The separately merged recorder-backpressure repair `5da8934bbb0700feb096b2143c53b9ce2d7aa5f9` also has not been rolled out because its precheck correctly stopped on unsafe storage health.

`MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, `MAX_DAILY_LOSS_USD=0`, `automatic_promotion=false`, the frozen 10-second selected-book freshness rule, Gate B block, Master live-gate `fail`, and Phase 15 block are unchanged.

## 0.14.6 — 3 September 2026

Phase 14 recorder backpressure reliability repair is **engineering-complete on draft PR #49 but not production-deployed**. Production diagnostics tied V2 source-data degradation to recorder overload: the shared raw-event queue reached 50,000 during bursty Polymarket traffic, followed by ping timeouts, reconnect loops, and an explicit upstream `slow consumer: send buffer full` close. The repair adds configurable bounded raw-event writer concurrency through `RECORDER_WRITER_WORKERS` with application default `1`, preserving the existing 50,000 queue, 500-event batch size, and 0.25-second flush interval until a separately authorized rollout selects a production worker count.

Backpressure reporting is now coalesced into one `backpressure` start incident plus one `backpressure_recovered` summary per continuous overload episode, including duration and blocked-event count, instead of synchronously amplifying database pressure with one incident write per blocked event. Lossless bounded semantics remain: no event-dropping policy, sampling, V2 feature rewrite, durable spool, reconnect-policy change, or trading-path change is introduced.

TDD preserved explicit RED/GREEN evidence for writer concurrency, configuration wiring, and overload-episode behavior. The deterministic synthetic Polymarket burst regression drives approximately 1,000 events/second—materially above the observed production peak—so the single-writer fixture reproduces local slow-consumer failure while four bounded workers persist every unique event without reconnect/error and continue heartbeat/control handling. A separate unsustainable-overload test proves producers block rather than silently dropping events.

Fresh branch verification on implementation head `a0e6ccc2ec2120594f4fcd173cc0ecd34d8d92b9` passed CI, Historical Backfill Smoke, Live Recorder Smoke, and Recorder Short Soak. This is engineering evidence only, not production acceptance. `MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, `MAX_DAILY_LOSS_USD=0`, `automatic_promotion=false`, the frozen 10-second selected-book freshness rule, the Master live gate `fail`, and the Phase 15 block remain unchanged. Production rollout requires separate explicit authorization and a natural-load soak; rollback must never delete or rewrite collected evidence.

## 0.14.5 — 2 September 2026

Phase 14 Gate A timestamp-coherent V2 is now **production-accepted for research evidence collection only**. The separately authorized guarded rollout advanced `/opt/bp` from `be1f82f65d15b2e172495e6ae934ec9a78648c32` to `d077e45f24704e6038c947169c84527e954de975` and established canonical forward epoch `2026-09-02T12:18:02Z`. All seven established research services stayed active with `MODE=research`, `LIVE_TRADING_ENABLED=false`, and both real-money limits at zero.

Gate A host acceptance demonstrated real dedicated Polymarket WebSocket last-trade provider/receipt timestamps and dedupe identity, proved unrelated later market activity did not refresh the trade timestamp, and generated exactly four immutable `core-v2-last-trade` rows at 60/120/180/240 seconds for one completed post-epoch 5m market. Future-source-cutoff violations were zero. The outcome-blind coverage report observed one market/four rows and remained `policy_selected=false` / `automatic_promotion=false`; sanitized evidence is `docs/evidence/phase-14-v2-gate-a-rollout-20260902.json`.

The follow-up continuous V2 forward-coverage collector is now implementation-complete on `phase14-v2-gate-a-rollout-evidence` but **not deployed or enabled**. It adds restart-safe discovery of completed post-epoch 5m markets with missing approved V2 keys, preserve-existing immutable generation, descriptive coverage reporting, a thin research-zero-money CLI, hardened systemd oneshot/timer packaging, and an exact-head rollback-capable rollout helper. The service permits local PostgreSQL networking while systemd denies non-loopback IP traffic; rollback restores code/unit state only and never deletes or rewrites `market_features`.

Pre-packaging exact-head CI #1978 (`33639062997`) passed all 860 Python tests, Ruff, deployment-asset validation, health checks, dashboard tests/typecheck/build, wrapper compilation, and rollout-helper Bash syntax. Full diff review against deployed head `d077e45f24704e6038c947169c84527e954de975` leaves frozen V1 feature-service, live-prediction, calibration, and execution paths unchanged and introduces no migration, V2 label/outcome join, economic freshness/timing/model/calibration/edge policy, wallet/secret change, risk-limit increase, geoblock bypass, live activation, or Phase 15 implementation.

Fresh documentation-complete CI plus PR-triggered Historical Backfill Smoke, Live Recorder Smoke, and Recorder Short Soak remain review gates before merge. Production activation of `bp-v2-forward-coverage.timer` is a **separate explicit authorization** after review/merge; this packaging work does not run that rollout. Existing V1 evidence remains immutable and separate, selected-book freshness stays exactly 10 seconds, `automatic_promotion=false`, the Master live gate remains `fail`, and Phase 15 remains blocked.

## 0.14.4 — 2 September 2026

Phase 14 Gate A for the timestamp-coherent 5m `market_price` V2 path is now code/test complete on the isolated `phase14-market-price-v2-design` branch and is **under review, not deployed**. Gate A remains limited to source provenance and outcome-blind research features/diagnostics; it does not create or select a V2 trading policy.

Task 1 adds dedicated Polymarket `last_trade_price` provenance to compact state: exact trade price/size/side, provider source timestamp, BP receipt timestamp, and raw-event dedupe identity survive reduction and are not refreshed by unrelated later book/price-change events. Task 1 passed full CI #1913. Task 2 adds the exact-token, timestamp-coherent as-of V2 source reader, requiring dedicated valid source/receipt timestamps at or before feature time and failing closed on malformed, missing, future, or invalid-price evidence; full CI #1918 passed.

Task 3 adds the separate immutable 5m feature version `core-v2-last-trade` at exactly 60, 120, 180, and 240 seconds after market start. Last-trade source/availability ages are descriptive and receive no Gate A economic freshness cutoff; executable book fields reuse the frozen V1 book-state semantics. Future-data perturbation, immutable rerun, exact-token isolation, static-target loading, and outcome/source-isolation tests passed in full CI #1934. Task 4 adds deterministic read-only coverage reporting over V2 feature rows only, including availability/age/book diagnostics and coverage hashing while hard-coding `policy_selected=false` and `automatic_promotion=false`; full CI #1939 passed.

V1 invariants were independently checked against current `main`: `FEATURE_VERSION="core-v1"` is unchanged; `src/bp_engine/features/service.py` is byte-identical; the complete `src/bp_engine/live_prediction` and `src/bp_engine/calibration` subtrees are byte-identical; and selected-book freshness remains exactly 10 seconds. The Gate A diff contains no migration, live-order activation, wallet/secret change, risk-limit increase, geographic bypass, Phase 15 implementation, V2 live prediction, V2 paper execution, V2 calibration, or V2 `min_edge`/timing/freshness/model-policy selection.

No production recorder rollout or forward `core-v2-last-trade` collection has occurred, so no production V2 coverage result or evidence epoch is claimed. Existing V1 predictions/evaluations/paper execution/P&L remain a separate immutable evidence epoch and are not reinterpreted or blended into V2. Final review packaging still requires one fresh CI run on the documentation-complete branch head, a complete diff review against `main`, and a draft PR. Any subsequent recorder/V2 production rollout is a separate explicit authorization with its own smoke/soak/host-acceptance and rollback boundary.

Safety remains unchanged: `automatic_promotion=false`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`. The Master live gate remains `fail`, no geographic restriction may be bypassed, and Phase 15 remains blocked.

## 0.14.3 — 2 September 2026

Phase 14 read-only prospective diagnostics established a timestamp-coherence defect in the accepted 5m V1 `market_price` research path. The current live predictor derives raw probability from the newest first-party Polymarket CLOB `/prices-history` Up-token observation at or before `scheduled_at`, using one-minute fidelity, while the edge engine compares that probability with a separately observed fresh selected-side WebSocket best ask. A 27-settled-trade timing probe found probability ages of 33–51 seconds while selected-book ages were approximately 0–1 second for every trade. This mismatch can turn ordinary late-market movement into very large apparent executable edge.

The diagnosis is consistent with the frozen historical contracts rather than a new paper-execution bug. `core-v1` exposed Polymarket token-price staleness but imposed freshness only on compact book/state; the accepted Phase 7 `market_price` champion consumes `pm_up_price`; Phase 8/9 selected timing, calibration, and edge policy under that asynchronous source contract; and Phase 10 prospectively materialized the same semantics. Existing V1 predictions, evaluations, paper orders/fills/settlements, reconciliation, and P&L remain immutable and valid evidence of the deployed V1 pipeline. They are not rewritten or discarded, but they must not be blended with a corrected V2 profitability epoch or used to choose V2 freshness/calibration/edge parameters.

The latest read-only prospective-evidence reporter run on deployed head `be1f82f65d15b2e172495e6ae934ec9a78648c32` observed 576 evaluated predictions and 25 settled paper trades. Realized after-cost P&L was `-53.761912629631` USD total and `-2.15047650518524` USD mean per settled trade. The deterministic 10,000-resample bootstrap 95% interval for mean P&L was `[-3.3860459535627063, -0.6900968267728099]`, still entirely below zero, so `positive_after_cost_profitability=fail`. Reconciliation remained `OK` across 63 paper orders and 63 trade signals with zero violations. Raw Brier/log-loss were `0.12787709740883035` / `0.3931367386374861`; calibrated Brier/log-loss were `0.13011990529718795` / `0.4065020971145606`. Calibration and sample sufficiency remain `insufficient_evidence` because no approved numerical prospective thresholds exist. The Master live gate remains `fail`.

The approved research direction is a versioned timestamp-coherent market-price V2 path using first-party Polymarket WebSocket `last_trade_price` evidence with its own source/receipt timestamp. Generic compact-state `last_event_at` cannot represent last-trade freshness because later book or price-change events may refresh state without refreshing the trade. Missing or stale last-trade evidence must fail closed to no-trade. Midpoint, selected ask, opposite-token transforms, and untimestamped REST last-trade responses are not silent substitutes.

No new probability freshness threshold is selected from the 27 prospective failures. The existing selected-book 10-second freshness rule remains frozen. The V1 calibrator and minimum-edge threshold are not automatically carried forward because they were selected under the asynchronous V1 eligibility contract. V2 requires a new leakage-safe chronological research/validation chain and a separate prospective shadow-evidence epoch; if independent historical timestamped last-trade evidence is insufficient, the correct V2 policy is `no_trade` until prospective evidence accumulates.

This work remains Phase 14 research. `automatic_promotion=false`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0` remain mandatory. No geographic restriction may be bypassed, Phase 15 remains blocked, and any future controlled live launch still requires every Master live-gate item plus separate explicit real-money authorization.

## 0.14.2 — 31 August 2026

Phase 14 prospective outcome/evaluation sync closes the evidence-ingestion gap exposed by the 0.14.1 production-host report. That report correctly returned `PROSPECTIVE_EVIDENCE_HOST_REPORT=PASS` while observing zero prediction evaluations and zero paper settlements. Root-cause tracing established the missing production link: paper settlement waits for an immutable live-prediction evaluation; evaluation waits for the canonical `official-outcome-v1` label; and that label waits for a preserved resolved Gamma snapshot. Newly completed prospective predictions had no always-on post-resolution snapshot acquisition path.

The new `bp_engine.prospective_outcomes` service and CLI reuse the existing canonical outcome chain rather than introducing another resolution source. Each cycle first consumes any canonical labels that already exist. It then selects only ended immutable predictions still missing evaluation, fetches each exact market by slug from official Polymarket Gamma, leaves missing or unresolved markets pending without writes, and validates condition ID, slug, horizon, market window, and Up/Down token identity before persisting anything. Resolved payloads are stored through the existing immutable historical Gamma-snapshot repository, passed through the existing `official-outcome-v1` label generator, and finally through the existing append-only live-prediction evaluator. Completed evaluations are excluded from repeat network fetching.

The work also exposed and fixed a dormant provenance-compatibility defect: historical snapshot SHA-256 values use the established `sha256:<hex>` representation while the live-evaluation ledger previously accepted only bare 64-character hex. The evaluation boundary now removes only the optional `sha256:` prefix and still requires an exact 64-character lowercase hexadecimal digest. No tolerance, hash weakening, historical rewrite, or alternate semantic identity was introduced.

The runtime surface is intentionally narrow and money-disabled. The CLI exposes only `once` and repeated `run` execution, reuses the existing `RESEARCH` / `LIVE_TRADING_ENABLED=false` / zero real-money-limits safety guard, and exposes no wallet, signing, order, promotion, or live-enable options. A hardened `bp-prospective-outcomes.service` definition is included for later permanent rollout; unlike the paper worker it permits outbound network access because official Gamma lookup is required, while remaining unprivileged and systemd-hardened.

A separate exact-head Google Cloud Shell acceptance helper is deliberately non-deploying. It requires the existing paper worker to be active, records the live-predictor service state without requiring activity, requires that predictor state to remain exactly unchanged, validates research/live-disabled/zero-money settings, fetches and runs the exact candidate from a detached worktree with the existing permanent Python environment, performs one bounded prospective-outcome cycle followed by one bounded paper-execution cycle, and proves the deployed `/opt/bp` checkout is unchanged. It performs no package installation, migration, service start/stop/restart, checkout/reset, or daemon installation. The helper may append canonical official snapshot/label/evaluation evidence and paper settlements derived by the existing paper worker; it cannot create real-money side effects.

TDD preserved explicit RED/GREEN evidence. The initial clean service RED at `233b35a1` failed only for missing `bp_engine.prospective_outcomes`. A later GREEN cycle exposed the prefixed-snapshot-hash compatibility defect before the complete service path passed. CLI RED `789e40723d1bec13d7a08f03d9b079695f299b0b` passed Ruff and 778 existing tests while failing only the three intentionally missing CLI contracts. Deployment RED on `6793f6b769cdde0e0eb6b2afaa74186337e5f56b` reached pytest and failed only for the absent systemd unit and Cloud Shell helper. Implementation head `eee3dc56ee6d8271f4ee1c41357f2ecb4efb2374` then passed the full Python/PostgreSQL and dashboard CI lanes. A final CI-coverage RED at `3b84c11dd6f52eaf8f3a17746d8e11e12d7e71ce` produced exactly one failure because the new helper was not yet included in shell-syntax validation; after adding the `bash -n` gate, exact-head CI run `33378572573` passed completely on implementation checkpoint `22663baa8105ca9c5768bc6defb0f51605eb2130`, including 784 Python tests, Ruff, deployment validation, health, dashboard tests, TypeScript typecheck, and production build.

A later acceptance-quality review found that the first host helper could technically PASS when no ended unevaluated prediction existed, which would not prove the new production path had actually executed. TDD tightened that boundary without changing runtime semantics: RED commit `685bcbd91fcee51fcc14584d334c4b0311baac1c` passed Ruff and 784 existing tests while failing only the new no-op-acceptance contract. GREEN commit `bfe1cac144cf3fc9d91d2e057faea9d8117434d6` requires at least one candidate, at least one resolved market, candidate/snapshot accounting parity, canonical label coverage, and a newly created immutable evaluation for every resolved candidate; PR-context CI run `33380984760` then passed all 785 Python tests, Ruff, deployment validation, health, dashboard tests, TypeScript typecheck, and production build. This is only an acceptance-path exercise requirement, not a prospective sample-size, profitability, calibration, or live-promotion threshold.

The first production-host acceptance attempt ran on candidate `c11000bf97bcfe93b91d17134c43bbd10a5791ef` on 31 August 2026 and failed closed before outcome processing with `REASON=predictor_service_not_active_before`. Investigation showed that Phase 10 had host-accepted the predictor through a temporary `/run/systemd/system` unit but had never established a permanent predictor installation. The failure is retained as operational evidence rather than erased. A corrective TDD cycle changed only the acceptance precondition: RED `040fc2b6a322abb58b1aa9e27025ad687b5502c5` passed Ruff and 784 existing tests while failing only the new predictor-neutral contract; GREEN `8d04a38c366370835a1c530c4aa542ed8521a3b2` passed all 785 tests in CI run `33382679725`, with deployment validation, health, dashboard checks, Historical Backfill Smoke #486 (`33382679677`), Live Recorder Smoke #593 (`33382679679`), and Recorder Short Soak #558 (`33382679682`) also passing.

Corrected exact-head production-host acceptance then ran on candidate `94afff004fcbc2ed37af0297d37c51ab50ba7098` and returned `PROSPECTIVE_OUTCOME_SYNC_HOST_ACCEPTANCE=PASS`. The sync observed 54 ended unevaluated candidates, resolved all 54 through official Gamma, and appended 54 new immutable snapshots, 54 new canonical labels, and 54 new immutable evaluations with zero pending markets. The deployed `/opt/bp` checkout remained unchanged at `0189ff70fc628c71ab7c503bac369c34bf5ce8bc`; the paper service remained active; the predictor was inactive both before and after; `MODE=research`; `LIVE_TRADING_ENABLED=false`; and both real-money limits remained zero. The bounded paper cycle examined 54 predictions, created no new orders/fills/terminal events/settlements, and observed three existing orders, three existing terminal events, four existing settlements, 51 skipped predictions, and cash `92.207577336709000000`. Sanitized host evidence is stored in `docs/evidence/phase-14-prospective-outcome-sync-host-acceptance-20260831.json`.

This PASS proves the canonical post-resolution ingestion path executes on production evidence; it is not a profitability, calibration, sample-sufficiency, or live-readiness claim. The read-only prospective-evidence reporter must be rerun against the newly populated evaluation/settlement ledgers before any economic interpretation. Permanent installation of the live-predictor and prospective-outcome daemons as a pair has **not** been run. The Master live gate remains `fail`, Phase 15 remains blocked, `LIVE_TRADING_ENABLED=false`, and both real-money limits remain zero.

After the outcome-sync acceptance, the read-only prospective-evidence reporter was rerun on exact candidate `de907d324c7ee4ec46e2dfef1eb516dbb3fa8348`. It returned `PROSPECTIVE_EVIDENCE_HOST_REPORT=PASS` with the deployed checkout still at `0189ff70fc628c71ab7c503bac369c34bf5ce8bc`, the paper service active, live trading disabled, and both real-money limits zero. The newly populated evidence ledger contained 54 prediction evaluations and two settled prospective paper trades. Realized after-cost P&L was `-7.792422663291` USD total and `-3.8962113316455` USD mean, with a deterministic 10,000-resample bootstrap 95% mean interval `[-4.285508316075, -3.506914347216]`; prospective profitability therefore remains `fail`. Raw/calibrated Brier were `0.11328198148148148` / `0.10868378084722523` and raw/calibrated log loss were `0.3669084283864382` / `0.35286272448721295`, but calibration remains `insufficient_evidence` because no prospective numerical acceptance threshold is approved. Sample sufficiency also remains `insufficient_evidence`; reconciliation remained `OK` with zero violations and stays `pass`. Sanitized evidence is stored in `docs/evidence/phase-14-prospective-evidence-host-report-post-outcome-sync-20260831.json`. The Master live gate remains `fail`, Phase 15 remains blocked, and no promotion or live activation occurred.

The separate permanent research-runtime rollout then proceeded under D-030/D-031. Its first production attempt on candidate `196519555bed8f68d37654bd171dac23f681fd52` failed closed before mutation with `REASON=deployed_checkout_not_clean`. Read-only inspection showed established dashboard-generated runtime residue: tracked `apps/dashboard/next-env.d.ts` and `apps/dashboard/tsconfig.json` modifications plus untracked `.node/`, `apps/dashboard/.next/`, `apps/dashboard/node_modules/`, and `apps/dashboard/tsconfig.tsbuildinfo`. A test-first correction permits only those explicit generated paths, rejects every other tracked/untracked checkout status entry and any candidate collision with preserved runtime paths, and restores the two tolerated tracked generated files during rollback. The residue-regression RED checkpoint was `d731d2896e476ee082e6d39d47305fe08ecc97b3`; the corrected final pre-host candidate `d2b2d515a4b982c691360fa1c6c46a461a665ff9` passed CI #1661 (`33394458434`), Historical Backfill Smoke #528 (`33394458466`), Live Recorder Smoke #635 (`33394458523`), and Recorder Short Soak #600 (`33394458454`).

Permanent research-runtime installation subsequently passed on exact candidate `d2b2d515a4b982c691360fa1c6c46a461a665ff9` with `/opt/bp` advanced from `0189ff70fc628c71ab7c503bac369c34bf5ce8bc` to the exact candidate. `bp-live-predictor.service` and `bp-prospective-outcomes.service` are both active and enabled; recorder, PostgreSQL, dashboard API, dashboard web, and paper execution all remained active. The root-controlled safety environment remains `MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`. Host-local evidence is `/var/lib/bp/evidence/phase14-prospective-runtime-install-20260831T131003Z.txt` and sanitized repository evidence is `docs/evidence/phase-14-prospective-runtime-install-host-acceptance-20260831.json`. This is operational evidence-continuity infrastructure only: the negative prospective profitability evidence remains unchanged, no model was promoted, the Master live gate remains `fail`, and Phase 15 remains blocked.

PR #15, `Fix Polymarket rotating subscriptions after reconnect`, was independently reviewed and merged on exact head `f1d7b72022b7dc484d8ecad3e8fc80e4f1639815` as merge commit `be4d866b46cbe13a4f12f43e580486ab46c0ad28`. The repair keeps the recorder's retained Polymarket market subscription synchronized with dynamic `subscribe`/`unsubscribe` rotations so reconnects restore the current token set instead of the startup set. Isolated real-websocket acceptance passed, controlled recorder-only rollout passed, and two natural production reconnect cycles were followed by fresh two-sided books for both active 5m and 15m markets. The frozen 10-second selected-book freshness rule was not changed.

A stale shell `ERR` rollback trap was later triggered by an unavailable `gh` command and safely returned the host to the previous deployment `d2b2d515a4b982c691360fa1c6c46a461a665ff9`. After PR #15 merged, production was deliberately restored to the merge commit `be4d866b46cbe13a4f12f43e580486ab46c0ad28`; all seven runtime services were active afterward and the root-controlled safety boundary remained `MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`.

A read-only freshness-policy snapshot beginning at the recorder's final merged-deployment `ActiveEnterTimestamp`, `2026-08-31T16:29:33Z`, observed 18 predictions: 14 on 5m and 4 on 15m. The corrected 5m decision distribution was 7 `edge_below_minimum` (50.0%), 5 `trade` (35.7%), and 2 `selected_book_missing` (14.3%); all four 15m predictions were `policy_no_trade`. Both `selected_book_missing` rows had reconstructed two-sided selected books and failed only the frozen event-age rule, at 12.57 seconds and 16.05 seconds respectively. There were zero `NO_STATE`, zero stale one-sided/incomplete-book cases, and zero fresh-<=10-second-but-missing inconsistencies. The first exploratory percentage query used an incorrect grouped window denominator and produced impossible percentages above 100%; those percentages were discarded, while its counts were retained and the corrected percentages were rerun separately.

This n=2 stale-book sample is evidence that the 10-second contract may be restrictive for 5m execution coverage, but it is insufficient to justify a policy revision. No threshold, feature, model, edge rule, execution rule, or historical/prospective result was retuned. The correct next action remains to keep the 10-second rule frozen and continue money-disabled prospective evidence collection, then rerun the same classification against a materially larger post-merge sample with the evidence start fixed at `2026-08-31T16:29:33Z`. Sanitized evidence is stored in `docs/evidence/phase-14-reconnect-freshness-post-merge-20260831.json`. The Master live gate remains `fail`, Phase 15 remains blocked, and no real-money authorization exists.

## 0.14.1 — 30–31 August 2026

Phase 14 prospective-evidence follow-up adds a separate read-only reporting path over existing immutable paper settlements, live prediction evaluations, and paper reconciliation evidence. The reporter surfaces settled/evaluated sample sizes, realized after-cost paper P&L, a deterministic 10,000-resample bootstrap 95% interval for mean realized P&L, raw/calibrated Brier and log-loss means, reconciliation, and the existing Phase 14 Master live-gate snapshot. It does not modify the paper worker or any execution, prediction, evaluation, research, or live-readiness ledger.

Evidence gates emitted by the reporter are restricted to `pass`, `fail`, or `insufficient_evidence`. The implementation does not invent a fixed minimum paper sample or a numerical prospective-calibration acceptance threshold because neither is approved by the canonical spec. `automatic_promotion=false` is enforced, and the CLI refuses to run unless `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`.

An exact-head Google Cloud Shell helper runs the candidate from a detached worktree, checks the paper service and money-disabled interlocks before reporting, verifies the deployed `/opt/bp` checkout remains unchanged, and performs no package install, migration, service restart, service stop, or production-checkout mutation. TDD established clean RED checkpoints for the missing report core, missing database/CLI read path, and missing Cloud Shell asset before each implementation slice. Exact-head CI on implementation candidate `17b742e3fa07af39348e9deb7ff1689040c1a5a6` passed the Python/PostgreSQL lane, deployment validation, health check, and dashboard tests/typecheck/build; the final cleaned pre-host head `fc63f104f13ed3922061d1d12cd33cb927ccfa2a` also passed both CI jobs.

On 31 August 2026, the exact-head production-host report ran on candidate `fc63f104f13ed3922061d1d12cd33cb927ccfa2a` and returned `PROSPECTIVE_EVIDENCE_HOST_REPORT=PASS`. The deployed `/opt/bp` checkout remained unchanged at `0189ff70fc628c71ab7c503bac369c34bf5ce8bc`, the paper service remained active, `LIVE_TRADING_ENABLED=false`, and both real-money limits remained zero. Reconciliation was `OK` with zero violations across three paper orders, three trade signals, and 51 no-trade signals.

The host report observed zero settled prospective paper trades and zero prediction evaluations. Realized after-cost total P&L was therefore `0`, while realized mean P&L, its 95% interval, and raw/calibrated Brier/log-loss metrics were unavailable. The reporter accordingly classified prospective sample sufficiency, prospective after-cost profitability, and prospective calibration as `insufficient_evidence`, while order execution/reconciliation remained `pass`. This does not override the existing Master live-gate snapshot: the Master gate remains `fail`, Phase 15 remains blocked, real-money trading remains disabled, and no promotion or live activation occurred. Sanitized host evidence is stored in `docs/evidence/phase-14-prospective-evidence-host-report-20260831.json`.

## 0.14.0 — 30 August 2026

Phase 14 — Live Readiness V1 — reached engineering closeout after fresh exact-head CI/smoke gates and non-spending production-host acceptance on candidate `5854e3003aa3340ce3733bf4532e204c1ec55836`. The phase adds fail-closed live-readiness settings and immutable models, official `polymarket-client` integration behind a narrow adapter, official geoblock checking, activation/kill-switch and secret-boundary controls, deterministic live risk rules, reconciliation, and read-only CLI/dashboard diagnostics. No Phase 14 test or host-acceptance path may place, cancel, sign, fund, approve, or settle a real-money order.

Production acceptance returned `PHASE14_HOST_ACCEPTANCE=PASS`, `SERVICES_ACTIVE=PASS`, `SDK_IMPORT=PASS`, `INTERLOCK_BLOCKS_SUBMISSION=PASS`, `RISK_RULES=PASS`, `RECONCILIATION=PASS`, and `REAL_ORDER_SIDE_EFFECTS=0`. Production remained `RESEARCH` with `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`. The official Polymarket geoblock response was `GEOBLOCK_BLOCKED=true`, so geographic eligibility fails closed and no bypass/relocation/proxy behavior is permitted.

During Phase 14 host work, a storage incident exposed stale compact-state amplification: retired market/token keys remained in reducer state and were rewritten every second. The recorder was fixed test-first to stop persisting stale states, disk-critical behavior was hardened to stop the recorder fail-closed, and production data was compacted after a pre-recovery snapshot. Post-recovery verification observed the recorder active, disk-health timer active, substantial free space restored, recent rows flowing, and zero stale rewrites in the verification window. These operational repairs did not enable live execution.

The explicit Master live-gate closeout is `PHASE_14_ENGINEERING_COMPLETE_LIVE_GATE_BLOCKED`. Historical reproducibility, no-known-leakage controls, chronological splits, risk/kill-switch testing, and execution/reconciliation testing are recorded as `pass`. Walk-forward economic stability, sufficiently large prospective paper evidence, and prospective calibration are `insufficient_evidence`. Positive after-cost profitability is `fail` because the accepted 5m untouched final holdout is negative after assumed costs and Phase 13 added no positive economic uncertainty or independent confirmation. Geographic/compliance eligibility is `fail` because `GEOBLOCK_BLOCKED=true`; explicit live authorization is also `fail` because no real-money transition has been authorized.

Sanitized closeout evidence is stored in `docs/evidence/phase-14-closeout-20260830.json`. Phase 15 controlled live launch is not permitted. The next allowed work is continued money-disabled prospective paper evidence and a later complete Master live-gate reassessment.

## 0.13.0 — 29 August 2026

Phase 13 — Improvement Loop V1 — closed after exact-head CI and production-host acceptance on operational candidate `4dcdf8955b2c79ea9f130fec5a0dcceef915a678`. CI run #1387 passed 637 Python tests, Ruff, deployment validation, health in research/live-disabled mode, and the dashboard test/typecheck/production-build lane.

The phase adds immutable experiment, evaluation, evidence, and promotion-decision records; exact Phase 9 → Phase 8 → Phase 7 champion provenance loading; evidence-role, temporal, known-holdout, and fresh-confirmation reuse guards; deterministic paired-market bootstrap uncertainty; calibration guardrails; a network-free research CLI; and deliberate `reject_challenger` / `keep_champion` / eligible research-paper promotion semantics. Promotion eligibility never mutates the champion automatically and cannot activate live trading.

The first concrete experiment froze a 5m validation-selected max-spread abstention guard over the existing Phase 9 edge mechanics. The spread grid was `0.02, 0.04, 0.06, 0.08, 0.10, None` with deterministic validation-only tie-breaks. The immutable experiment is `phase13-exp-0c6f77ab575fdc75d517480285574ff8`; evaluation is `phase13-eval-4c7c0457409f7e29687c5b75139cd405`; challenger is `phase13-spread-55b1f388b3df86b83124d6f289cdd625`.

The challenger did not improve the accepted Phase 9 champion. Across 144 reused ordinary-OOS markets, both policies recorded 3 trades, `+0.148014` assumed-cost total P&L, calibrated log loss `0.33497222323288234`, and calibrated Brier `0.1064723920324928`. The deterministic 10,000-resample paired bootstrap produced economic delta `0.0` with interval `[0.0, 0.0]`; calibration deltas were also zero. No independent `fresh_holdout` or `prospective_paper` confirmation was available, so the evaluation was promotion-ineligible for `economic_uncertainty_not_positive` and `independent_confirmation_missing`.

Production acceptance returned `PHASE13_HOST_ACCEPTANCE=PASS`. It verified the exact immutable champion chain, idempotent experiment registration and evaluation, rejection of an ineligible promotion attempt, and the deliberate `keep_champion` decision `phase13-decision-8e32d904a1169e10bed2eb8f7a375637`. Recorder, PostgreSQL, dashboard API, dashboard web, and the money-disabled paper worker remained active; paper reconciliation was `OK` with zero violations; `execution_available=false`; `LIVE_TRADING_ENABLED=false`; and maximum real trade size and daily loss remained zero.

Sanitized closeout evidence is stored in `docs/evidence/phase-13-closeout-20260829.json`. Phase 14 — Live Readiness — is now the next permitted phase. This closeout is not a profitability claim and does not authorize real trading; the full live-readiness gate and explicit user authorization remain mandatory before any controlled live launch.

## 0.12.0 — 28 August 2026

Phase 12 — Paper Execution — closed after exact-head CI, isolated production-host acceptance, and permanent installation on operational candidate `159ce77af9a51ae208511d216bee52d5732cee3b`. CI run #1328 passed 564 Python tests, Ruff, deployment validation, health in research/live-disabled mode, and the dashboard test/typecheck/production-build lane.

The phase adds a generic execution contract and deterministic paper broker tied only to immutable eligible 5m/15m live-prediction signals. Simulated orders model latency, executable selected-side ask, limit price, recorded depth, partial fills, fees, slippage, expiry/cancellation, cash accounting, terminal events, and official-outcome-only settlement. Causal fills use immutable Polymarket book evidence; midpoint, synthetic, future, or fabricated fills remain forbidden.

Production debugging kept crossed/locked reconstructed books fail-closed per order, added replay-specific PostgreSQL indexes, and bounded deployment paper passes to 120 seconds. A previously exposed production database credential was rotated, and Phase 12 stopped passing `DATABASE_URL` through process argv in favor of `BP_ENV_FILE` indirection.

Isolated host acceptance returned `PHASE12_HOST_ACCEPTANCE=PASS` with a genuine prospective `TRADE`, causal paper-fill evidence, reconciliation `OK`, an idempotent rerun, nonnegative cash, and recorder/PostgreSQL/dashboard continuity. Permanent installation returned `PHASE12_INSTALL=PASS` on the same SHA with the money-disabled paper worker active.

The installed dashboard reported `paper_execution_available=true`, `execution_available=false`, current cash `92.207577336709`, realized P&L `0.0`, 3 paper orders, 2 paper fills, 0 settlements, and reconciliation `OK` with every recorded violation counter at zero. These are simulated execution results, not a live-profitability claim.

Sanitized closeout evidence is stored in `docs/evidence/phase-12-closeout-20260828.json`. Phase 13 — Improvement Loop — is now the next permitted phase. Live trading remains disabled, real-money limits remain zero, and Phase 14 live-readiness plus explicit authorization remain mandatory before real order placement.

## 0.11.0 — 28 August 2026

Phase 11 — Dashboard V1 — closed after exact-head CI, isolated production-host acceptance, and permanent host installation on operational candidate `126959eaef973b061c3c7ea619b6d6313f3f4e4e`. CI run #1223 passed 511 Python tests plus lint, deployment validation, health, dashboard tests, strict TypeScript typecheck, and the Next.js production build.

Dashboard V1 adds a read-only Python snapshot API and a localhost-only Next.js operator surface for active markets, model probabilities, observed market prices, edge/action, four-feed health, immutable prediction history, evaluation-backed performance/calibration, and current safety mode. The API rejects mutation requests, exposes no wallet/signing/order path, and keeps paper P&L explicitly `UNAVAILABLE_UNTIL_PHASE_12` with `execution_available=false` and `paper_execution_available=false`.

Production acceptance uncovered and corrected three host-only deployment/read-model defects without weakening safety: the temporary Node npm probe needed the downloaded Node directory in `PATH`; feed health had to derive from the recorder's authoritative compact `market_state_1s` evidence when the unused `feed_status` table was empty; and the initial compact-state fallback had to use bounded latest-row lookups rather than a full-table aggregate scan. Regression tests cover those cases. The final isolated host acceptance returned `PHASE11_HOST_ACCEPTANCE=PASS` with 4 active markets, 4 feed rows, 2 performance rows, 26 prediction-history rows, zero evaluated predictions, localhost-only candidate listeners, and the recorder active.

Permanent installation on the same SHA returned `PHASE11_INSTALL=PASS`. Node `v24.20.0` was checksum-verified; `bp-recorder`, PostgreSQL, dashboard API, and dashboard web were active after installation; listeners remained only `127.0.0.1:8787` and `127.0.0.1:3000`; API health reported `RESEARCH` with live trading disabled; and POST mutation requests returned HTTP 405. Zero evaluated predictions remains valid append-only evidence and is not converted into a performance or profitability claim.

Sanitized closeout evidence is stored in `docs/evidence/phase-11-closeout-20260828.json`. Phase 12 — Paper Execution — is now the next permitted build-order phase. It must simulate bid/ask, depth, partial fills, latency, slippage, cancellations, expiry, and fees through the same interface intended for later live trading, reconcile paper trades to immutable signals, and surface paper execution/P&L diagnostics through the dashboard. Live trading remains disabled and still requires the later live-readiness gate plus explicit user authorization.

## 0.10.0 — 28 August 2026

Phase 10 — live prediction engine — closed after prospective production-host acceptance on exact operational candidate `39101a60cdf712650f57a833849015c49da24946`. Fresh exact-head pre-host gates passed on that commit: CI #1130, Historical Backfill Smoke #439, Live Recorder Smoke #544, and Recorder Short Soak #510. The production host returned both `VERDICT=PASS` and `PHASE10_HOST_ACCEPTANCE=PASS`.

The accepted service is research-only and money-disabled. It loads exactly the frozen accepted Phase 9 policies for the verified 5m and 15m horizons, observes only timing-safe live inputs, records immutable `live-prediction-v1` predictions before outcome, and keeps official-outcome evaluation append-only. A stored `trade=true` remains a research decision only; Phase 10 contains no order, wallet, signing, allowance, paper-fill, or position path.

Production acceptance observed 26 stored predictions in total and required fresh prospective evidence during the candidate window: one newly recorded 5m prediction and one newly recorded 15m prediction, with two future markets and two opportunities available for each horizon. Maximum recorded lateness was 5,563 ms against the frozen 10-second deadline. The read-only report recorded 2,315 scheduled eligible markets and 2,289 historical late-or-missed coverage rows; those historical misses are explicit evidence and were never repaired by late backfill.

All acceptance integrity and safety counters were zero: pre-outcome violations, source-cutoff violations, semantic-hash violations, duplicate natural keys, prediction mutations, evaluation mutations, and order side effects. No official-outcome evaluation was yet available in the bounded acceptance window, so `EVALUATION_COUNT=0` and `EVALUATION_STATUS=pending` were accepted as the correct non-leaky state rather than manufacturing an outcome.

The final semantic-integrity correction preserves exact cryptographic verification for legacy PostgreSQL rows without mutating them. New numeric writes use lossless Decimal-bound inserts. For legacy rows that fail the fast exact/legacy alias path, the verifier reconstructs complete live inputs from frozen Phase 9 policy provenance plus recorder state; when a compact one-second snapshot has been overwritten by a later same-bucket event, it replays immutable raw market events only up to the stored cutoff. Candidate inputs must reproduce the exact stored `input_fingerprint`, then the complete prediction is rebuilt through the production writer and accepted only when its exact SHA-256 equals the stored semantic hash. No tolerance was added, no hash was weakened, and the bounded hash-search cap was not increased.

Host safety remained intact throughout acceptance: the recorder was active before and after, disk health was `ok` before and after with 88,395,304,960 and 87,412,625,408 bytes free respectively, the predictor service ran as the unprivileged `bp` user, and `ORDER_SIDE_EFFECT_VIOLATIONS=0`. Live trading remains disabled and Phase 10 makes no profitability claim.

Sanitized closeout evidence is stored in `docs/evidence/phase-10-closeout-20260828.json`. Phase 11 — Dashboard V1 — is now the next permitted build-order phase. It should expose active markets, model probabilities, market prices, edge/action, feed health, prediction history, evaluation-backed accuracy/calibration, and current mode without requiring direct database access. Paper execution, live readiness, and real-money trading remain later phases; live trading remains disabled and still requires explicit user authorization.

## 0.9.0 — 26 August 2026

Phase 9 — probability calibration + edge engine — closed after production-host acceptance on exact candidate `023832db5a55c6fcb686d81bd5ab6a6185273481`. Fresh exact-head pre-host gates passed on that commit: CI #861, Historical Backfill Smoke #328, Live Recorder Smoke #432, and Recorder Short Soak #396. Production acceptance used the fixed half-open window `2026-08-24T00:00:00Z <= t < 2026-08-25T00:00:00Z` and returned both `VERDICT=PASS` and `PHASE9_HOST_ACCEPTANCE=PASS`.

Phase 9 implements deterministic identity-versus-monotone-Platt calibration, fitted only on each frozen training partition and selected only on validation. It reuses the immutable Phase 8 fold memberships and prediction offsets, preserving ordinary OOS and final-holdout timing without re-selection. The edge engine uses the observed selected-side best ask, rejects missing/stale books, applies explicit research assumptions `fee_rate=0.07` and `slippage_buffer=0.01`, and chooses a validation-only minimum-edge threshold or the first-class `no_trade` policy. Test and final-holdout outcomes cannot rewrite calibration or threshold selection.

The accepted 5m run is `phase9-300-c9f0e00eb7836af08008c66909f8f179` with semantic SHA-256 `c9f0e00eb7836af08008c66909f8f179f03089413426508469353c75bcbcae24`, sourced from Phase 8 run `phase8-300-efdf493067e9d56419afc4d88452bec6`. Frozen offsets remained 240 seconds in all six folds. Validation selected `no_trade` in five folds and a trade threshold in one. Ordinary OOS produced three trades and +0.148014 assumed-cost P&L, but the untouched final holdout also produced three trades and -0.418991 assumed-cost P&L. The positive ordinary-OOS result is therefore not promoted into a profitability claim and is not retuned against the final holdout.

The accepted 15m run is `phase9-900-15c234f25588b23cce73a12f87a2e2ea` with semantic SHA-256 `15c234f25588b23cce73a12f87a2e2ea9087490055f203f22f183594b4bcfacd`, sourced from Phase 8 run `phase8-900-64aaf2b1774ee7af37bd110b84b37ec1`. Frozen offsets remained 840, 840, 780, 780, 840, and 840 seconds. Validation selected `no_trade` in every ordinary fold and for the final holdout, producing zero trades and zero assumed-cost P&L. That abstention is accepted evidence, not a failure to be overridden merely to manufacture activity.

Immediate rerun semantics matched exactly. The immutable Phase 9 registry moved from zero rows to two on the first pass and remained at two on the second, for `REGISTRY_SECOND_RUN_DELTA=0`. Production validation found zero source-offset mismatch violations, zero ordinary-OOS/final-holdout overlap violations, zero executable-contract violations, zero cost-assumption violations, and zero selection-boundary violations. Disk status was `ok` before and after acceptance with 118,854,045,696 and 118,456,893,440 bytes free respectively; the recorder remained active before and after; `LIVE_TRADING_ENABLED=false`; and maximum trade-size and daily-loss limits remained zero.

Sanitized closeout evidence is stored in `docs/evidence/phase-9-closeout-20260826.json`. Phase 10 — live prediction engine — is now the next permitted build-order phase. It must run on live feeds with money disabled, persist immutable predictions before outcomes with version/timestamp/market/probability/side/bid-ask/edge/decision provenance, and append outcome/evaluation only after resolution. Dashboard, paper execution, live readiness, and real-money trading remain later phases; live trading remains disabled and still requires explicit user authorization.

## 0.8.0 — 25 August 2026

Phase 8 — walk-forward backtester — closed after production-host acceptance of deterministic `walk-forward-v1` evaluation for the accepted Phase 7 `market_price` source runs on the verified 5m and 15m BTC Polymarket horizons. The accepted operational candidate was `69d3f9f8967dfcd1c1a68c640c242bd2b77cc089`. Fresh exact-head pre-host gates passed on that commit: CI #774, Historical Backfill Smoke #286, Live Recorder Smoke #390, and Recorder Short Soak #354. Production acceptance used the fixed half-open window `2026-08-24T00:00:00Z <= t < 2026-08-25T00:00:00Z` and returned `VERDICT=PASS`.

The backtester uses whole-market chronological duration-based rolling train/validation/test folds, one-market embargo protection, validation-only prediction-offset selection, non-reused ordinary test markets, and a separate final holdout outside all ordinary folds. Phase 8 acceptance found zero partition-overlap violations, zero ordinary-test reuse violations, zero single-class evaluated partitions, zero prediction-coverage violations, zero non-finite metric violations, and zero execution-semantic violations. Immediate rerun semantics matched exactly and the immutable backtest registry remained unchanged on the second pass.

The accepted 5m run is `phase8-300-efdf493067e9d56419afc4d88452bec6` with dataset SHA-256 `d5d2843ea2882aebe1cd3612e4345062067430d060824209b955a30590d8a6c2`, config SHA-256 `0ad67e69632d3b52c96b4970f0a5de640f0660d28eae86324c5b65454652c75a`, plan SHA-256 `2be73910d90903582ae884d801125ed29e36206b97671edd7e4c1d64efaf6d04`, and semantic SHA-256 `efdf493067e9d56419afc4d88452bec6effb871482664d19f109b3bbe4dd1d93`. Across six ordinary folds it evaluated 144 OOS markets at 0.8264 accuracy, 0.3344 log loss, and 0.1063 Brier score. Observed selected-side best-ask execution coverage was 0.4306 and gross P&L before costs was -1.465. The untouched final holdout was 0.8333 accurate, with 0.8333 execution coverage and gross P&L -0.26. Validation selected 240 seconds in every ordinary fold.

The accepted 15m run is `phase8-900-64aaf2b1774ee7af37bd110b84b37ec1` with dataset SHA-256 `21b71a29a01c97f63af306fdc48c3b88c5cfbd203bfd70999acaff1053a6ed6f`, the same config SHA-256, plan SHA-256 `53bdd6643d6388f19f7dc5a771a4040d50a98248fa3e62d08fd9fb5a763e8328`, and semantic SHA-256 `64aaf2b1774ee7af37bd110b84b37ec19f85bdc875a283986d4dba16ae921828`. Across six ordinary folds it evaluated 48 OOS markets at 0.9792 accuracy, 0.1068 log loss, and 0.0292 Brier score. Observed-ask execution coverage was only 0.2083 and gross P&L before costs was +0.381. The untouched final holdout fell to 0.625 accuracy with 0.625 execution coverage and gross P&L -0.47. Validation-selected ordinary-fold offsets were 840, 840, 780, 780, 840, and 840 seconds. The high ordinary-OOS 15m headline is therefore not treated as a stable trading result and individual timing slices are not cherry-picked.

Execution diagnostics are intentionally conservative. A hypothetical fill exists only when the frozen prediction side has an observed, fresh selected-side best ask; missing or stale book state is unavailable/no-fill. Midpoint fills, price-history substitutes, and synthetic fills are forbidden. Reported P&L is gross before fees, slippage, latency, and other costs, so Phase 8 makes no net-profitability claim. The accepted results explicitly show that predictive accuracy does not automatically imply executable edge.

Two host-gate defects were fixed test-first without weakening research semantics. The first acceptance run reached valid reports but a brittle source-code probe incorrectly demanded literal `pm_up_best_ask` and `pm_down_best_ask` tokens even though execution correctly constructed the selected-side key dynamically; the probe was changed to validate the actual dynamic implementation. The postflight full storage aggregate report was also removed from the critical acceptance path in favor of the bounded `disk-health` check already used preflight, retaining fail-closed disk safety while avoiding the known long-running storage scan.

Final host safety remained intact: disk status was `ok` before and after acceptance with 126,673,256,448 and 126,290,612,224 bytes free respectively; the recorder remained active before and after; `LIVE_TRADING_ENABLED=false`; and maximum trade-size and daily-loss limits remained zero.

Sanitized closeout evidence is stored in `docs/evidence/phase-8-closeout-20260825.json`. Phase 9 — probability calibration + edge engine — is now the next permitted build-order phase. It must calibrate probabilities only on permitted training/validation data, compare them with observed executable prices, account for spread/fees/slippage/uncertainty/staleness, and abstain when the configured minimum edge is not met. Live prediction, paper trading, and live trading remain blocked by later phase gates; live trading remains disabled.

## 0.7.0 — 25 August 2026

Phase 7 — baselines before fancy ML — closed after production-host acceptance of the deterministic `supervised-core-v1` modeling pipeline for the verified 5m and 15m BTC Polymarket horizons. The accepted operational candidate was `66bae5c71eab5e2c154cff1144ce509101d6e985`. Fresh exact-head pre-host gates passed on that commit: CI #635, Historical Backfill Smoke #218, Live Recorder Smoke #322, and Recorder Short Soak #286. Production acceptance used the fixed half-open market-start window `2026-08-24T00:00:00Z <= t < 2026-08-25T00:00:00Z` and returned `VERDICT=PASS`.

The supervised dataset joins immutable `core-v1` features to `official-outcome-v1` labels only after feature generation. `chronological-market-v1` keeps every feature row for a `condition_id` inside one chronological train, validation, test, or embargo partition, with no random feature-row shuffle. Training-only median imputation/scaling, equal-market weighting, deterministic dataset/split hashes, calibration/coverage metrics, immutable model-training registry rows, and external joblib artifact SHA-256 manifests are all part of the accepted contract.

Production acceptance expanded coverage to 288 labeled 5m markets and 96 labeled 15m markets, producing 1,152 5m and 1,344 15m `core-v1` feature rows. The full-day expansion preserved all 104 previously accepted Phase 6 feature rows without recomputing or rewriting them; later recovered source history is used only to create previously missing natural keys. Strict/default generation continues to fail closed on semantic feature conflicts.

The accepted 5m run is `phase7-300-0a822e17ceced11742bf6d3bc8214f44` with dataset SHA-256 `d5d2843ea2882aebe1cd3612e4345062067430d060824209b955a30590d8a6c2`, split SHA-256 `50a05c4240decee89de7f1b1341d61068a972636ff9b530477159b2c9653d9e0`, and semantic SHA-256 `0a822e17ceced11742bf6d3bc8214f44f4755c7bc23bb1d3f2dcfa897f3edcc0`. The accepted 15m run is `phase7-900-e36d978aecc29816c5b9e2b67b30d6e2` with dataset SHA-256 `21b71a29a01c97f63af306fdc48c3b88c5cfbd203bfd70999acaff1053a6ed6f`, split SHA-256 `2d70e523dabfbd328b8b93e343459512244c4c4de2864881f7b75d252a7e695`, and semantic SHA-256 `e36d978aecc29816c5b9e2b67b30d6e218a0af6e08e6b7f31c10161ec1fc2a0b`.

For both verified horizons the validation champion is the simple Polymarket `market_price` baseline. XGBoost recorded `boosted_promotion_eligible=false` for both horizons, so Phase 7 does not justify escalating model complexity. The final test cannot rewrite the validation champion. The accepted report includes per-offset metrics for later timing analysis; for example, the 15m market-price baseline recorded 0.80 accuracy at 780 seconds and 0.85 accuracy at 840 seconds on 20-market offset slices, but Phase 7 does not select an optimal prediction time from those final-test slices.

The immediate training rerun matched semantically, the model registry remained at two rows on the second run, cross-partition condition violations were zero, single-class partition violations were zero, and artifact hash violations were zero. Bybit historical REST remained the already-audited production-host HTTP 403 limitation; no route-around or synthesized history was used. Disk status was `ok` before and after acceptance with 128,369,307,648 and 127,244,443,648 bytes free respectively; the recorder remained active; `LIVE_TRADING_ENABLED=false`; and trade-size/daily-loss limits remained zero.

Several failed acceptance attempts are preserved as regression evidence. The candidate runner was changed from a shared root-created worktree to a verified root-owned Git worktree plus exact `git archive` export into a `bp`-owned non-Git build tree, eliminating package-build and Git dubious-ownership coupling without global `safe.directory` exceptions. The full-day expansion then exposed immutable-feature conflicts after historical source enrichment; an explicit preserve-existing mode was added test-first so frozen Phase 6 rows remain authoritative while missing full-day keys can be generated. The earlier expensive preflight full storage report was also replaced by a lightweight disk-health preflight while retaining the full post-run report.

Sanitized closeout evidence is stored in `docs/evidence/phase-7-closeout-20260825.json`. Phase 8 — walk-forward backtester — is now the next permitted build-order phase. It must preserve chronological evaluation, purging/embargo where required, frozen reproducible outputs, regime/timing breakdowns, and realistic executable prices. Live prediction, paper trading, and live trading remain blocked by later phases; live trading remains disabled.

## 0.6.0 — 25 August 2026

Phase 6 — feature engine — closed after production-host acceptance of deterministic, immutable, versioned `core-v1` feature snapshots built only from observations provably available at each feature timestamp.

The final host-accepted operational candidate was `71ab67f178f8dc30a1d933ff2e553a508bb08f02`. Fresh exact-head pre-host gates passed on that commit: CI #562, Historical Backfill Smoke #183, Live Recorder Smoke #287, and Recorder Short Soak #251. Production acceptance used the fixed half-open market-start window `2026-08-24T18:00:00Z <= t < 2026-08-24T19:00:00Z`, a 60-second feature cadence, and returned `VERDICT=PASS`.

The feature store is immutable at natural key `(condition_id, feature_at, feature_version)`. `core-v1` includes market-time geometry, Polymarket price state, BTC momentum/volatility from fully closed Coinbase candles, compact book/state observations when provably available and fresh, observed trailing trade flow when raw coverage is eligible, explicit missing/stale flags, and source cutoffs/fingerprints. Official outcome, official label references, resolution metadata, and label provenance are not feature inputs. `official_reference_distance` remains NULL with `official_reference_missing=true` because no independently verified first-party reference series was established for V1.

Production acceptance verified 16 target markets and 104 persisted feature rows. Both final feature-generation passes were existing-only (`inserted=0`, `existing=104`), proving immediate idempotence against the already-populated immutable rows. The gate found zero source cutoffs after feature time, zero duplicate natural keys, zero forbidden label/outcome keys, and zero official-reference contract violations. Disk status was `ok` with 133,556,445,184 bytes free, and the recorder remained active before and after acceptance. Live trading remained disabled with zero trade-size and daily-loss limits.

Two failed production acceptance attempts are intentionally preserved. Candidate `d38250c6f5fb68704ce306cfb051111b25c7c680` exposed a real leakage edge case: second-rounded compact-state buckets could contain `last_event_at` values a fraction of a second after `feature_at`. A RED regression test reproduced the failure, and `latest_state` was corrected to require both `bucket_at <= feature_at` and `last_event_at <= feature_at` in the source query while retaining post-selection fail-closed guards. Candidate `c76dfabb100129efd94501721c3d52820b13f4fa` then populated all 104 immutable rows but the later acceptance checker failed because it called `json_each_text` on a PostgreSQL `jsonb` column. A second RED regression test isolated that checker defect and the final candidate changed it to `jsonb_each_text` without rewriting feature data.

Missing-data semantics remain explicit rather than imputed. In the accepted window the final rerun reported 26 `coinbase_candles_missing`, 104 `official_reference_missing`, 76 missing and 72 stale rows for each Polymarket outcome book group, and 72 missing rows for both Polymarket trade flow and aggregate raw trade flow. These counts are research evidence about source coverage, not zeros or synthesized observations. Phase 3 raw-data exclusions and Phase 4 historical-source limitations remain binding downstream.

Sanitized closeout evidence is stored in `docs/evidence/phase-6-closeout-20260825.json`. Phase 7 — baseline modeling and model training — is now the next permitted build-order phase. Model work must join frozen feature rows to official labels only after feature generation, use leakage-safe time-ordered evaluation, and report calibration/coverage alongside accuracy. Backtesting, live prediction, paper trading, and live trading remain blocked by later phase gates; live trading remains disabled.

## 0.5.0 — 25 August 2026

Phase 5 — official outcome/label pipeline — closed after production-host acceptance of deterministic, immutable, leakage-safe labels derived only from preserved official Polymarket Gamma resolution evidence.

The host-accepted operational candidate was `3c39b626a37c7ac7c0a9c10caaabd4d0b6cf0325`. Fresh exact-head pre-host gates passed on that commit: CI #483, Historical Backfill Smoke #145, Live Recorder Smoke #249, and Recorder Short Soak #213. Production acceptance used the fixed half-open market-start window `2026-08-24T18:00:00Z <= t < 2026-08-24T19:00:00Z` and returned `VERDICT=PASS`.

The label pipeline stores immutable `official-outcome-v1` rows keyed by `(condition_id, label_version)`. Labels are generated offline from the Phase 4 `polymarket_market_snapshots` store, not from a live HTTP dependency. A snapshot may become label evidence only when the market is closed, the official outcome is unambiguous, and the snapshot was observed at or after market end. For each condition the canonical source is the earliest eligible resolved snapshot ordered by download time and snapshot id. Conflicting official-resolution semantics or attempts to change an existing semantic label fail closed rather than rewriting history.

Production acceptance generated 16 labels on the first pass and then immediately reran the exact same window with zero inserts and 16 existing rows. The gate verified zero leakage violations, zero contract violations, zero missing exact snapshot-provenance joins, zero duplicate natural keys, and matching condition coverage across the two runs. The recorder remained active before and after acceptance.

Official start/end reference prices remain NULL in V1. The preserved Gamma payloads and current Phase 5 evidence do not establish independently verified first-party start/end reference-price fields, so Coinbase, Bybit, CLOB token prices, trades, or inferred prices are not substituted into the authoritative label contract. Downstream feature work may use market/BTC observations as features only under its own timestamp and provenance rules; those observations do not become official label references.

Phase 3 raw-data exclusions and Phase 4 source-availability limitations remain binding downstream. In particular, unavailable/unverified historical Polymarket L2 remains unavailable rather than synthesized, and audited source gaps must remain explicit through feature missing-data flags.

Sanitized closeout evidence is stored in `docs/evidence/phase-5-closeout-20260825.json`. Phase 6 — feature engine — is now the next permitted build-order phase. Model training, backtesting, live prediction, paper trading, and live trading remain blocked by later phase gates; live trading remains disabled.

## 0.4.0 — 25 August 2026

Phase 4 — historical backfill — closed after production-host acceptance of deterministic Polymarket market discovery, official token-price history, Coinbase BTC-USD candles, immutable/idempotent historical storage, provenance/checksums, and environment-aware Bybit handling.

The host-accepted operational candidate was `29fa75b500858ae50f50b863d0c62ff2acb4ec52`. Fresh exact-head gates passed before closeout: CI #421, Historical Backfill Smoke #112, Live Recorder Smoke #220, and Recorder Short Soak #184. Production acceptance used the fixed half-open window `2026-08-24T18:00:00Z <= t < 2026-08-24T19:00:00Z` and returned `VERDICT=PASS`.

Polymarket historical market discovery now deterministically enumerates aligned `btc-updown-<horizon>-<window_start_epoch>` slugs for the verified 5m/15m horizons and fetches each exact Gamma market-by-slug payload. This replaces list/date-filter discovery after production acceptance repeatedly exposed HTTP 500 behavior on Gamma keyset queries and live verification showed the regular dated market list did not reliably include a known recent BTC market. A missing exact slug is an explicit coverage gap; a returned slug/window mismatch fails closed; exact-slug HTTP 500/503 responses receive bounded retries.

Historical Up/Down token prices use the official Polymarket CLOB `/prices-history` endpoint and persist only observations inside the market's half-open window while retaining provenance for the complete fetched response. Coinbase public BTC-USD candles are the mandatory core BTC historical series for Phase 4.

Bybit spot and linear BTCUSDT historical kline support is implemented, but the production GCP host and GitHub US-hosted runners receive documented HTTP 403 restrictions from Bybit. Standard backfill therefore records only that narrowly classified condition as audited `unavailable` with zero rows/chunks; explicit Bybit-only commands and `standard --require-bybit` remain strict. The project does not route around provider restrictions.

The production host gate verified 10 new dataset-run records across two standard runs, zero invalid terminal statuses, four audited Bybit-unavailable runs, zero rows inserted by the second run, non-empty/existing core-source coverage, recorder active before and after acceptance, maintenance and disk-health timers enabled, disk status `ok`, and the preserved Phase 3 forensic SHA-256 unchanged. The boot disk was safely expanded to 200 GiB after PostgreSQL growth crossed the Phase 3 warning threshold; no protected research data was deleted.

No verified first-party historical Polymarket L2/order-book endpoint was found for this phase. Historical depth is therefore marked unavailable/unverified and is never synthesized. Phase 3 raw-data exclusions remain binding for downstream raw-dependent research.

Sanitized closeout evidence is stored in `docs/evidence/phase-4-closeout-20260825.json`. Phase 5 — official outcome/label pipeline — is now the next permitted build-order phase. Feature engineering, model training, backtesting, paper trading, and live trading remain blocked by their later phase gates; live trading remains disabled.

## 0.3.0 — 24 August 2026

Phase 3 — retention and aggregation — closed after host validation of bounded raw retention, verified archive-before-delete behavior, compact one-second state, disk protection, and scheduled maintenance.

Final host validation ran on commit `d90919a29f7beca7c7b1b5ba4cfd2964c11747c4`. Fresh branch gates passed on that commit: CI #206, Live Recorder Smoke #114, and Recorder Short Soak #78. The host proof returned `FAIL_COUNT=0` and `PHASE3_RETENTION_SEMANTICS_HOST_REVALIDATION_PASS`, with zero premature archive removals, archive/delete row parity, all managed archives verifying, zero stale archive temp files, four compact feeds present, recorder active with zero warning lines during the proof, disk status `ok`, successful systemd maintenance, and both maintenance/disk-health timers enabled.

A late closeout review found that the accepted archive-retention contract is 24 hours of hot PostgreSQL raw data plus 24 additional hours of verified local archive retention, for roughly 48 hours of full-raw recoverability. The earlier operator wiring counted archive retention from event time and was corrected in `6d329b2707b7897cbd73baa2aae5a87990fe7975`; a regression test first failed on the old wiring and then passed after the fix.

The interrupted-maintenance incident remains explicitly preserved: exactly 250,000 events from `2026-08-22T20:00:00Z` through `2026-08-22T21:00:00Z` are missing from both PostgreSQL and the surviving archive. The preserved forensic archive SHA-256 is `423f22c58ed356a207684b794f401537ba60e009f08aa89fe54fc7f58efbe9ef`. That exact interval is excluded from raw-dependent model training unless independently recovered from a trustworthy source.

A separate rollout-era local coverage limitation is also recorded. After final revalidation, surviving managed archives covered `2026-08-23T18:00:00Z` through `2026-08-23T21:00:00Z`; compact state was first observed for all four feeds in the `2026-08-24T10:00:00Z` hour and the earliest retained raw row was `2026-08-24T10:18:16.692122Z`. Surviving VM artifacts cannot prove whether unavailable earlier local raw coverage was caused by pruning history or capture downtime, so that unavailable interval is excluded from raw-dependent research unless independently reacquired. Compact-state absence before the compact-state rollout is not, by itself, treated as proof of recorder downtime.

Storage evidence measured approximately 39.5 GB/day of projected raw PostgreSQL growth at the observed event rate. Near-steady-state maintenance completed in 1,375 seconds against a 55-minute service timeout, and the first scheduled cycle also completed successfully. PostgreSQL retention scans use the `(received_at, id)` index path and autovacuum/reuse was observed; `VACUUM FULL` was not used.

Sanitized closeout evidence is stored in `docs/evidence/phase-3-closeout-20260824.json`. Phase 4 — historical backfill — is now the next permitted build-order phase. Live trading remains disabled.

## 0.2.3 — 23 August 2026

Phase 2 closed after the genuine 24-hour always-on recorder gate and continuity review.

The accepted frozen window is `2026-08-22T20:12:57.033984Z` through `2026-08-23T20:12:57.033984Z`. Revalidation of that exact window passed with 45,669,676 persisted events across Polymarket, Bybit spot, Bybit linear/perpetual, and Coinbase spot.

Continuity review found:

- zero systemd recorder restarts during the run;
- zero unresolved disconnects;
- zero `clock_skew` incidents;
- zero internal `backpressure` incidents;
- all 35 recorded disconnects followed by prompt reconnects;
- bounded and explicitly logged Bybit stale gaps of 30.734 s (spot) and 51.229 s (linear), followed by resumed ingestion.

The original formal report remains preserved as failed evidence because a stale-recovery incident persistence bug incorrectly left the two Bybit stale states unresolved in the incident table. The defect was reproduced test-first, fixed, and verified. Final reliability/evaluator commit `8c4c35b654b46a8bd8235daa2a03d43496693c2a` passed CI run 79 with Ruff clean and 77 tests, Live Recorder Smoke run 48, and Recorder Short Soak run 16.

The earlier host attempt that filled the original 40 GB disk is also retained as failed evidence and is not counted toward acceptance. That storage failure directly motivates Phase 3 retention/aggregation work.

Sanitized closeout evidence is stored in `docs/evidence/phase-2-host-soak-20260823.json`; the narrative closeout is `docs/PHASE-2-CLOSEOUT.md`.

Phase 3 — retention and aggregation — is now the next permitted build-order phase. Live trading remains disabled.

## 0.2.2 — 21 August 2026

Phase 2 reached the pre-host recorder checkpoint. The phase remains open pending the required genuine 24-hour always-on soak test.

Verified on commit `cf85c9139cfd887188bb10b60d6a75cf98e0e389`:

- CI passed with 74 automated tests and Ruff clean;
- live recorder smoke passed against Polymarket, Bybit spot, Bybit perpetual, and Coinbase spot;
- PostgreSQL-backed short soak passed with 17,506 live events and no health failures;
- Polymarket book snapshot timestamps are preserved in raw payloads without being misclassified as transport clock-skew evidence;
- Coinbase secondary feed uses ticker/top-of-book plus market trades and heartbeats, while Bybit remains the primary deep-book source;
- SQLAlchemy PostgreSQL connections use the installed psycopg v3 driver;
- secure always-on Ubuntu deployment assets were added;
- production PostgreSQL is bound to localhost only;
- the recorder runs as a dedicated unprivileged systemd user without Docker-socket access;
- host NTP synchronization remains required;
- CI validates deployment shell syntax and production Docker Compose configuration;
- a formal 24-hour soak report command and protected evidence location are documented.

No model training, paper trading, or live trading has been added. `LIVE_TRADING_ENABLED` remains false and Phase 3 must not begin until the actual 24-hour Phase 2 host gate is passed and documented.

## 0.2.1 — 20 August 2026

Phase 1 market discovery closed and Phase 2 opened.

Verified:

- GitHub Actions successfully queried live Polymarket Gamma;
- authentic 5m and 15m market payloads were captured in-repo;
- live payloads confirmed real condition/event/CLOB token IDs and current Chainlink BTC/USD 60-second TWAP rule metadata;
- focused unit fixtures were replaced with authentic captured values;
- 15 local tests pass against the authentic fixture shapes;
- compile and safe `RESEARCH` health checks pass.

No model, paper trading, or live trading has been added. Phase 2 is the 24/7 raw recorder.

## 0.2.0 — 20 August 2026

Phase 1 market-discovery implementation prepared.

Added:

- official Gamma `GET /markets/slug/{slug}` client;
- deterministic UTC-aligned BTC 5m/15m recurring slug discovery;
- strict Gamma market parser and Up/Down token-label mapping;
- per-market resolution-source/rules fingerprinting;
- normalized `polymarket_markets` schema, PostgreSQL migration, and repository;
- rule-change guard for an existing condition;
- live Gamma smoke workflow/artifact capture for network-enabled GitHub Actions;
- Phase 1 parser, client, discovery, storage, and service tests.

Corrected source-of-truth resolution details after verifying that current short BTC markets use the Chainlink BTC/USD 60-second TWAP stream while older examples used a different Chainlink rule version.

Phase 1 remains open until the live Gamma smoke succeeds and authentic live response fixtures are inspected. No model, paper trading, or live trading has been added.

## 0.1.1 — 20 August 2026

Phase 0 repository bootstrap completed locally and prepared for repository publication.

Added:

- Python 3.12+ project configuration;
- safe runtime defaults for Research/Paper/Live modes;
- active 5m/15m and optional 10m horizon configuration;
- machine-readable health command;
- JSON logging foundation;
- PostgreSQL 16 Docker Compose development service;
- `.env.example` and secret-safe `.gitignore`;
- pytest + Ruff development tooling;
- GitHub Actions CI;
- source-of-truth and project handoff documentation;
- Phase 0 implementation plan.

No market collector, prediction model, paper execution, or live trading code exists yet.

## 0.1.0 — 20 August 2026

Initial project/source-of-truth freeze.