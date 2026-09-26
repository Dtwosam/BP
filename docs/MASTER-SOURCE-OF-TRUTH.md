# BTC Polymarket Prediction Engine — Master Source of Truth

**Status:** Active
**Version:** 0.1.0
**Frozen on:** 20 August 2026
**Authority:** This file is the canonical source of truth for the project.

---

## 0. How to use this document

This file is the final authority when a future chat, developer, model, note, or old message disagrees with the project.

Rules:

1. Read this file before changing architecture, scope, metrics, trading logic, data sources, model targets, or phase gates.
2. Do not silently overwrite a decision in this file.
3. If a decision changes, update:
   - this file,
   - `docs/DECISION-LOG.md`,
   - `docs/CHANGELOG.md`,
   - `PROJECT_STATE.json`.
4. The desired 80% accuracy is a research goal, **not an assumed capability**.
5. No real-money trading is allowed until the live-trading gate in this document is passed.
6. Never paste a wallet private key, seed phrase, API secret, or server secret into ChatGPT or a source file.
7. Market rules and APIs can change. Re-verify current Polymarket rules and API behavior before live trading.

---

# 1. Project mission

Build an end-to-end system that estimates whether Bitcoin will resolve **Up or Down** in short-duration Polymarket BTC prediction markets, initially focused on 5-minute and 15-minute markets, with 10-minute support if/when such a market is available.

The long-term system should:

1. collect BTC and Polymarket market data continuously;
2. build a clean historical training dataset;
3. train separate predictive models by horizon;
4. produce calibrated Up/Down probabilities;
5. compare the model probability with the tradable Polymarket price;
6. paper-trade first;
7. measure real out-of-sample performance;
8. trade automatically only after strict validation and risk gates are passed.

The end goal is **profitable, well-measured decision-making**, not merely a high headline accuracy number.

---

# 2. Current market definition

## 2.1 Verified Polymarket horizons

As of the source-of-truth freeze date:

- **5-minute BTC Up/Down markets:** verified.
- **15-minute BTC Up/Down markets:** verified.
- **10-minute BTC Up/Down markets:** desired by the project, but not verified as a current recurring Polymarket market.

Therefore, horizon support must be **configurable**, not hard-coded.

Initial configuration:

```yaml
active_horizons:
  - 5m
  - 15m

optional_horizons:
  - 10m
```

If Polymarket offers other recurring BTC Up/Down durations later, they may be added without redesigning the engine.

## 2.2 Verified resolution rule

Current verified BTC 5m/15m Polymarket examples resolve using the **Chainlink BTC/USD data stream**.

Current rule:

- **Up** wins if the BTC price at the end of the stated interval is **greater than or equal to** the BTC price at the beginning.
- Otherwise, **Down** wins.

This means the prediction target is **not simply “will Binance BTC go up?”**.

The target is:

> What is the probability that the relevant Polymarket BTC Up/Down market resolves Up according to its official resolution rule?

The market's own rule is always authoritative. If Polymarket changes the resolution source or wording, the system must detect/review the change before trading.

---

# 3. Core principle

The engine must distinguish between:

1. **Direction probability** — our estimate that Up or Down will win.
2. **Market price** — the cost of buying that outcome on Polymarket.
3. **Expected value** — whether the model's estimated probability is sufficiently better than the executable market price after fees, spread, slippage, uncertainty, and execution risk.

Example:

- model says Up = 80%;
- executable Up ask = $0.60;
- this may contain positive expected value.

But:

- model says Up = 80%;
- executable Up ask = $0.90;
- this can be a bad trade despite a strong directional prediction.

Therefore:

**Accuracy alone does not decide trades.**

---

# 4. Success criteria

## 4.1 Research target

The user would like the system to reach approximately **80% prediction accuracy**.

This is a target to investigate, not a promise.

We must report:

- accuracy across all predictions;
- accuracy by horizon;
- accuracy by confidence bucket;
- coverage (% of observations where a trade-quality signal exists);
- calibration;
- profitability;
- expected value;
- realised P&L;
- maximum drawdown;
- losing streaks;
- performance by market regime.

A useful outcome may be:

- lower overall accuracy;
- but 75–80%+ accuracy on a smaller, genuinely high-confidence subset;
- with positive net expected value and live paper-trading profitability.

## 4.2 Minimum proof standard

A model is **not considered proven** because it reaches 80% in one backtest.

It must survive:

1. data-quality validation;
2. leakage checks;
3. time-ordered holdout testing;
4. walk-forward testing;
5. multiple market regimes;
6. realistic execution simulation;
7. live paper trading with predictions timestamped before outcomes are known.

## 4.3 Live-trading gate

Real-money automation remains disabled until all of the following are true:

- historical pipeline is reproducible;
- no known target leakage;
- backtests use time-ordered splits;
- walk-forward results are stable enough to justify continuation;
- live paper predictions have a sufficiently large sample;
- profitability remains positive after realistic costs;
- confidence is calibrated;
- risk limits and kill switch are tested;
- order execution and reconciliation are tested;
- current Polymarket geographic eligibility/compliance is checked;
- the user explicitly authorizes the transition to real-money trading.

There is no fixed “magic” sample count in this version of the spec. The analysis must include uncertainty/confidence intervals rather than relying on a round number alone.

### 4.3.1 Frozen V3 controlled-live authorization — 23 September 2026

The user has explicitly authorized **pursuing** a controlled real-money transition for the exact frozen V3 while V4 research collection continues in parallel. This satisfies only the explicit-human-authorization requirement; it does not itself enable trading or override any other live-gate requirement.

Before any real order is allowed, a fresh V3-specific reassessment must isolate `v3-frozen-paper-v1` / `paper-execution-v3-frozen-v1` and report paper-sample uncertainty, after-cost profitability robustness including largest-winner sensitivity, calibration, drawdown/losing streak, and reconciliation. Current direct Polymarket geographic eligibility must also pass. Any failed or insufficient row keeps Phase 15 blocked. No proxy/VPN/restriction bypass is permitted.

Until the complete gate passes, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0` remain mandatory. The V3 model artifact, calibration, 240-second timing, `min_edge=0.075`, and paper sizing remain frozen; this authorization is not permission to tune them from paper results.

### 4.3.2 Frozen V3 read-only live-gate reassessment result — 23 September 2026

The one-shot production reassessment completed read-only on exact main `ba98b3871e03895d04bb2b06d4be5350f6c17491`. Sanitized evidence is `docs/evidence/phase-14-v3-live-gate-reassessment-production-20260923.json`.

The frozen V3 paper sample contained 76 settled trades (47 wins, 29 losses), realized after-cost P&L `682.252111761097` USD, mean P&L `8.97700147054075` USD, profit factor `6.262572772140266`, maximum drawdown `27.974134608836` USD, maximum losing streak 4, and P&L `450.802786408456` USD after removing the largest winner. The deterministic 10,000-resample bootstrap 95% interval for mean realized P&L was `[1.6407501892525114, 18.945890261783955]`, entirely above zero. Under the already-approved profitability rule, `positive_after_cost_profitability=pass`. Reconciliation remained `OK` with zero violations, so `order_execution_and_reconciliation_tested=pass`. Explicit user live authorization is `pass`.

Across all 519 evaluated frozen-V3 predictions, raw/calibrated Brier mean was `0.0999937890122578` and raw/calibrated log-loss mean was `0.3173973846097945`. No approved numerical prospective calibration acceptance threshold exists, so `calibration_acceptable=insufficient_evidence`. Section 4.3 still defines no fixed prospective sample-size threshold, so `sufficiently_large_live_paper_sample_with_uncertainty=insufficient_evidence`. `walk_forward_results_stable_enough` also remains `insufficient_evidence`.

The direct official Polymarket geoblock request from the production VM returned `blocked=true`, `country=US`, `region=SC` at `2026-09-23T09:43:05.145648Z`. Therefore `geographic_compliance_eligible=fail`. The overall Master live gate remains `fail`; Phase 15 remains blocked; live trading remains disabled; real-money limits remain zero. This result must not be bypassed with VPNs, proxies, tunnels, or relocation tricks. Preserve the one-shot evidence and do not rerun the reassessment absent a separately versioned reason.


### 4.3.3 Accelerated frozen-V3 canary-readiness mapping — 23 September 2026

The user has requested the shortest compliant path to a controlled live canary. The separately versioned `phase15-v3-canary-readiness-v1` audit may reassess only the three remaining statistical rows; it cannot enable trading.

The sample-sufficiency rule reuses the Phase 13 principle that there is no magic count: prospective evidence is sufficient only when the deterministic 95% lower confidence bound for mean realized after-cost P&L is strictly above zero. No post-hoc round-number minimum is introduced.

Walk-forward stability is mapped from three already-separated layers: the pre-registered five-fold V3 ordinary validation economics gate, the untouched V3 final holdout, and the prospective paper uncertainty interval. The frozen V3 implementation forces `no_trade` whenever the ordinary validation economics gate fails; because the immutable selected policy is `trade_threshold` at `min_edge=0.075`, that pre-registered ordinary economics gate necessarily passed.

For calibration, the new reliability acceptance rule is frozen **before** calibration intercept/slope diagnostics are read. Prospective calibrated Brier and log loss must be no worse than the frozen V3 final-holdout values, and a deterministic bootstrap 95% interval for calibration intercept must contain 0 while the corresponding calibration-slope interval must contain 1. Ten-bin ECE is descriptive only. If this new audit fails, the rule may not be weakened after seeing the result.

Geography remains independent and mandatory. A statistical PASS cannot override a blocked physical location or blocked execution host. Before any real order, the user's ordinary physical-network check with VPN/proxy disabled and the eventual execution host's direct official Polymarket geoblock check must both be unblocked. Infrastructure must not be used to disguise a restricted user location.

### 4.3.4 Second frozen-V3 Telegram canary authorization — 25 September 2026

After the first real-money canary was officially reconciled at zero fill, the user explicitly requested that V3 continue to live trading. That authorization is accepted only in the following narrow form: **exactly one additional frozen-V3 canary may be submitted through the private Telegram approval path**. It is not authorization for unrestricted autonomous trading.

The second-canary policy preserves the already-frozen strategy and risk envelope: prediction version `v3-frozen-paper-v1`, execution version `paper-execution-v3-frozen-v1`, 240-second decision offset, `min_edge=0.075`, **$5 target notional**, **$10 maximum trade**, **$10 maximum total exposure**, **$10 daily-loss stop**, one consecutive-loss stop, one network submission attempt for this authorization, and the existing 2-second limit-order TTL/cancel attempt. No stake growth, V3 refit/recalibration/threshold change, side or regime filter, V4 mutation, or broad autonomous rollout is authorized.

Global and Phase-15 `LIVE_TRADING_ENABLED` remain false. The permitted live path is instead bound to the exact short-lived order through explicit source-truth authorization fields: `second_order_authorized=true`, `automated_real_money_submission=true`, `manual_real_money_submission_required=false`, `telegram_one_tap_submission_authorized=true`, `telegram_persistent_execution_transport_authorized=true`, and `telegram_pubsub_transport_authorized=true`. Those fields authorize only the reviewed Telegram pipeline; unrelated execution paths remain blocked.

Before the order can reach the existing Johannesburg executor, all of the following must pass for the same exact intent/request: fresh private Telegram APPROVE, origin attestation, signed source-truth authorization, authenticated transport verification, one-shot claim, pre-execution authorization, dispatch-ticket/claim verification, immutable execution-package verification, and the read-only privileged-handoff contract bound to the exact expected executor SHA-256. Immediately before submission, the Johannesburg path must independently recheck direct official geoblock eligibility, official account state, collateral, zero open orders, activation expiry, request/executor binding, and kill-switch semantics.

Missing, stale, malformed, mismatched, expired, rejected, or ambiguous state fails closed and must not be retried automatically. Official order/fill reconciliation is mandatory after this second canary before any third live action can be authorized.

### 4.3.5 Telegram transport staged, not activated — 26 September 2026

The previous inactive transport stage was rolled back after an activation attempt failed closed with `REASON=executor_transport_service_activation_failed`. That attempt submitted no real order, left global and Phase-15 live trading disabled, and re-engaged the Johannesburg kill switch. Production diagnostics identified two staging/runtime defects (service-user venv access and an absent optional `/etc/bp` namespace path), followed by a recovery-preflight defect caused by the empty recorder transport-config directory left by fail-closed cleanup. PRs #298 and #299 corrected those defects without changing wallet, arming, live-trading, IAM-scope, or order-submission semantics.

The corrected deterministic Phase-15 Telegram transport release is now staged successfully on both production hosts at exact release head `def15988b87fe90436fa9ee1458e640ba67bb1af`, release SHA-256 `6b35d1ce72e581dbe75531170cf9b078ed71e23416431ea8f0f965822a8d9d24`, and stage ID `phase15-telegram-stage-c1eb8ce238d6cd96d2edaec6`. Durable sanitized evidence is `docs/evidence/phase-15-v3-telegram-transport-restage-readiness-production-20260926.json`.

The stage-only installer and the independent read-only stage-status verifier both returned PASS with zero blockers. The recorder publisher plus all four Johannesburg transport/execution-authorization units are installed but inactive and disabled. No runtime environment files or key files are present, the stage binding is current, live trading remains disabled, and no real order was submitted.

The independent Pub/Sub readiness verifier also returned PASS with zero blockers. The dedicated publisher and subscriber service accounts have no project-level workload roles; the existing topic/subscription and their resource-scoped publisher/subscriber IAM bindings are ready. Those Pub/Sub resources survived the earlier failed activation attempt; the corrected restage itself did not mutate Pub/Sub or IAM.

Transport activation remains authorized but not activated. Activation itself must submit no order and must preserve the executor's fail-closed safe-idle state. Exactly one fresh private Telegram `APPROVE` is still required after successful activation before the authorized second frozen-V3 canary may invoke the executor, and official reconciliation remains mandatory before any third live action.

