# Phase 10 Live Prediction Engine Design

**Date:** 26 August 2026  
**Status:** Frozen for implementation  
**Phase:** 10 — live prediction engine  
**Trading mode:** research only; money disabled

## 1. Goal

Build a prospective live prediction service for the verified BTC 5m and 15m Polymarket Up/Down horizons. The service must create an immutable prediction **before the market outcome is known**, preserve enough provenance to prove what was known when the prediction was made, and append evaluation only after the official outcome becomes available.

Phase 10 is not an execution phase. A Phase 9 research decision whose semantic value is `trade=true` remains a stored research decision only. No order client, signing key, allowance, wallet, paper fill, or position-management path is added.

## 2. Accepted upstream policy sources

Production V1 is pinned to the two host-accepted Phase 9 runs:

- 5m: `phase9-300-c9f0e00eb7836af08008c66909f8f179`, semantic SHA-256 `c9f0e00eb7836af08008c66909f8f179f03089413426508469353c75bcbcae24`.
- 15m: `phase9-900-15c234f25588b23cce73a12f87a2e2ea`, semantic SHA-256 `15c234f25588b23cce73a12f87a2e2ea9087490055f203f22f183594b4bcfacd`.

The source loader exposes only the policy information frozen **before** each final holdout was evaluated:

- Phase 9 and upstream provenance/version hashes;
- horizon and source feature version;
- the final source's validation-selected prediction offset;
- the train-fitted, validation-selected calibration fit;
- the validation-selected edge policy and minimum-edge threshold;
- the explicit fee/slippage/max-spread configuration;
- the training-only fallback prior needed by the accepted `market_price` predictor.

It does **not** expose final-holdout labels, metrics, predictions, or P&L to the live decision path. Host evidence about the final holdout remains evaluation evidence only and cannot alter the deployed policy.

The implementation constants accepted in Phase 9 are `platt-or-identity-v1` and `selected-ask-edge-v1`. The Phase 9 checkpoint's friendly strings `identity-platt-v1` / `executable-edge-v1` are metadata aliases from closeout and will be corrected to the implementation constants in the next `PROJECT_STATE.json` update; no accepted hashes or behavior change.

## 3. Prospective policy interpretation

The final Phase 9 train/validation context is the only legitimate already-frozen context for future predictions. It was selected before its final holdout was scored, so it may be applied prospectively without consulting final-holdout results.

This does not mean the policy is profitable or execution-ready:

- 5m's untouched Phase 9 final holdout lost `-0.418991` after the explicit research cost assumptions. Phase 10 may record the frozen 5m research decision prospectively, but may not use it to place an order or claim promotion.
- 15m's final policy is `no_trade`; Phase 10 preserves that outcome and does not force activity.

A new Phase 9 source run or a different policy mapping requires a new live-prediction version rather than silently changing V1.

## 4. Prediction timing

For each eligible market:

`scheduled_at = market_start_at + selected_offset_seconds`

The service polls at one-second cadence and may create the prediction only inside a small fixed lateness allowance. V1 uses `max_lateness_seconds = 10`.

Rules:

1. `scheduled_at` must be strictly inside the market window.
2. `recorded_at` is the real database persistence time after all live inputs are observed and the decision is computed.
3. `recorded_at >= scheduled_at` and `recorded_at <= scheduled_at + 10s`.
4. `recorded_at < market_end_at`.
5. If the service misses the deadline, it does not backfill a live prediction. The missed market is visible as a coverage gap from the market ledger; it is never reconstructed later and presented as live.
6. Restart/rerun is idempotent. An already-recorded natural key is returned unchanged; a semantic mismatch fails closed.

The distinction between `scheduled_at` and `recorded_at` is first-class evidence and is retained permanently.

## 5. Live market source

The service consumes `polymarket_markets` populated by the already-running recorder's `MarketDiscoveryService`. It does not run a second competing Gamma discovery loop.

A market is eligible only when:

- its horizon has an explicitly supplied accepted Phase 9 policy source;
- its exact condition/slug/window/token metadata are present in `polymarket_markets`;
- its scheduled prediction time is due and within the lateness allowance;
- it is not already closed/resolved at decision time; and
- no V1 prediction already exists for that condition.

The existing recorder remains a separate systemd service and must stay active throughout Phase 10 acceptance.

## 6. Live input contract

The accepted upstream champion is `market_price`; Phase 10 therefore materializes only the inputs actually required by that frozen policy rather than pretending unused features affect the decision.

### 6.1 Market probability

`pm_up_price` must retain the Phase 6/7 source meaning: an official Polymarket CLOB `/prices-history` observation, not a midpoint or a locally synthesized price.

At the due time the prediction service performs one bounded first-party request for the Up token using the existing `PolymarketPriceHistoryClient`, with the request ending at `scheduled_at` and fidelity 1 minute. It chooses the newest returned point with `observed_at <= scheduled_at`.

For proof of availability it stores, inside the immutable prediction input provenance:

- selected price and its `observed_at`;
- actual `downloaded_at`;
- request parameters;
- canonical response SHA-256;
- source name `polymarket_clob` / dataset `prices_history`.

No point after `scheduled_at` is eligible. The request must complete before the prediction lateness deadline.

If no eligible point exists or the request fails, raw model probability falls back to the exact training-only prior from the accepted source context, matching the accepted `market_price` model behavior. The missing live market probability is explicit and makes the Phase 9 edge decision non-trading; it is never replaced by midpoint, bid/ask average, opposite-token transform, or WebSocket `last_change_price`.

### 6.2 Executable book observation

Up/Down books come from the recorder's compact `market_state_1s` store. `MarketStateReducer.last_event_at` is receipt time, so it is a usable availability timestamp. For the exact token id and `scheduled_at`, the reader requires both:

- `bucket_at <= scheduled_at`; and
- `last_event_at <= scheduled_at`.

The existing 10-second freshness rule is retained. Selected-side missing/stale book means non-executable/no-trade exactly as in Phase 9.

The immutable prediction stores both outcomes' observed best bid/ask when present, their state cutoffs/freshness, and the selected-side bid/ask/spread used by the edge decision.

### 6.3 No silent feature-version drift

The prediction records `source_feature_version=core-v1` because the accepted model was trained from that contract, plus a separate `live_input_version=phase10-live-market-input-v1` describing this minimal prospective materialization path. This makes it explicit that Phase 10 is not materializing the full historical feature vector and prevents a later model from silently consuming additional live fields under the same version.

## 7. Prediction computation

The prediction pipeline is deterministic given the frozen policy source and live input snapshot:

1. raw probability = observed Up CLOB history price, else frozen training prior;
2. apply the final train-fitted / validation-selected identity or Platt calibration fit;
3. predicted side is Up when calibrated probability `>= 0.5`, otherwise Down;
4. construct Phase 9-compatible book predictor fields from the selected exact compact states;
5. call the same `selected-ask-edge-v1` economics function with the frozen config and validation-selected policy/threshold;
6. retain the complete research decision, including `trade`, reason, ask, bid, spread, fee, slippage, raw edge, cost-adjusted edge and threshold;
7. persist before any label/outcome query occurs.

To avoid fabricating a supervised row with an unknown target, Phase 10 introduces a mapping-based edge primitive. Existing Phase 9 `edge_decision(row, ...)` becomes a backward-compatible wrapper around the same mapping primitive; Phase 9 behavior/tests must remain unchanged.

## 8. Immutable policy source loader

`bp_engine.live_prediction.policy` loads one `calibration_edge_runs` row by explicit run id and validates:

- immutable stored columns match the report;
- `calibration_version == platt-or-identity-v1`;
- `edge_policy_version == selected-ask-edge-v1`;
- source training champion/config is the accepted `market_price` model contract;
- final partition markers are train fit / validation selection / holdout evaluation;
- selected offset is within the horizon;
- edge config is finite and valid;
- calibration fit is valid and monotone;
- edge policy is either `trade_threshold` with a valid `min_edge` or `no_trade` with `min_edge=None`;
- source feature/label/training/backtest provenance chains are intact.

The fallback prior is reconstructed from the source final training condition ids using only immutable official labels. No final-holdout target is read while constructing the policy spec.

The returned `LivePolicySpec` deliberately omits final-holdout metrics, targets, P&L and predictions.

## 9. Storage model

Migration `0010_live_predictions.sql` adds two append-only tables.

### 9.1 `live_predictions`

Natural key: `(condition_id, prediction_version)` with V1 `prediction_version=live-prediction-v1`.

Core fields:

- deterministic `prediction_id` and `semantic_sha256`;
- `prediction_version`, `live_input_version`;
- condition id, slug, horizon, market start/end;
- `scheduled_at`, `recorded_at`, lateness milliseconds;
- source Phase 9 run id/semantic and upstream training/backtest provenance;
- calibration/edge/source-feature versions;
- selected offset and frozen policy/calibration/config payload + hashes;
- frozen training prior;
- raw and calibrated probability; predicted target/side;
- market probability observed flag/value/time/download time/request/response hash;
- Up/Down bid/ask plus book cutoffs/freshness;
- selected-side edge decision fields and complete decision JSON;
- input fingerprint.

The row contains **no official outcome, target, correctness, realized P&L, or post-resolution data**.

Repository semantics:

- first insert creates;
- identical natural-key rerun returns existing without rewriting `recorded_at`;
- different semantics under the same natural key raise `LivePredictionConflict`;
- updates/deletes are not part of the repository API.

### 9.2 `live_prediction_evaluations`

Natural key: `(prediction_id, label_version)`.

Fields include:

- prediction id;
- official outcome/target;
- label version/source snapshot SHA/source observed time;
- evaluation timestamp;
- correctness;
- per-row raw/calibrated log-loss and Brier terms;
- research hypothetical gross and assumed-cost P&L only when the stored decision was `trade=true`;
- evaluation semantic SHA.

It is an insert-only child record. The original `live_predictions` row is never updated when an outcome arrives.

## 10. Evaluation service

`bp_engine.live_prediction.evaluation` periodically finds predictions without an evaluation and joins only to immutable `official-outcome-v1` `market_labels` rows that already exist.

It requires:

- label condition/window agrees with the prediction;
- label source was observed at or after market end;
- prediction `recorded_at < market_end_at`;
- prediction `recorded_at < label.source_observed_at`;
- stored prediction semantic hash still verifies.

It then inserts the evaluation child row. Contradictory labels or semantic drift fail closed.

Phase 10 does not change how official labels are created. The existing Phase 5 label pipeline remains authoritative.

## 11. Service process and safety interlock

A new long-running `bp-live-predictor` process is deployed separately from `bp-recorder`.

Startup fails unless all are true:

- `mode == research`;
- `LIVE_TRADING_ENABLED=false`;
- `MAX_TRADE_SIZE_USD=0`;
- `MAX_DAILY_LOSS_USD=0`;
- exactly one accepted policy source is supplied for each enabled verified horizon;
- source-policy integrity checks pass;
- database schema is current.

The package contains no order placement imports, API keys, signing, allowance, wallet, or exchange-auth code. Network access in this service is limited to the public first-party CLOB price-history request; live book/BTC state is consumed from the recorder database.

The loop:

1. load/validate policies at startup;
2. every second load due markets from `polymarket_markets`;
3. for each due market without a prediction, observe bounded live inputs;
4. if still inside the lateness window, compute and insert the immutable prediction;
5. periodically append evaluations for labels that have appeared;
6. emit structured coverage/miss/error logs without terminating the recorder.

A single market failure is recorded/logged and does not corrupt another market. Source-policy integrity failure is process-fatal.

## 12. CLI and deployment

Provide:

- `python -m bp_engine.live_prediction` / `scripts/run_live_prediction.py` for the long-running service;
- an offline/read-only report command for proving prediction timing/evaluation integrity;
- `deploy/bp-live-predictor.service` or equivalent installation asset;
- host acceptance and Cloud Shell wrappers following the proven exact-SHA root-owned worktree -> `git archive` -> bp-owned source architecture.

No Phase 10 command accepts a trade-size, wallet, private key, or order option.

## 13. Testing

TDD must cover at least:

### Policy provenance
- exact Phase 9 source run/semantic/version chain;
- final policy extraction omits holdout metrics/targets;
- prior reconstructed only from final training ids;
- source/report mismatch fails closed;
- 15m `no_trade` preserved.

### Timing and leakage
- prediction cannot be inserted before scheduled time or after lateness deadline;
- prediction cannot be inserted at/after market end;
- compact state with `last_event_at > scheduled_at` is rejected;
- CLOB price point after scheduled time is rejected;
- slow price request crossing the lateness deadline produces no prediction;
- missed market cannot later be backfilled under V1;
- final/official label is never queried by prediction computation.

### Economics
- raw probability/fallback/calibration matches frozen source;
- selected-side ask semantics exactly match Phase 9;
- no midpoint/synthetic/opposite-side price substitution;
- Phase 9 edge wrapper remains behavior-identical after mapping refactor;
- a stored research `trade=true` creates no order side effect.

### Immutability
- identical prediction rerun does not change `recorded_at`;
- semantic conflict fails closed;
- evaluation inserts a child row and leaves prediction bytes/hash unchanged;
- evaluation rerun is idempotent and conflicting outcome fails closed.

### PostgreSQL / process
- migrations work on PostgreSQL;
- unique keys and hashes enforce invariants;
- startup safety interlock rejects paper/live/positive limits;
- service restart does not duplicate predictions;
- no trading/auth modules are imported by the live predictor.

## 14. Production acceptance

Phase 10 acceptance requires a genuine prospective host run, not historical replay presented as live evidence.

The host gate must prove:

- exact candidate SHA and accepted source Phase 9 run ids/semantics;
- live trading false and zero limits before/after;
- recorder active before/after;
- disk health ok before/after;
- predictor service runs under the unprivileged `bp` account;
- predictions exist for both enabled horizons or honest coverage gaps are reported;
- every stored prediction has `scheduled_at <= recorded_at <= scheduled_at+10s < market_end_at`;
- all stored source cutoffs/price points are `<= scheduled_at`;
- every prediction row existed before its official outcome source observation;
- prediction semantic hashes are unchanged after any later evaluation inserts;
- duplicate prediction natural keys = 0;
- evaluation/prediction outcome leakage violations = 0;
- source-policy violations = 0;
- order/trading side effects = 0.

Acceptance does **not** require a minimum number of `trade=true` decisions or positive P&L. A complete prospective `no_trade` result is valid research evidence.

The acceptance window should span enough live markets to prove at least one genuinely pre-outcome prediction per verified horizon. If official labels have not yet been materialized during the window, timing proof is still retained and the evaluation insert portion remains pending rather than inventing an outcome.

## 15. Phase boundary

Phase 10 closes when immutable prospective predictions and the append-only evaluation path are host-proven. Phase 11 may then build a dashboard from those tables.

Paper execution remains Phase 12. Live readiness remains Phase 14. Controlled real-money launch remains Phase 15 and still requires explicit user authorization. Phase 10 must not weaken any of those gates.
