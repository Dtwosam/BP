# START HERE

This folder is the handoff pack for the **BTC Polymarket Prediction Engine**.

If you are opening a new ChatGPT/Codex chat, upload/add this pack to the project and say:

> Read `docs/MASTER-SOURCE-OF-TRUTH.md`, `PROJECT_STATE.json`, `docs/BUILD-ORDER.md`, and `AGENTS.md`. Continue the project from the current phase. Do not redesign or restart it from memory unless the source of truth explicitly requires a change.

## Authority order

1. `docs/MASTER-SOURCE-OF-TRUTH.md` — canonical project definition
2. `PROJECT_STATE.json` — where the build currently stands
3. `docs/BUILD-ORDER.md` — what to build next
4. `docs/DECISION-LOG.md` — why key decisions were made
5. `docs/CHANGELOG.md` — what changed over time
6. `AGENTS.md` — working rules for AI/developers

## Current next step

Phase 9: build the **probability calibration + edge engine** from the Phase 8 accepted walk-forward outputs. Phase 8 is production-host accepted on candidate `69d3f9f8967dfcd1c1a68c640c242bd2b77cc089` with deterministic semantic reruns, zero partition overlap, zero ordinary-test reuse, zero prediction-coverage violations, and zero execution-semantic violations.

The accepted Phase 8 5m walk-forward report covered 144 ordinary OOS markets at 0.8264 accuracy, but observed selected-side best-ask execution coverage was 0.4306 and gross P&L before costs was -1.465; the untouched 5m final holdout was 0.8333 accurate with gross P&L -0.26. The 15m report reached 0.9792 ordinary OOS accuracy on 48 markets, but the untouched final holdout fell to 0.625 accuracy and gross P&L -0.47. Treat these as evidence that prediction accuracy alone is not a trade-selection rule and do not cherry-pick the high 15m ordinary-OOS headline or individual timing slices.

Phase 9 should calibrate probabilities using only permitted training/validation data, compare them to the observed executable selected-side price, explicitly account for spread/fees/slippage/uncertainty/staleness, and abstain whenever a configured minimum edge is not met. Missing or stale executable books remain no-fill/unavailable; no midpoint or synthetic fill is allowed.

Live prediction, paper trading, and live trading remain blocked by later phase gates. `LIVE_TRADING_ENABLED=false` and trade-size/daily-loss limits remain zero.
