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



## D-035 — Stopped-recorder recovery may retire a proven terminal partial raw partition at the last retained raw timestamp
**Date:** 7 Sep 2026  
**Status:** Active

D-034 remains the normal production retention contract: an hourly raw partition is archived and verified exactly, compact state must advance beyond the interval, the raw child is dropped, and only then are matching dedupe-ledger rows removed.

A controlled Phase 14 stopped-recorder recovery may use a narrower cutoff only for the terminal partially populated raw partition. This exception is allowed only when the recovery path is explicitly opted in, no raw row exists at or after that partition's nominal end, the partition contains retained raw rows, and every required compact feed has a latest `last_event_at` strictly greater than the last retained raw `received_at` in that partition. The exact cutoff and use of the terminal-partial rule must be recorded in maintenance evidence.

Normal steady-state maintenance does not enable this exception and continues to require compact advancement beyond the full nominal hourly end. Any partition with later raw evidence remains ineligible for the exception. The archive/manifest verification, raw row parity, partition drop ordering, dedupe cleanup ordering, disk-health thresholds, recorder-stopped recovery boundary, research/zero-money settings, Gate B block, and live-trading block are unchanged.

## D-036 — Already-partitioned runtime ensure is validation-only
**Date:** 9 Sep 2026  
**Status:** Active

Once the partitioned raw-storage migration has been accepted, steady-state runtime maintenance must not re-run parent-table, parent-index, dedupe-parent, or sequence bootstrap DDL as an idempotent repair mechanism. Production diagnostics on 9 September 2026 showed the 15:00 and 16:00 UTC maintenance cycles deadlocking on `CREATE INDEX IF NOT EXISTS ix_raw_event_dedupe_received_at` while the recorder was active. The recorder claims `raw_event_dedupe` before inserting `raw_market_events`; the old already-partitioned ensure path revisited raw-parent/index DDL before dedupe-parent/index DDL, creating the opposite lock order.

For an already-partitioned runtime, `ensure_partitioned_raw_storage` therefore validates the accepted parent schema read-only and fails closed on drift, then provisions only the current plus two future hourly raw partitions. The read-only contract requires the raw ID sequence, the partitioned dedupe parent, the required raw/dedupe parent indexes, and the exact 16 dedupe hash children. Bootstrap and explicit migration keep their existing DDL creation behavior.

This decision does not weaken partition availability, retention, maintenance-freshness, disk thresholds, archive verification, dedupe semantics, the fail-closed recorder stop, or research/zero-money controls. A recorder restart after a fail-closed production stop remains a separate production mutation requiring explicit authorization.

## D-037 — Production deadlock recovery uses a minimal backport candidate
**Date:** 9 Sep 2026  
**Status:** Active

The accepted production checkout remains `895c6bd2f9409f16bf5d544b26b30e20ecbfe43a`, while current main contains 153 later commits. Recovering the recorder from the 9 September steady-state storage deadlock must not use that broad checkout transition merely to obtain the one runtime fix.

The production recovery candidate is therefore a clean descendant of the accepted checkout whose deployed-from diff is restricted to the exact-main `src/bp_engine/storage/partitioned_raw.py` deadlock fix plus its PostgreSQL regression test. The exact candidate is `e9c7afc1536880e4612cb6e3d1a7282fa37c69f5` on `ops/phase14-storage-deadlock-recovery-candidate`. The runtime and test Git blobs must remain byte-identical to the exact current-main helper head, and any branch movement, extra path, or blob mismatch fails closed before production checkout mutation.

The recovery gate requires the already accepted `RECORDER_WRITER_WORKERS=4` setting and does not edit the environment file. It validates healthy partitioned storage before mutation, runs one maintenance cycle with the recorder stopped, then proves the fixed lock path under real recorder load by forcing a second maintenance cycle while the recorder is active and requiring the recorder MainPID/restart count to remain unchanged. Any post-checkout failure stops the recorder and returns the checkout to the accepted head. Production execution remains a separate explicit authorization; Gate B, final-holdout access, automatic promotion, Phase 15, and live/money settings remain blocked.

## D-038 — READY=true ends the feature-chronology blocker but does not authorize Gate B
**Date:** 10 Sep 2026
**Status:** Active

The accepted Phase 14 V2 feature-only readiness run at `2026-09-10T09:07:50Z` returned `READY=true` under the frozen Gate B planning contract. The exact-main helper observed 651 immutable V2 markets, accepted analysis start `2026-09-09T10:20:00Z`, produced five eligible ordinary folds, and identified 24 feature-only markets in the final-holdout time window. The readiness run read no labels, wrote no plan or selection artifact, did not evaluate final-holdout outcomes, and preserved `HOLDOUT_TOUCHED=false`.

This result closes only the feature-chronology readiness blocker. It does not authorize Gate B execution or permit labels/outcomes to be joined, a Gate B plan or selection to be frozen, the final holdout to be evaluated, or any V2 policy/model/calibration/edge/min-edge choice to be accepted. Those actions remain behind a separate explicit Gate B authorization. Automatic promotion remains false, Phase 15 remains blocked, live trading remains disabled, and real-money limits remain zero.

Because the readiness prerequisite is now satisfied, repeated readiness polling is no longer required for the current Gate B path. The optional readiness watcher remains uninstalled; no production installation should be inferred or performed from `READY=true`.

## D-039 — Gate B research execution is authorized once without downstream promotion
**Date:** 10 Sep 2026
**Status:** Active

The accepted `READY=true` prerequisite is merged to `main`. Explicit authorization is granted for one Phase 14 V2 Gate B research execution under the frozen contract: label-free `plan`, labeled non-holdout `prepare`, then one-time `evaluate-holdout` against the hash-bound final holdout. Validation selection must be frozen before final-holdout labels are read, and a produced holdout artifact makes the final evaluation one-shot; its result must not be used to select another holdout or alter preregistered search geometry.

This authorization covers research evaluation only. It does not itself accept any V2 policy/model/calibration/edge/min-edge selection and does not authorize prospective V2 activation, automatic promotion, Phase 15, geographic bypass, live trading, or nonzero money. `MODE=research`, `LIVE_TRADING_ENABLED=false`, zero money limits, and `automatic_promotion=false` remain mandatory.



## D-040 — A failed Gate B prepare freezes its plan; label repair and holdout resume require separate SHA-bound authorization
**Date:** 10 Sep 2026
**Status:** Superseded by D-041

**Decision:** The Gate B attempt started at `2026-09-10T10:27:33Z` consumed D-039's one-shot execution authorization when it successfully wrote `plan.json` and entered `prepare`. Its subsequent missing non-holdout canonical-label failure does not authorize a clean rerun or a new plan. The existing `plan.json` is frozen and must be reused byte-for-byte.

Non-holdout label recovery is a distinct production mutation boundary. Before any repair, a holdout-blind audit must bind the exact failed evidence directory and frozen plan SHA-256. Repair, if separately authorized, may append only canonical post-resolution Gamma-derived `official-outcome-v1` evidence for non-holdout condition IDs already in that frozen plan. It must not read final-holdout labels, write selection/holdout/summary artifacts, change the plan, or select policy.

Gate B resume is a second distinct boundary. It requires a fresh audit with zero missing non-holdout labels plus a separate explicit approval bound to the exact current main head and the exact frozen `plan.json` SHA-256. Resume cannot perform label repair or replan; it may only execute the existing `prepare -> evaluate-holdout` sequence, with the final holdout evaluated once.

**Reason:** This preserves the originally authorized chronology and validation geometry while allowing a narrowly scoped canonical-data repair. It prevents a failed prepare from becoming an implicit authorization to redraw the sample, peek at the holdout, or silently expand production mutation scope.

**Safety:** At the time of this decision `selection.json`, `holdout.json`, and `summary.json` are absent and `HOLDOUT_TOUCHED=false`. No repair/resume production action is authorized by this decision. Research-only zero-money interlocks remain unchanged; V2 acceptance, automatic promotion, Phase 15, geographic bypass, live trading, and nonzero money remain blocked.

## D-041 — A touched final holdout consumes the frozen Gate B plan; future Gate B requires a fresh plan and holdout
**Date:** 11 Sep 2026
**Status:** Active

**Decision:** The separately authorized recovery/resume sequence for frozen plan SHA-256 `8f2a756161bb0d85e6020d6ff0d6f4f3540eb28caf133870a4926f65ac7d2fea` is closed. The resume durably created and fsynced `holdout-attempt.json` before final-holdout evaluation, then failed closed while loading final-holdout supervised input because condition `0x2a760ccdb973c19ce13b6af86d752cf377790d5149a46b8498462904ece86efd` lacked canonical supervised input. The marker makes `HOLDOUT_TOUCHED=true`; that final holdout and frozen plan are consumed and must not be reused. Gate B was not accepted.

PR #179 fixes the future feature-only outcome-label coverage class in source, but its production rollout is a separate production-mutation boundary. After any accepted rollout, the next Gate B attempt must begin from fresh feature-only readiness, create a fresh statistically clean plan, and reserve a new final holdout. Final-holdout access remains a new one-shot explicit authorization boundary; the consumed holdout may not be cherry-picked or inspected again to shape the replacement plan.

**Reason:** Once final-holdout access has begun under the durable attempt marker, reusing that holdout or plan after an input failure would allow post-hoc adaptation to a touched holdout. Requiring a fresh planning epoch and new holdout preserves the statistical meaning of the one-shot Gate B contract.

**Safety:** `MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, `MAX_DAILY_LOSS_USD=0`, and `automatic_promotion=false` remain mandatory. Gate B remains unaccepted; V2 activation, Phase 15, geographic bypass, live trading, and nonzero money remain separately blocked.

## D-042 — Resolved markets drive adaptive supervised learning
**Date:** 12 Sep 2026
**Status:** Active

**Decision:** Adaptive supervised retraining is triggered by resolved eligible markets, not by executed trades alone. The initial readiness threshold is **50 newly resolved eligible markets** for the exact horizon, feature version, and official label version. A market with an official resolved label and matching frozen pre-resolution features is learnable even when the current policy chose `NO_TRADE`.

The adaptive cycle reuses the existing deterministic modeling and Phase 13 champion/challenger infrastructure, records append-only readiness/training/cycle identities, and keeps `automatic_promotion=false`. It may not access a final holdout, activate a paper model, enable live trading, change money limits, or advance Phase 15.

The 12 September V2 Gate B evidence is complete but not accepted: the final policy is `no_trade`, with selection reason `no_validation_edge_candidate_profitable`; the one-shot final holdout was touched and is permanently consumed. That evidence may inform diagnosis but may not be reused for retuning or replacement-policy selection.

**Reason:** The model's statistical learning target is the official resolved market outcome. Restricting learning to executed trades would create policy-dependent sample selection and would prevent a cautious or `NO_TRADE` policy from learning from the larger set of markets it observed. Economic trade outcomes remain required evidence for deployment decisions, but they are not the sole supervised-learning examples or retraining trigger.

**Safety:** Phase 14 remains live-gate blocked. `MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, `MAX_DAILY_LOSS_USD=0`, and `automatic_promotion=false` remain mandatory. Paper activation, any new final-holdout access, Phase 15, geographic bypass, live trading, and nonzero money remain separately blocked.

## D-043 — Freeze first adaptive-cycle bootstrap boundary
**Date:** 12 Sep 2026
**Status:** Active

The first adaptive supervised-learning cycle uses immutable `bootstrap_since_at=2026-09-12T17:21:13Z`. This timestamp is the exact merge time at which the adaptive subsystem became canonical on `main`, so first-cycle readiness counts only newly resolved eligible markets whose learning availability occurs after that boundary.

After a completed adaptive cycle exists, the next cycle derives `since_at` only from the previous cycle's frozen cutoff. The first-cycle bootstrap boundary cannot be reset or moved forward to manufacture a newer evidence window. This decision changes no model, promotion, paper activation, live-trading, holdout, or money authorization boundary.

## D-044 — Allow read-only first-cycle readiness before ledger migration
**Date:** 12 Sep 2026
**Status:** Active

Before migration 0015 creates `adaptive_learning_cycles`, first-cycle `adaptive-readiness` may inspect immutable labels/features using the explicit frozen bootstrap boundary `2026-09-12T17:21:13Z`. This fallback is read-only: it must not create the ledger table, write cycle evidence, train a challenger, or infer a prior-cycle cutoff. If the table is absent and no explicit bootstrap is supplied, readiness fails closed.

Once `adaptive_learning_cycles` exists, the normal ledger lookup is authoritative. `adaptive-train` still requires the ledger and remains blocked until migration 0015 is separately authorized and applied. This decision changes no paper activation, automatic promotion, final-holdout, live-trading, or money authorization.

## D-045 — BTC-first V3 Gate A
**Date:** 13 Sep 2026
**Status:** Active

Repository implementation for the separately versioned `core-v3-btc-native` Gate A challenger is complete on PR #192 at audited implementation head `e09e9834260996553fa3cff7c18bf5269e48f4f0`; CI run `34757241403` passed before the source-of-truth handoff. The forecast contract is BTC-native only (Coinbase spot, Bybit spot, Bybit linear) at fixed 60/120/180/240-second offsets for 5-minute markets. Polymarket price/book inputs are excluded from the V3 forecast payload and remain an execution/evaluation concern. Outcome-blind coverage reporting and future-data perturbation regression coverage are part of Gate A.

V2 adaptive training remains paused despite prior readiness evidence. Existing V1/V2 features, adaptive-readiness evidence, Gate B evidence, and consumed holdout history remain immutable and truthful; they are not reset or rewritten by V3. The 84-trade diagnosis cohort is not a validation/test/final-holdout set for policy selection.

This decision records repository implementation only, not Gate A acceptance or production rollout. No training, production migration, model activation, paper activation, final-holdout access, automatic promotion, Phase 15 progression, geographic bypass, live trading, or nonzero money is authorized. The next step is outcome-blind V3 coverage collection followed by a separate Gate A acceptance decision.


## D-046 — BTC-first V3 Gate A production coverage accepted

**Status:** Active

Production materialization/coverage for `core-v3-btc-native` is accepted PASS from `docs/evidence/phase-14-v3-gate-a-production-20260913.json`: 17 markets / 68 rows, coverage input SHA-256 `32c283a7769681ebe5b2e0d1fe255ad6c38aa5b0301303f8fe86f4e7b2278ffb`, zero future-cutoff violations, zero Polymarket predictor keys, and complete current-state availability across the three BTC sources. This acceptance did not perform model training, model activation, final-holdout access, automatic promotion, or live-trading change.

## D-047 — V3 Gate B preregistration frozen

**Status:** Active

Freeze the approved V3 Gate B preregistration design at `c9e179c91ea990ca4a25a13f69fc5932811fb32a` and implementation checkpoint `39a887e138398215ac97dc45f8099e3515a90fe2` (Issue #193, CI `34775202056`). The prospective epoch is `2026-09-13T13:45:00Z` through `2026-09-16T13:45:00Z`, with exactly five ordinary folds and a separate 12-hour final reserved window. The complete future search contract is frozen and hash-bound. Historical `diagnosis` and `consumed_v2_final_holdout` exclusion manifests remain mandatory, and supplied exclusions may not intersect the prospective V3 epoch. After epoch completion, proceed only through outcome-blind readiness and then, if ready, a fixed feature-only plan. No labeled modeling or final-holdout access is authorized here.

## D-048 — Retire Gate B v1 and execute successor v2 with structural prospective contamination boundary

**Date:** 14 Sep 2026
**Status:** Active

`v3-gate-b-preregister-v1` remains immutable historical preregistration evidence but is retired and non-executable for model/policy selection because the exact 84-trade diagnosis identities were not durably frozen before its `2026-09-13T13:45:00Z` to `2026-09-16T13:45:00Z` prospective epoch. No post-hoc diagnosis reconstruction is allowed. Data from that epoch may be retained only as engineering, source-availability, leakage, and coverage evidence. The complete 48-condition consumed-V2 final-holdout contamination boundary remains permanently non-reusable historical evidence. Gate A coverage remains accepted under `32c283a7769681ebe5b2e0d1fe255ad6c38aa5b0301303f8fe86f4e7b2278ffb` and the historical preregistration design commit remains `c9e179c91ea990ca4a25a13f69fc5932811fb32a`.

The approved clean successor is `v3-gate-b-preregister-v2` for `core-v3-btc-native`, with epoch `[2026-09-16T13:45:00Z, 2026-09-19T13:45:00Z)`. Contamination control is structural: only `market_start_at >= epoch_start AND market_start_at < epoch_end` may enter train/validation/test/final-holdout membership, so every pre-epoch market is ineligible. Historical diagnosis and consumed-V2 manifests are evidence only and are not successor runtime selection inputs. The v1 predictor/model/calibration/economic search contract is otherwise unchanged.

Repository runtime checkpoint `659d9524fe8bfeba182b7cf7c8d9b664280f7562` passed CI `34841954823`. The implementation preserves outcome-blind readiness, feature-only planning, PostgreSQL read-only transactions, and no-clobber plan output. No successor readiness or plan has been run yet. No model fitting, final-holdout access/evaluation, activation, production mutation, live trading, automatic promotion, geographic bypass, or money-limit change is authorized by this decision.

## D-049 — Create a separate regime-aware V4 challenger after consumed V3 holdout

**Date:** 20 Sep 2026
**Status:** Active

The V3 successor one-shot final holdout is complete and permanently consumed. It produced positive aggregate holdout economics, but the trade ledger was directionally asymmetric: UP trades were materially stronger than DOWN trades. This result is evidence that regime robustness deserves explicit study, not authorization to disable DOWN trades or tune V3 after seeing the holdout.

Create a separately versioned `core-v4-regime-aware` challenger. Preserve the V3 BTC-native short-horizon inputs and add timestamp-coherent 5m, 15m, and 60m returns from Coinbase spot, Bybit spot, and Bybit linear. Regime classification is fixed without a learned magnitude cutoff: each horizon uses a majority sign across venues; bull requires all three horizon directions positive, bear requires all negative, and complete mixed directions are sideways/mixed. Missing direction evidence remains unknown.

The consumed V3 holdout may motivate this hypothesis but may not select V4 thresholds, confidence cutoffs, side filters, model hyperparameters, calibration, or economic policy. V4 must use a new prospective cohort frozen after the V4 feature implementation/collection boundary. Every future V4 evaluation must report performance separately for bull, bear, sideways/mixed, and unknown regimes.

This decision authorizes repository research implementation only. Production feature materialization, model training, paper activation, automatic promotion, Phase 15, live trading, geographic bypass, and nonzero money remain separately controlled boundaries.

## D-050 — Authorize isolated prospective V4 production feature collection

**Date:** 20 Sep 2026
**Status:** Active

Authorize production materialization and continuous collection of immutable `core-v4-regime-aware` feature rows only. The prospective collection boundary is fixed at `2026-09-20T12:40:53Z`, the merge time of the V4 regime-aware foundation. Only completed 5-minute markets with `market_start_at >= 2026-09-20T12:40:53Z` are eligible.

The collector must remain outcome-blind and research-only. It may discover completed eligible markets, materialize the four fixed 60/120/180/240-second V4 feature rows using Coinbase/Bybit compact state, preserve existing immutable rows, report regime/source coverage, and write operational evidence. It must fail closed on future source cutoffs, Polymarket predictor keys, invalid regime one-hot state, or non-research/nonzero-money configuration.

To avoid an unrelated production application deployment, the collector runs from a separate versioned runtime under `/var/lib/bp/runtime` using the existing `/opt/bp/.venv`. It must leave the deployed `/opt/bp` checkout unchanged and must not restart the recorder.

This authorization does not include labels as selection inputs, V4 model training, calibration, edge/threshold search, Gate B planning, final-holdout construction/access, paper activation, automatic promotion, Phase 15, geographic bypass, live trading, or nonzero money.

## D-051 — Accept V4 forward collector production rollout and remain in collection mode

**Date:** 20 Sep 2026
**Status:** Active

The authorized isolated V4 production feature collector passed rollout acceptance on candidate `36b02d0687194173ab5d3862d3b88c6c90607574`. It runs from the isolated versioned runtime under `/var/lib/bp/runtime`, while the deployed `/opt/bp` checkout remains unchanged at `7c3af78da1922a0e5187c24b799951130cc98887`.

The first accepted cycle processed 9 completed prospective 5-minute markets and inserted 36 immutable V4 feature rows. Regime coverage was 2 bull, 0 bear, 7 sideways/mixed, and 0 unknown. Future-cutoff violations, Polymarket predictor leakage, and regime-invariant violations were all zero. Training, policy selection, automatic promotion, model activation, and recorder restart were all false.

Because the first cycle contains no bear markets and only nine total markets, this evidence establishes collector correctness and initial coverage only. It is not model-performance evidence and must not be used to tune V4.

Continue prospective collection. Before fitting any V4 model, freeze a new V4 Gate B preregistration over a fresh cohort with meaningful regime coverage and an untouched final holdout.

## D-052 — Activate frozen V3 prospectively in isolated zero-real-money paper mode

**Date:** 20 Sep 2026
**Status:** Active

Authorize prospective paper trading of the exact frozen V3 Gate B successor only. The model artifact SHA-256 is `124627e15cab3997b8abe54ec5237450d976ab5682953f45a1399a76b6dae0e7`. The model artifact, calibration, 240-second timing, 0.075 minimum cost-adjusted edge, 0.07 fee coefficient, 0.01 slippage buffer, and 10-second selected-book freshness contract are immutable.

The paper signal version is `v3-frozen-paper-v1`; the paper execution version is `paper-execution-v3-frozen-v1`. The activation timestamp becomes a hard lower bound on market start time. No historical or already-started market may be added to the V3 paper epoch.

The existing Phase 12 paper broker is preserved for legacy research signals but must explicitly exclude the V3 prediction version. V3 paper orders, virtual cash, fills, and settlements are isolated by their execution version.

Use the established $100 virtual starting cash and $5 virtual target notional with 250ms simulated latency and 2s order TTL. These values are simulated execution settings only. Real-money limits remain zero.

V4 collection continues. This decision does not authorize V3 tuning, V4 training, automatic promotion, Phase 15, wallet/signing access, real Polymarket order submission, geographic bypass, or live trading.

## D-053 — Accept frozen V3 paper activation and observe without tuning

**Date:** 20 Sep 2026
**Status:** Active

Accept the production rollout of frozen V3 paper trading at `2026-09-20T15:39:45Z`. Candidate `9d52eb753355365848a637ffa6663928664bf770` passed the guarded activation with the exact frozen model SHA, isolated V3 prediction/execution identities, zero pre-activation predictions, zero invalid order sources, zero real money, unchanged recorder PID, and V4 collection still active.

Continue prospective paper observation only. V3 paper results may be measured and reported, including simulated fills, settlements, side-specific outcomes, virtual cash, and realized paper P&L, but they must not be used to refit, recalibrate, change `min_edge=0.075`, alter paper sizing, or automatically promote the model.

Live-order access, Phase 15, geographic bypass, live trading, and nonzero real-money limits remain unauthorized.

## D-054 — Treat V4 as comprehensive V3 weakness remediation, not regime-only

**Date:** 20 Sep 2026
**Status:** Active

V4 is the full successor research program for the documented weaknesses exposed by V3. Regime robustness remains important, but it is only one required objective.

The next V4 Gate B preregistration must cover regime dependence, UP/DOWN asymmetry, model simplicity/feature underuse, calibration robustness, decision timing, trade-quality versus coverage, drawdown/loss robustness, and execution availability. Candidate models must include a simple baseline, multivariate BTC-native approaches, and at least one nonlinear BTC-native challenger using only the frozen V4 predictor set.

The existing prospective V4 collector remains unchanged. The consumed V3 holdout may motivate these questions but may not provide numeric tuning values or acceptance thresholds.

## D-055 — Steady-state raw partition retirement detaches concurrently before physical drop
**Date:** 21 Sep 2026  
**Status:** Active

D-034 remains authoritative for retention windows and the archive-before-retire safety contract, and D-036 remains authoritative for the already-partitioned runtime bootstrap path. Production diagnostics on 21 September 2026 exposed a separate steady-state contention class: the hourly maintenance cycle completed and verified the expired raw archive but then waited long enough in raw-partition retirement to hit the existing 55-minute systemd timeout while the four-writer recorder remained active. With the recorder later stopped by the existing fail-closed storage-health chain, the following maintenance cycle retired both overdue hours successfully. Disk reserve remained healthy.

Normal partitioned PostgreSQL retirement therefore uses this order: verify the exact canonical archive/manifest, require compact-state advancement, verify the physical hourly child row count equals the archive manifest, detach that child with `ALTER TABLE raw_market_events DETACH PARTITION ... CONCURRENTLY`, verify the now-stable standalone table still has the exact manifest row count, physically drop that standalone table, and only then remove matching rows from the hash-partitioned dedupe ledger. This preserves D-034's data-safety ordering while avoiding the parent-table `ACCESS EXCLUSIVE` lock required by direct attached-child `DROP TABLE`.

Concurrent detach is explicitly restartable. If PostgreSQL records the child with `pg_inherits.inhdetachpending=true`, the next maintenance attempt must complete `DETACH PARTITION ... FINALIZE`; if detach completed but physical drop did not, the standalone hourly table remains an explicit retirement candidate. Such retained physical tables continue to count toward retention lag and raw physical bytes, and nonempty detached intervals cannot satisfy the archive-prune raw-empty guard. A retry must reverify row-count parity before physical drop and dedupe cleanup.

The 55-minute service timeout, two-hour maintenance-freshness guard, one-extra-hour retention-lag tolerance, free-space thresholds, 24-hour hot raw retention, 24-hour additional archive retention, and fail-closed recorder-stop behavior are unchanged. Increasing the service timeout is not the remedy for this incident class.

This decision is engineering source truth only until a separately authorized exact-SHA production rollout passes active-recorder maintenance acceptance. It does not authorize a production checkout change, service restart, schema migration, V3/V4 model change, automatic promotion, Gate B action, Phase 15, live trading, or nonzero money limits.

## D-056 — Freeze V4 Gate B v1 on a wholly future seven-day cohort
**Date:** 22 Sep 2026  
**Status:** Active

**Decision:** Freeze `v4-gate-b-preregister-v1` before any V4 selection labels are read. The only eligible selection cohort is `2026-09-23T00:00:00Z <= market_start_at < 2026-09-30T00:00:00Z`. Earlier V4 rows, including the 373-market / 1,492-row read-only observation cohort, remain coverage/engineering evidence only and cannot enter V4 train, validation, ordinary test, or final-holdout membership.

The preregistration doubles V3's time windows to 48h train / 12h validation / 12h ordinary test with 12h steps, seven ordinary folds, one-market embargo, and a final untouched 24h holdout. Feature-only readiness requires exact offsets, zero leakage/predictor/regime-invariant violations, >=90% current/short-return availability, >=75% long regime-return availability, and at least 120 distinct markets observed in each bull, bear, and sideways/mixed regime.

The global candidate ladder is `training_prior`, `single_feature_btc_logistic`, `short_context_v4_logistic`, `full_v4_logistic`, and `full_v4_xgboost`, with identity/Platt calibration and all four 60/120/180/240-second offsets. The finite edge grid remains `0, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15` plus `no_trade`. Side-specific and regime-specific policy tuning are forbidden in v1; those slices are mandatory diagnostics only.

**Reason:** V4 must address regime dependence, side asymmetry, feature underuse, calibration, timing, coverage/quality, drawdown/losses, and execution availability without converting the consumed V3 holdout or the pre-epoch V4 observation cohort into tuning data. A future-only epoch and feature-only plan make the contamination boundary structural and auditable.

**Boundary:** This decision authorizes continued feature collection plus outcome-blind readiness and feature-only/no-clobber planning after epoch close. It does not authorize V4 labels/outcomes for selection, model/calibration fitting, economic-policy selection, final-holdout access, paper activation, automatic promotion, Phase 15, live trading, geographic bypass, or nonzero money.

## D-057 — Pursue frozen V3 controlled live transition only through the complete Master gate
**Date:** 23 Sep 2026  
**Status:** Active

The user explicitly authorizes pursuing a controlled real-money transition for the exact frozen V3 while V4 research collection continues in parallel. The authorization applies only to the frozen model SHA `124627e15cab3997b8abe54ec5237450d976ab5682953f45a1399a76b6dae0e7`, prediction identity `v3-frozen-paper-v1`, execution identity `paper-execution-v3-frozen-v1`, 240-second timing, and `min_edge=0.075`.

This decision satisfies the human-authorization requirement but does **not** waive any other Master live-gate row. Before real-money activation, a fresh V3-specific read-only reassessment must cover uncertainty, after-cost P&L robustness, largest-winner sensitivity, calibration, drawdown/losing streak, risk/kill-switch readiness, reconciliation, and current direct geographic eligibility. Any failure or insufficient evidence keeps Phase 15 blocked. Geographic restrictions may not be bypassed.

V3 refit, recalibration, threshold/timing/sizing changes, automatic promotion, and V4 Gate B label/training/policy actions remain outside this authorization. Live trading stays disabled and real-money limits remain zero until the complete gate passes.


## D-058 — Keep frozen V3 live gate closed after positive paper economics because geography fails
**Date:** 23 Sep 2026  
**Status:** Active

The one-shot frozen-V3 read-only production reassessment completed on exact main `ba98b3871e03895d04bb2b06d4be5350f6c17491`. The paper economics now satisfy the existing profitability rule: 76 settled trades produced +$682.252111761097 realized after-cost P&L, and the deterministic bootstrap 95% interval for mean P&L was entirely above zero. Execution/reconciliation remains clean and explicit user live authorization is present.

This does not open the Master live gate. Sample sufficiency, calibration acceptance, and walk-forward stability remain `insufficient_evidence` under the current canonical rules. More importantly, the production VM's direct official Polymarket geoblock response returned `blocked=true` for `US/SC`, so geographic compliance is `fail`. Overall live gate therefore remains `fail` and Phase 15 is blocked.

Preserve the evidence at `docs/evidence/phase-14-v3-live-gate-reassessment-production-20260923.json`. Do not rerun the one-shot reassessment absent a separately versioned reason, do not tune frozen V3 from this result, and do not use VPNs, proxies, tunnels, or relocation tricks to bypass geographic restrictions. Continue frozen V3 paper observation and V4 prospective collection unchanged.


## D-059 — Freeze accelerated V3 statistical gate mapping before new reliability diagnostics
**Date:** 23 Sep 2026  
**Status:** Active

To pursue the user's requested same-day controlled V3 canary without weakening the project's evidence discipline, freeze a separate read-only `phase15-v3-canary-readiness-v1` audit.

Sample sufficiency reuses the pre-existing Phase 13 uncertainty rule rather than inventing a magic count: the prospective after-cost mean-P&L 95% lower bound must be strictly positive. Walk-forward stability requires the preregistered five-fold V3 ordinary validation economics gate, positive untouched V3 final-holdout economics, and positive prospective paper uncertainty.

Calibration receives one new reliability test whose acceptance rule is fixed before the diagnostics are observed: prospective Brier/log loss no worse than the frozen V3 final holdout, calibration-intercept 95% interval containing 0, and calibration-slope 95% interval containing 1. ECE remains descriptive. A failed calibration audit cannot be repaired by weakening these criteria after the fact.

This decision does not authorize live activation. Geographic compliance is independent: the user's ordinary physical connection and the eventual execution host must both be unblocked by the official Polymarket geoblock check without VPN/proxy/tunnel circumvention. Until the complete Master gate passes, wallet/signing access, authenticated client construction, live orders, Phase 15 production activation, and nonzero money remain blocked.


## D-058 — Freeze a same-day V3 canary-readiness audit without tuning the strategy
**Date:** 23 Sep 2026  
**Status:** Active

The user requested the shortest compliant path toward a controlled frozen-V3 live canary. The project therefore freezes `phase15-v3-canary-readiness-v1` before reading any new calibration intercept/slope diagnostics.

The sample-sufficiency row reuses the accepted Phase 13 uncertainty principle rather than inventing a round-number trade threshold after seeing the current 76-trade paper sample. It passes only when the deterministic prospective mean realized after-cost P&L 95% lower bound is strictly positive.

Walk-forward stability is supported only when the pre-registered five-fold V3 ordinary validation economics gate passed, the untouched V3 final holdout has positive after-cost P&L, and the prospective paper mean-P&L lower confidence bound is positive. The frozen V3 implementation forced `no_trade` when the ordinary validation economics gate failed; the immutable selected policy is `trade_threshold` at `min_edge=0.075`, so the frozen ordinary gate necessarily passed.

Calibration acceptance is frozen before reading the new reliability diagnostics: prospective calibrated Brier/log loss must be no worse than the frozen pre-paper holdout, and deterministic bootstrap 95% intervals for calibration intercept/slope must contain 0/1 respectively. ECE is reported descriptively and cannot acquire an after-the-fact threshold.

Geography remains an independent hard gate. User-provided direct official geoblock evidence from the ordinary physical network reports `blocked=false`, `NG/LA`; the IP is not persisted. The latest execution host remains blocked in `US/SC`. No VPN, proxy, tunnel, or other physical-location circumvention is allowed. Statistical PASS alone cannot authorize real money.

The new production bridge is read-only and cannot access wallet/signing material, construct an authenticated trading client, create an activation manifest, enable live trading, or change nonzero money limits. V3 remains frozen and V4 collection continues unchanged until the complete Master live gate passes.


## D-059 — Statistical V3 gate passes; isolate live execution to a directly unblocked host
**Date:** 23 Sep 2026  
**Status:** Active

The separately frozen accelerated V3 readiness audit completed read-only and passed every statistical live-readiness row. The result is durable at `docs/evidence/phase-15-v3-accelerated-readiness-production-20260923.json`. This does not alter the frozen V3 strategy and does not by itself authorize real-money submission.

The user’s ordinary physical-network check is unblocked (`NG/LA`) and the IP is intentionally not persisted. The current production host remains directly blocked from `US/SC`. Therefore the current host must never submit real orders.

The next authorized production mutation is limited to provisioning one dedicated execution-only candidate `bp-v3-canary-exec` in GCP `africa-south1-a` (Johannesburg), machine type `e2-micro`, after explicit acknowledgement that the VM may incur charges. The candidate must contain no trading software, wallet, private key, or live service during the probe. It may only call the official direct Polymarket geoblock endpoint. A blocked or invalid response requires automatic deletion and leaves the live gate closed. An unblocked response permits only the next separately reviewed canary-deployment package.

No VPN, proxy, tunnel, or other mechanism may be used to disguise a restricted user or route blocked-host Polymarket traffic through the candidate. V3 paper and V4 collection continue unchanged.


## D-060 — Open Phase 15 only for a one-order frozen-V3 canary
**Date:** 23 Sep 2026  
**Status:** Active

The dedicated Johannesburg execution candidate passed the direct official Polymarket geoblock check (`blocked=false`, `ZA/GP`). The user's ordinary physical connection had already passed (`blocked=false`, `NG/LA`). Combined with the accelerated frozen-V3 statistical PASS, execution/reconciliation PASS, risk/kill-switch engineering PASS, and explicit user authorization, every Master live-gate row is now `pass`.

Phase 15 opens only for one tightly bounded frozen-V3 canary. The user explicitly stated that up to **$10 per market** is acceptable risk. This is a hard ceiling, not a new strategy target. The first live order retains the exact frozen paper target of **$5**.

The live policy is `v3-live-canary-v1`: $5 frozen strategy target under a $10 maximum trade, $10 maximum total exposure, $10 daily-loss stop, one consecutive-loss stop, one accepted-order maximum, **one network submission attempt maximum**, minimum edge 0.075, at least $5 fresh displayed selected-side liquidity, and a two-second resting-order TTL followed by cancellation of any remaining order.

Only a NEW frozen-V3 paper order created after the canary activation timestamp may be prepared. Historical paper orders are forbidden. The existing US production host remains data/risk/audit only and may never hold wallet/private-key material or make an authenticated Polymarket order request. Signing and authenticated order submission occur only on `bp-v3-canary-exec` in Johannesburg.

The operational sequence is intentionally split: bootstrap signer with the kill switch engaged and no order; require a fresh official-account preflight with zero open orders and at least $5 collateral; prepare and durably persist one risk-approved intent with no order; explicitly arm for at most 45 seconds with `PHASE15_ACCEPT_REAL_MONEY=yes` and bind the arm to the exact intent/request/executor hashes; then the user manually submits only the prepared payload. The executor rechecks geography/account/binding and atomically re-engages its kill switch before the pinned SDK's direct `post_order()`, making the arm one-shot without the higher-level allowance-recovery retry helper. Ambiguous outcomes fail closed.

No automated real-money submission is authorized. No second submission attempt or retry is authorized. Official order/fill reconciliation is mandatory before any additional live action. A successful canary does not authorize stake growth. Frozen V3 and the preregistered V4 Gate B process remain unchanged.


## D-061 — Close expired prepared intents without consuming the one network attempt
**Date:** 23 Sep 2026  
**Status:** Active

A Phase 15 prepare persists a live intent before any arm or network submission. If that prepared market becomes unarmable and the arm fails before activation/submission, the persisted intent must not be silently deleted or mislabeled as a rejected/unknown submission. It is reconciled with the distinct terminal event `closed_before_submission`.

This closure is permitted only after the Johannesburg executor proves the kill switch is engaged, activation is invalid, submission is not ready, `live_order_submitted=false`, the official account has zero open orders, and collateral remains at least $5. The closure writes a zero-unresolved reconciliation record and does **not** increment the canary submission-attempt count.

The one-network-attempt rule from D-060 is unchanged: only `accepted`, `rejected`, or `submission_unknown` consumes that attempt. After a verified `closed_before_submission` reconciliation, a later NEW frozen-V3 signal may be prepared under the same original one-order authorization. No retry after an actual network submission is authorized.


## D-062 — Require an armable window before persisting a canary intent
**Date:** 23 Sep 2026  
**Status:** Active

The Phase 15 live-risk minimum time to expiry remains 15 seconds. Separately, the prepare step now requires at least 30 seconds remaining before it may persist a live intent. This is an operational safety margin above the arm helper's 20-second freshness requirement.

A candidate that passes the strategy/live-risk checks but has less than 30 seconds remaining is recorded as evaluated and returned as `insufficient_arm_window`; no live intent is persisted. This tightens the canary workflow without changing model behavior, target notional, live-risk thresholds, or the one-network-attempt rule.


## D-063 — Permit a bounded persistent prepare-only watcher
**Date:** 24 Sep 2026  
**Status:** Active

The user explicitly authorized a persistent Phase 15 prepare watcher so Cloud Shell disconnects do not terminate the no-order waiting loop.

The watcher may run only on the existing US recorder as a separate sidecar for at most two hours per authorized start. It must run as user `bp` in research mode with live trading disabled and zero money limits, contain no wallet/private-key material, use localhost-only networking, and remain disabled across VM reboot. It may call the same current Phase 15 `prepare_next_canary` logic against the frozen V3 runtime and may persist at most the same risk evidence/live intent/prepared payload that the existing prepare helper would create.

The watcher has no arm path and no authenticated order-submission path. Arm remains an explicit Cloud Shell action gated by `PHASE15_ACCEPT_REAL_MONEY=yes`; the single network submission remains manual and exactly once. The status helper may materialize a remote prepared payload only when at least 20 seconds remain to market end and the watcher-start commit is binding-equivalent to current `main` across the prepare runner, current live-risk/canary modules, sidecar unit, arm helper, and executor. Documentation/evidence-only commits do not invalidate an otherwise identical watcher. Any bound runtime/execution-file change fails closed into the existing closed-before-submission reconciliation path.



## D-067 — Stop after the first accepted live canary and require official fill reconciliation
**Date:** 24 Sep 2026  
**Status:** Active

The first frozen-V3 real-money canary was submitted exactly once under the existing Phase 15 policy. Intent `live-intent-6cdfcfd28d0eb52f1ee0762bfd351409` used the frozen $5 target and was accepted by the official SDK path as external order `0x7c85e5e8753a875dfd9fec8ffd45726164863648127351b21c2a8ba1819a28de`. The executor then reported successful cancellation after the two-second TTL and the result was durably recorded as event `accepted`.

This consumes the one authorized network submission attempt. There is no retry and no second order authorization. Because an accepted order can fill before a later cancellation succeeds, the cancellation response is not treated as proof of zero fill. The project must reconcile official order/fill state and resulting exposure/P&L before any later live action is considered. This canary does not authorize automatic trading, larger sizing, V3 mutation, or V4 promotion.


## D-066 — Use an interactive Cloud Shell fast path instead of chat between canary gates
**Date:** 24 Sep 2026  
**Status:** Engineering ready; no live action performed by this decision

The observed frozen-V3 canary window is short enough that routing candidate review, arm authorization, and submission authorization through a chat round trip can consume the armable window. The operator path therefore gains a single interactive Cloud Shell helper, `scripts/deploy/phase15_v3_canary_interactive_operator_cloudshell.sh`, that composes the existing prepare-watcher, arm, executor, record, and closed-before-submission reconciliation helpers.

This is not unattended live automation. The helper requires an interactive terminal and literal local confirmations at each mutation boundary: `RECONCILE` for a stale unsubmitted intent, `START` for a new bounded prepare-only watcher, `ARM` for the exact displayed fresh $5 intent, and `SUBMIT` for the single network submission. The arm step still calls the existing `PHASE15_ACCEPT_REAL_MONEY=yes` helper and submits no order. The submission step is not reached unless the operator types `SUBMIT` after a successful arm.

The helper performs at most one executor submission invocation, writes a local per-intent attempt marker before that invocation, re-engages the Johannesburg kill switch after the call as a belt-and-suspenders action, and never retries missing, malformed, ambiguous, or unbound output. A valid structured result is passed to the existing binding-checked record helper. The frozen model, 240-second timing, 0.075 edge, $5 target, $10 ceilings, two-second TTL/cancel, one-network-attempt rule, no-second-order rule, and manual-real-money-submission policy remain unchanged.


## D-064 — Retry only transient live-liquidity misses during Phase 15 prepare
**Date:** 24 Sep 2026  
**Status:** Production rollout authorized and active

A Phase 15 frozen-V3 paper candidate may be re-evaluated while still fresh only when all prior canary risk decisions failed exclusively for transient live conditions: `liquidity_missing`, `liquidity_below_minimum`, or `api_unhealthy`. The existing append-only risk decisions remain preserved. Any eligible decision or any non-transient failure permanently removes that prediction from further prepare consideration.

This fixes a mismatch where current live liquidity could recover after the first poll but the candidate was already blacklisted by the mere existence of a prior risk decision. It does not change the frozen V3 model, calibration, 240-second timing, 0.075 minimum edge, $5 target, live limits, wallet/signer boundary, arm requirements, or one-network-submission-attempt rule.

The user explicitly authorized production rollout of main `562cb0eacae283a2916cbb9201d0bbead272d684` and restart of the prepare-only watcher. The rollout passed: run `phase15-prepare-watch-20260924T112126Z-562cb0ea` is active on `bp-recorder`, live trading remains disabled, no arm or real order has been attempted, and the one network submission attempt remains unused. Evidence: `docs/evidence/phase-15-transient-risk-retry-rollout-production-20260924.json`.

## D-065 — Reduce avoidable Phase 15 canary latency without changing frozen V3 timing
**Date:** 24 Sep 2026  
**Status:** Production active

Repeated prepared canary observations arrived with roughly 42–46 seconds remaining. This is principally a consequence of the immutable frozen-V3 timing: the selected offset is 240 seconds into a 300-second market, leaving at most 60 seconds from the scheduled prediction instant to market end. The canary must not move that prediction earlier because doing so would change the frozen strategy.

The engineering hardening therefore changes only operational polling latency. The frozen-V3 paper executor poll is reduced from 5 seconds to 1 second, and the persistent prepare-only watcher default poll is reduced from 2 seconds to 0.5 seconds. Prepared reports expose the prediction scheduled time, prediction recorded time, the paper order's semantic submission time, prepare observation time, prediction lateness, combined post-prediction-to-prepare latency, total scheduled-window consumption, and remaining time to market end. The ledger's paper-order timestamps intentionally mirror the source signal time rather than physical insert time, so the paper-executor persistence delay and watcher pickup delay are not represented as separately measured segments.

The model SHA, 240-second selected offset, 0.075 minimum edge, $5 target, paper execution assumptions, live-risk thresholds, 30-second prepare floor, 20-second arm-helper floor, 45-second maximum arm duration, kill-switch behavior, manual submission requirement, and one-network-submission-attempt rule are unchanged. This decision is engineering-only until a separately authorized production rollout validates the faster paper-executor cadence and prepare watcher under live production load. The dedicated production helper is `scripts/deploy/phase15_v3_timing_latency_rollout_cloudshell.sh`; it is exact-SHA bound, changes only the frozen-V3 paper-execution runner inside a derived runtime, restarts only `bp-v3-paper-execution.service`, preserves recorder/predictor PIDs, enforces research/live-disabled/zero-money state, validates post-restart cycle timing, and rolls back the runtime link on failure. Starting the prepare-only watcher remains a separate explicit production action.

The user explicitly authorized rollout from main `aede7c4b72614aa4f6471881ece6f2b222f27c54`, including the paper-executor timing rollout and restart of the prepare-only watcher, with no arm or live submission. Production confirmation shows runtime `/var/lib/bp/runtime/v3-paper-timing-c1e8166c463dd0111098ca29e43cd8a106d11939` already current under the 1-second paper-executor cadence. The new watcher run `phase15-prepare-watch-20260924T153537Z-aede7c4b` is active with the 0.5-second cadence, live trading disabled, no arm attempted, no live order submitted, and the single submission attempt unused. Evidence: `docs/evidence/phase-15-timing-latency-production-confirmation-20260924.json`.

