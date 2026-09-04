# Decision Log

Decisions are append-only. Superseded decisions remain for history and point to the replacement.

## D-001 — Project objective
**Date:** 20 Aug 2026  
**Status:** Active

Build a system that ultimately trades short-duration Polymarket BTC Up/Down markets using a model-derived probability and market-price/edge comparison.

## D-002 — Accuracy target is aspirational
**Date:** 20 Aug 2026  
**Status:** Active

Approximately 80% accuracy is the desired research target, but must never be represented as guaranteed or proven until strict out-of-sample/live evidence supports it.

## D-003 — Market horizons are configurable
**Date:** 20 Aug 2026  
**Status:** Active

Verified initial Polymarket BTC Up/Down horizons are 5m and 15m. 10m is desired but not currently verified.

## D-004 — Resolution-target alignment
**Date:** 20 Aug 2026  
**Status:** Superseded by D-009

Train and evaluate against the official Polymarket outcome. Early checked examples used Chainlink BTC/USD and end-price-versus-start-price wording; current rules are versioned per D-009.

## D-005 — $0-first infrastructure
**Date:** 20 Aug 2026  
**Status:** Active

Validate the idea with free infrastructure where practical while keeping the architecture portable.

## D-006 — Data recorder before prediction model
**Date:** 20 Aug 2026  
**Status:** Active

Build the continuously running BTC + Polymarket recorder before serious model work.

## D-007 — Controlled retraining
**Date:** 20 Aug 2026  
**Status:** Active

The engine does not blindly learn after every trade. Live history enters a versioned champion/challenger retraining process.

## D-008 — No live money until gated
**Date:** 20 Aug 2026  
**Status:** Active

Progression is Research → Paper → Live. Live trading requires documented validation, security/risk readiness, geographic eligibility checks, and explicit user authorization.

## D-009 — Resolution rules are versioned market data
**Date:** 20 Aug 2026  
**Status:** Active

Current checked BTC 5m/15m Rules use the Chainlink BTC/USD 60-second TWAP stream and TWAP-over-range wording, while older short markets used the regular BTC/USD stream and end-price-versus-start-price wording. The engine must preserve exact rules text/source and a rules fingerprint for every market. Official Polymarket resolution remains the authoritative label.

## D-010 — Phase 2 primary BTC venue starts with Bybit
**Date:** 20 Aug 2026  
**Status:** Active

Phase 2 begins with Bybit public BTCUSDT spot and linear-perpetual WebSocket feeds as the primary BTC venue because the official V5 API exposes real-time public trades, ordered snapshot/delta books, matching-engine timestamps, and separate spot/linear streams without private credentials. A secondary venue is added after the primary path is stable; Coinbase Advanced Trade is the initial secondary candidate because its public `level2`, `market_trades`, and heartbeat channels are available without authentication.

## D-011 — Phase 3 archive retention is additional to hot raw retention
**Date:** 24 Aug 2026  
**Status:** Active

`STORAGE_HOT_RAW_HOURS=24` and `STORAGE_ARCHIVE_RETENTION_HOURS=24` mean approximately 24 hours of hot raw PostgreSQL data followed by 24 additional hours of verified local archive retention, for roughly 48 hours of full-raw recoverability. Archive pruning therefore uses the sum of the hot and archive retention windows when evaluating event-time archive intervals.

An archive may be pruned only after the archive and manifest verify, compact state has advanced beyond the interval, and the exact interval contains no remaining raw rows. The raw-empty guard prevents an interrupted partial deletion from losing the only complete copy of an interval. Maintenance remains fail-closed at critical disk status and never substitutes manual deletion of unarchived raw data.

## D-012 — Raw coverage exclusions are first-class dataset metadata
**Date:** 24 Aug 2026  
**Status:** Active

The exact interval `2026-08-22T20:00:00Z` through `2026-08-22T21:00:00Z` is excluded from raw-dependent model training because 250,000 events are known to be missing from both PostgreSQL and the surviving forensic archive. It may be admitted only if independently recovered from a trustworthy source and revalidated.

A separate Phase 3 rollout-era local coverage limitation is also excluded from raw-dependent research unless independently reacquired. Surviving VM artifacts do not prove whether unavailable local raw coverage after `2026-08-23T21:00:00Z` and before the compact-state rollout was earlier pruning history or capture downtime. Compact state was first observed with all four feeds in the `2026-08-24T10:00:00Z` hour, so absence of compact rows before that rollout is not, by itself, evidence of recorder downtime. Future dataset builders must carry these exclusions explicitly rather than silently treating missing local raw data as complete history.

## D-013 — Historical observations are immutable and reruns fail closed on conflicts
**Date:** 25 Aug 2026  
**Status:** Active

Phase 4 historical storage uses immutable natural keys. Re-fetching an identical historical observation is a no-op/existing-row result. If the same natural key later carries a different value, the backfill raises `HistoricalDataConflict` instead of silently rewriting history.

Every external historical chunk records source/dataset identity, exact request parameters, download timestamp, row count, and canonical SHA-256 provenance. This contract is required for reproducibility and must be preserved by downstream dataset builders.

## D-014 — Bybit historical REST is optional only when a documented host restriction is explicitly audited
**Date:** 25 Aug 2026  
**Status:** Active

Bybit V5 spot and linear BTCUSDT historical candle support remains implemented, but Bybit documents HTTP 403 restrictions for US IP addresses. Both GitHub US-hosted runners and the production GCP `us-east1` recorder host produced that condition during Phase 4 validation.

In standard historical backfill, only the narrowly classified Bybit HTTP 403 restriction may terminate as `unavailable`, with zero rows/chunks and a durable reason. `standard --require-bybit` and explicit Bybit-only commands remain strict. The project will not bypass, tunnel around, or otherwise evade provider geographic/service restrictions. Coinbase BTC-USD public candles are the mandatory verified core BTC historical series for Phase 4.

## D-015 — Phase 4 Polymarket historical market discovery uses deterministic exact BTC slugs
**Date:** 25 Aug 2026  
**Status:** Active

Phase 4 does not depend on Gamma keyset/date-filter market listing for BTC 5m/15m historical coverage. Production acceptance repeatedly received HTTP 500 from the bounded keyset query even with retry, while a separate regular dated market-list live check did not reliably include a recent completed BTC market that exact slug lookup had already returned.

Historical discovery therefore enumerates aligned `btc-updown-<horizon>-<window_start_epoch>` slugs for the verified 5m and 15m horizons and fetches each exact Gamma market-by-slug payload. A 404 is an explicit coverage gap; any returned slug/window mismatch fails closed; HTTP 500/503 exact-slug responses receive only bounded retries. This discovery contract is reproducible and directly matches the recurring market naming contract already validated in Phase 1.

## D-016 — Unavailable historical order-book depth must remain unavailable, never synthesized
**Date:** 25 Aug 2026  
**Status:** Active

Phase 4 did not verify a current first-party Polymarket endpoint that provides historical L2/order-book depth for the required past BTC markets. Historical L2 is therefore represented as unavailable/unverified data, not reconstructed from token prices, trades, compact snapshots, or assumptions.

Downstream feature work must distinguish genuinely observed live/retained book data from historical periods where depth was not captured. Missing historical order-book information must remain explicit rather than being fabricated to create apparent coverage.

## D-017 — Official labels are immutable, post-resolution derivatives of preserved Gamma snapshots
**Date:** 25 Aug 2026  
**Status:** Active

Phase 5 labels use the official resolved Polymarket outcome parsed from preserved Phase 4 Gamma snapshots. Label generation is offline and network-free. A snapshot is eligible only when the market is closed, the official outcome is unambiguous, and the snapshot was observed at or after the market end; any apparently resolved snapshot observed before market end is treated as leakage/data-integrity failure.

For each condition, the canonical source is the earliest eligible resolved snapshot ordered by `downloaded_at` and snapshot id. All eligible snapshots must agree on market identity, window, rules fingerprint/source, and resolved outcome. Contradictory official-resolution evidence raises a source conflict. Stored labels are versioned by `(condition_id, label_version)`; identical reruns are no-ops and semantic relabel attempts fail closed.

## D-018 — Unverified official start/end reference prices remain NULL
**Date:** 25 Aug 2026  
**Status:** Active

Phase 5 does not infer or substitute the official market-resolution start/end reference prices from Coinbase candles, Bybit candles, Polymarket token prices, trades, or other secondary observations. The preserved Gamma evidence used for V1 labels does not independently verify a first-party start/end reference-price field, so `start_reference` and `end_reference` remain NULL in `official-outcome-v1`.

Future work may populate those fields only when a trustworthy first-party resolution source is explicitly verified and provenance/versioning are defined. Market/BTC prices remain valid candidate features under Phase 6 feature-time rules, but they are not silently promoted into the authoritative label contract.

## D-019 — Feature versions freeze source-selection and missing-data semantics
**Date:** 25 Aug 2026  
**Status:** Active

Phase 6 stores immutable feature snapshots keyed by `(condition_id, feature_at, feature_version)`. `core-v1` fixes the source-selection rules, availability cutoffs, staleness threshold, trailing-window lengths, formulas, feature names, and missing-data semantics. Identical reruns are existing/no-op; changing any of those semantics requires a new feature version rather than rewriting a `core-v1` row.

Feature generation must not read official outcomes, official label references, resolution metadata, or label provenance. Historical L2 that was not observed remains unavailable rather than synthesized, raw-dependent groups remain missing when Phase 3 exclusions or unproven feed coverage overlap the required window, and the unverified official reference distance remains NULL with an explicit missing flag.

## D-020 — Compact state is usable only when both bucket time and last event time are in the feature past
**Date:** 25 Aug 2026  
**Status:** Active

A `market_state_1s` row is eligible at feature time `T` only if both `bucket_at <= T` and `last_event_at <= T`. The reader must select the latest row satisfying both conditions, not select a bucket by `bucket_at` and then abort merely because that bucket contains a later sub-second event. Post-selection leakage guards remain as defense in depth.

This rule was promoted to an explicit project decision after production acceptance candidate `d38250c6f5fb68704ce306cfb051111b25c7c680` exposed the same-second sub-second leakage edge case. The fix was regression-tested before implementation and final Phase 6 host acceptance verified zero persisted source cutoffs after feature time.

## D-021 — Supervised splits are chronological and indivisible by market
**Date:** 25 Aug 2026  
**Status:** Active

Phase 7 treats `condition_id` as the indivisible supervised-learning grouping key. Every feature timestamp from one market must remain wholly inside one train, validation, test, or embargo partition. Random row shuffles are forbidden because multiple feature timestamps share the same eventual market outcome and path; splitting those rows independently would leak correlated market information across evaluation boundaries.

The initial split contract is `chronological-market-v1`: unique markets are ordered by `(market_start_at, condition_id)`, assigned chronologically, and protected by one whole-market embargo at each train/validation and validation/test boundary when the sample permits it. Preprocessing is fitted on training data only, and production acceptance requires zero cross-partition condition overlap and both classes in every non-embargo partition.

## D-022 — Validation chooses the champion; the final test cannot rewrite it
**Date:** 25 Aug 2026  
**Status:** Active

Model selection is frozen from validation results before final-test metrics are used. Phase 7 compares the weighted prior, Polymarket market-price baseline, logistic regression, and deterministic XGBoost challenger; `validation_champion` is selected by validation log loss with documented tie-breaks. The final test is evidence about the already-frozen candidates and cannot change the champion, feature schema, threshold, or preprocessing.

XGBoost is promotion-eligible only if it beats the simple baselines under the documented validation log-loss/Brier rule and confirms the required test behavior without a worse Brier score. Phase 7 production acceptance selected `market_price` for both 5m and 15m and recorded `boosted_promotion_eligible=false` for both. This is a successful complexity stop: later work must not escalate model complexity merely because a more complex model exists.

## D-023 — Historical expansion preserves already-frozen feature snapshots
**Date:** 25 Aug 2026  
**Status:** Active

Phase 7 may expand source history over a broader research window after earlier `core-v1` rows have already been accepted. Such later source recovery must not retroactively rewrite those immutable snapshots or silently pretend the recovered observations were part of the original materialization context.

The explicit Phase 7 expansion mode therefore checks for an existing `(condition_id, feature_at, core-v1)` key before feature recomputation, validates that its static market metadata still matches, preserves the existing row untouched, and computes only missing natural keys from the expanded history. Normal/default feature generation remains strict and raises `FeatureConflict` on semantic drift. Production acceptance proved that all 104 previously accepted Phase 6 feature rows were preserved while the full-day feature set was expanded.

## D-024 — Walk-forward evaluation selects timing on validation only and never reuses ordinary test markets
**Date:** 25 Aug 2026  
**Status:** Active

Phase 8 uses deterministic duration-based chronological rolling folds over whole `condition_id` markets. Train, validation, and ordinary test partitions are disjoint, a whole-market embargo protects boundaries, and an ordinary test market cannot appear as test evidence in more than one fold. A final holdout is outside all ordinary folds.

Prediction-offset selection is performed only from each fold's validation candidates. Ordinary test results and the final holdout cannot rewrite the selected offset, source model, feature contract, or evaluation configuration. The Phase 8 production gate requires zero partition overlap, zero ordinary-test reuse, both classes in evaluated partitions, and deterministic semantic reruns.

## D-025 — Backtest fills require an observed selected-side best ask; unavailable books are no-fill
**Date:** 25 Aug 2026  
**Status:** Active

Phase 8 execution diagnostics use only the observed best ask for the side implied by the frozen probability decision: `pm_up_best_ask` for Up and `pm_down_best_ask` for Down, selected dynamically by side. Missing or stale selected-side book state is unavailable/no-fill. Midpoint fills, price-history substitutes, and synthetic fills are forbidden.

Reported execution P&L is explicitly gross before fees, slippage, latency, and other costs. It is diagnostic evidence, not a net-profitability claim. Phase 9 must preserve these availability semantics when calculating edge and must not create apparent trade coverage by inventing executable prices.

## D-026 — Accuracy alone cannot promote a trading rule; Phase 9 must optimize calibrated executable edge and abstention
**Date:** 25 Aug 2026  
**Status:** Active

Phase 8 production evidence demonstrates why the project's aspirational accuracy target is not itself a trading criterion. The 5m accepted walk-forward report reached 0.8264 ordinary OOS accuracy over 144 markets and 0.8333 on the final holdout, yet observed-ask gross P&L was negative in both aggregate ordinary OOS and final holdout. The 15m report reached 0.9792 ordinary OOS accuracy over only 48 markets, but the untouched final holdout fell to 0.625 accuracy and gross P&L was negative.

Therefore the high 15m ordinary-OOS headline and individual offset slices must not be cherry-picked into a trading rule. Phase 9 should calibrate probabilities using permitted training/validation data, compare them with observed executable selected-side prices, account for spread/fees/slippage/uncertainty/staleness, and abstain when a configured minimum net edge is not met. Any threshold or calibration choice must be frozen before untouched evaluation is consulted.

## D-027 — Phase 9 abstention is valid; ordinary-OOS edge cannot override a losing untouched holdout
**Date:** 26 Aug 2026  
**Status:** Active

Phase 9 production acceptance validates the calibration/edge/abstention machinery, not trading profitability. Under the explicit research assumptions `fee_rate=0.07` and `slippage_buffer=0.01`, the accepted 5m policy traded only three ordinary OOS markets for +0.148014 assumed-cost P&L, but the untouched final holdout also traded three markets and produced -0.418991 assumed-cost P&L. The positive ordinary-OOS slice must not be promoted, threshold-retuned, or described as a profitable strategy because the frozen untouched evidence does not confirm it.

For 15m, validation selected `no_trade` in every ordinary fold and for the final holdout, producing zero trades. That is a successful fail-closed research outcome, not a defect to be bypassed. Phase 10 may therefore build a live prediction engine with money disabled so prospective immutable predictions can be measured, but paper execution, live readiness, and real-money trading remain blocked by later build-order gates. Live trading still requires explicit user authorization.

## D-028 — Prospective evidence reporting is read-only and cannot promote automatically
**Date:** 30 Aug 2026  
**Status:** Active

After Phase 14 engineering closeout, prospective paper evidence is summarized by a separate read-only reporting path rather than by changing the paper execution worker. The reporter reads existing immutable paper settlements, prediction evaluations, and reconciliation evidence; it does not place, cancel, sign, fund, approve, settle, or mutate orders, predictions, evaluations, or live-readiness records.

The report exposes settled-trade and evaluation sample sizes, realized after-cost paper P&L, a deterministic bootstrap 95% interval for mean realized P&L, raw/calibrated Brier and log-loss means, reconciliation status, and the existing Master live-gate snapshot. Evidence gates may only be `pass`, `fail`, or `insufficient_evidence`. No fixed sample threshold or numerical prospective-calibration acceptance threshold may be invented when the canonical specification has not approved one.

The reporter must never promote a model or enable live trading automatically. `automatic_promotion` remains false, the existing Master live gate remains authoritative, and the reporting CLI is permitted to run only while `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`. Phase 15 remains blocked until every Master live-gate item independently passes and explicit real-money authorization is separately recorded.

## D-029 — Prospective official outcomes reuse the canonical Gamma snapshot-to-label-to-evaluation chain
**Date:** 31 Aug 2026  
**Status:** Active

The 31 August prospective-evidence host report observed zero prediction evaluations and zero settled paper trades. Root-cause tracing established that paper settlement depends on an immutable `live_prediction_evaluations` row, evaluation depends on an existing canonical `official-outcome-v1` label, and the canonical label depends on a preserved resolved Gamma market snapshot. Production had no always-on post-resolution path that acquired those snapshots for new prospective predictions, so otherwise valid predictions could remain unevaluated and their paper fills unsettled indefinitely.

The Phase 14 follow-up therefore adds a separate money-disabled prospective outcome-sync path. It selects only ended immutable predictions that still lack evaluation, fetches the exact market by slug from official Polymarket Gamma, and writes nothing while the market is missing or unresolved. Before any snapshot is persisted, condition ID, slug, horizon, start/end window, and Up/Down token identity must match the immutable prediction exactly; any drift fails closed. A resolved payload is stored through the existing immutable historical Gamma-snapshot repository, then the existing `official-outcome-v1` label generator is run, then the existing append-only live-prediction evaluator is run. This creates no second outcome truth source: official Polymarket resolution remains authoritative and D-017 remains binding.

Historical snapshot provenance may use the established `sha256:<64-hex>` representation while the live evaluation ledger stores the normalized bare digest. Evaluation may strip only that optional `sha256:` prefix and must still require exactly 64 lowercase hexadecimal characters; no hash tolerance, weakening, or semantic rewrite is permitted.

The outcome-sync CLI exposes only bounded one-cycle or repeated research execution, reuses the existing research/live-disabled/zero-money safety guard, and contains no wallet, signing, order-submission, promotion, or live-enable path. Host acceptance must run an exact candidate from a detached worktree, require the existing paper service to be active, record the live-predictor service state without requiring activity, require that predictor state to remain exactly unchanged, keep `/opt/bp` unchanged, and may append only official outcome/label/evaluation evidence plus paper settlements derived through the already-existing paper worker. It must not install packages, migrate production, start/stop/restart services, or install either prospective daemon. Permanent rollout remains a separate explicit step after host acceptance. The Master live gate remains unchanged and Phase 15 remains blocked.

A host-acceptance PASS is invalid if the outcome-sync cycle is a no-op. Acceptance must observe at least one ended unevaluated candidate and at least one resolved candidate, reconcile every candidate as pending or resolved, reconcile every resolved candidate to a snapshot-store result, confirm canonical label evidence for the resolved set, and append a new immutable evaluation for every resolved candidate. This deliberately proves the new production evidence path executes; it does not define a sufficiently large prospective sample and must never be reused as a profitability, calibration, or live-readiness threshold.

## D-030 — Outcome-sync acceptance is predictor-neutral; permanent prospective daemons require a separate install gate
**Date:** 31 Aug 2026  
**Status:** Active

The first production-host outcome-sync acceptance attempt on candidate `c11000bf97bcfe93b91d17134c43bbd10a5791ef` failed closed before outcome processing with `REASON=predictor_service_not_active_before`. Investigation established that this was an invalid acceptance precondition, not evidence that the outcome chain itself had failed: Phase 10 acceptance used a temporary `/run/systemd/system/bp-live-predictor.service` runtime unit and cleaned it up, and the canonical state never recorded a permanent predictor installation.

Outcome-sync host acceptance is therefore predictor-neutral. It may inspect and report the existing predictor service state, but it may not require that service to be active and may not start, stop, restart, install, or otherwise mutate it. The before/after predictor state must match exactly. The paper worker remains required active because the bounded acceptance deliberately exercises the already-installed money-disabled paper settlement path after canonical evaluations are appended.

A permanent prospective runtime is a separate deployment decision after non-deploying host acceptance. That rollout must fail closed, preserve `RESEARCH`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`, and explicitly establish both `bp-live-predictor.service` and `bp-prospective-outcomes.service` as the intended long-running research-only daemons. This correction changes no evidence threshold, promotion rule, Master live gate, or Phase 15 status.

The corrected predictor-neutral host acceptance subsequently passed on exact candidate `94afff004fcbc2ed37af0297d37c51ab50ba7098`. It exercised 54 ended candidates, resolved all 54 through official Gamma, appended 54 snapshots, 54 canonical labels, and 54 immutable evaluations, preserved the inactive predictor state and deployed checkout, and kept all real-money controls disabled. This validates the acceptance boundary and the canonical outcome/evaluation ingestion path; it does not itself establish profitability, calibration quality, prospective sample sufficiency, or live eligibility.

## D-031 — Negative prospective profitability remains a fail; research daemons may continue collecting evidence without promotion
**Date:** 31 Aug 2026
**Status:** Active

After the canonical outcome sync populated 54 immutable live-prediction evaluations, the read-only prospective-evidence reporter was rerun on exact candidate `de907d324c7ee4ec46e2dfef1eb516dbb3fa8348`. It observed two settled prospective paper trades with realized after-cost total P&L `-7.792422663291` USD and mean `-3.8962113316455` USD. The deterministic 10,000-resample bootstrap 95% interval for mean realized P&L was `[-4.285508316075, -3.506914347216]`, entirely below zero. Therefore prospective `positive_after_cost_profitability` is `fail`; this result must not be reframed as positive because the evaluation count is larger, nor retuned away post hoc using the same prospective evidence.

Calibration over 54 evaluations improved numerically after the frozen calibrator (Brier `0.11328198148148148` to `0.10868378084722523`; log loss `0.3669084283864382` to `0.35286272448721295`), but no approved prospective calibration threshold exists, so `calibration_acceptable` remains `insufficient_evidence`. No fixed prospective sample-size threshold exists either, so sample sufficiency remains `insufficient_evidence`. Reconciliation is `OK` with zero violations and remains `pass`.

This evidence does not authorize promotion or Phase 15. The Master live gate remains `fail`, `automatic_promotion=false`, and all real-money controls remain disabled/zero. A separate permanent installation of the already-approved research-only predictor and prospective-outcome daemons may proceed solely to preserve prospective evidence continuity; successful installation must not be treated as economic validation or live-gate progress.

## D-032 — Permanent prospective research daemons are operational continuity, not live-gate progress
**Date:** 31 Aug 2026  
**Status:** Active

The separately authorized permanent research runtime for `bp-live-predictor.service` and `bp-prospective-outcomes.service` is now established on production. Its sole purpose is to continue collecting immutable prospective predictions, official Gamma outcomes, canonical labels/evaluations, and money-disabled paper evidence. Installing or running these daemons cannot count as economic validation, model promotion, live-gate progress, or real-money authorization.

The first install attempt on candidate `196519555bed8f68d37654bd171dac23f681fd52` failed closed before mutation because the deployed checkout contained established dashboard-generated build/runtime residue. The approved correction does not clean or reset that production state. It allows only the explicitly identified dashboard runtime paths, fails closed on every other checkout change, rejects candidate/runtime path collisions, and preserves the tolerated tracked generated dashboard files for rollback.

Corrected exact-head install candidate `d2b2d515a4b982c691360fa1c6c46a461a665ff9` passed CI #1661 plus Historical Backfill Smoke #528, Live Recorder Smoke #635, and Recorder Short Soak #600, then passed production installation. The deployed head became `d2b2d515a4b982c691360fa1c6c46a461a665ff9`; both prospective daemons are active and enabled; recorder, PostgreSQL, dashboard API/web, and paper execution remained active; and the root-controlled runtime boundary is still `RESEARCH`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`.

D-031's negative prospective profitability result is unchanged and remains canonical. No prospective threshold was retuned, no evidence gate was upgraded by the installation, `automatic_promotion=false`, the Master live gate remains `fail`, and Phase 15 remains blocked. Sanitized evidence: `docs/evidence/phase-14-prospective-runtime-install-host-acceptance-20260831.json`.

## D-033 — V1 market-price edge is timestamp-incoherent; V2 must use separately timestamped current market-price evidence and a new validation epoch
**Date:** 2 Sep 2026  
**Status:** Active

Read-only Phase 14 attribution established that the accepted 5m V1 path compares two observations with materially different effective times. The V1 raw probability is the newest first-party Polymarket CLOB `/prices-history` Up-token point satisfying `observed_at <= scheduled_at`, requested at one-minute fidelity. The executable selected-side ask comes from compact WebSocket book state with the existing 10-second freshness contract. A 27-settled-trade timing probe found probability ages of 33–51 seconds while selected-book ages were approximately 0–1 second for every trade. This cross-source timing mismatch can create large apparent edge when the market has moved substantially between the price-history observation and the executable book.

This is an inherited research-contract defect rather than a paper-execution defect. `core-v1` exposed Polymarket token-price staleness as a feature but did not impose a token-price freshness gate; the accepted `market_price` champion uses `pm_up_price` itself; Phase 8/9 selected timing and edge policy under that asynchronous source contract; and Phase 10 faithfully materialized the same meaning prospectively. Existing V1 predictions, evaluations, paper orders/fills/settlements, and P&L remain immutable and valid evidence of the deployed V1 pipeline. They must not be rewritten or discarded, but they cannot be blended with a corrected V2 profitability epoch or used to choose V2 freshness, calibration, or edge thresholds.

The approved V2 research direction is a new versioned market-price input based on first-party Polymarket WebSocket `last_trade_price` evidence with a dedicated trade timestamp/receipt timestamp. Generic compact-state `last_event_at` is not sufficient because later book or price-change events can refresh state without refreshing the last trade. Missing or stale last-trade evidence must fail closed to no-trade. Midpoint, selected ask, opposite-token transforms, or untimestamped REST last-trade responses must not silently substitute for the V2 probability input.

The existing selected-book 10-second freshness contract remains frozen. No new probability freshness number may be selected from the 27 prospective failures. The V2 research chain must derive and freeze any source-freshness/eligibility rule independently, preserve chronological/leakage-safe selection, and create new feature/input/prediction/policy semantics rather than mutating V1.

The V1 calibrator and minimum-edge threshold cannot be automatically carried forward because they were selected under the asynchronous V1 eligibility contract. V2 must rerun the permitted calibration/edge research chain under its new source semantics. If independent historical timestamped last-trade evidence is insufficient to validate a policy, V2 remains `no_trade` while collecting a separate prospective shadow-evidence epoch. `automatic_promotion=false` remains mandatory.

This decision does not authorize live trading, Phase 15, a geographic bypass, a higher risk limit, or any real-money setting change. `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0` remain mandatory, and the complete Master live gate plus separate explicit real-money authorization remain prerequisites for any future controlled live launch.

## D-034 — Physical raw retention uses verified hourly partition drop; monolithic DELETE remains legacy compatibility
**Date:** 4 Sep 2026  
**Status:** Active

D-011 remains authoritative for the retention meaning: approximately 24 hours of hot raw PostgreSQL data followed by 24 additional hours of verified local archive retention, for roughly 48 hours of full-raw recoverability. This decision changes the primary PostgreSQL **physical retirement mechanism**, not those retention windows or the archive-before-retire safety contract.

Production evidence on 4 September showed why the distinction matters. The monolithic `raw_market_events` relation reached approximately 157 GB total while root free space approached the existing 15 GiB critical reserve. Chunked `DELETE` safely removed logically expired rows only after archive verification, but PostgreSQL retained reusable relation pages instead of returning enough relation files to the operating-system filesystem. A failed/inactive maintenance timer also demonstrated that disk-only supervision was insufficient to detect retention drift early.

The approved Phase 14 architecture therefore uses hourly PostgreSQL `RANGE(received_at)` children for `raw_market_events`, a fixed 16-way hash-partitioned `raw_event_dedupe` ledger to preserve global `dedupe_key` uniqueness, and one shared sequence for generated event IDs. An expired raw child may be dropped only after its exact canonical archive/manifest verifies, required compact feeds have advanced beyond the hour, and the live child row count still equals the verified manifest row count. Dedupe-ledger cleanup follows partition drop. Legacy/SQLite compatibility may retain bounded row deletion, but production physical-capacity safety is based on verified partition removal.

Storage health additionally requires a successful maintenance heartbeat no older than two hours, a writable current-hour partition, and retention lag within the approved one-extra-hour tolerance, while preserving the existing free-space warning/critical thresholds. Production uses deployment configuration to evaluate the protected data filesystem; `/mnt/bp-data` is a host concern and is not hard-coded as the portable application default.

Migration from a populated legacy table is never implicit recorder startup behavior. It requires the explicit exact-SHA migration/rollback path, the recorder stopped, research/zero-money gates, verified recovery archives, safe filesystem headroom, exact parity checks, and retained rollback material. Engineering verification or merge does not authorize the production migration. The migration has not been performed as of this decision.

This decision changes no V2 timing/freshness/model/calibration/edge policy, no selected-book freshness rule, no execution policy, no geographic rule, and no live-trading authorization. Gate B and Phase 15 remain blocked and `automatic_promotion=false`.

