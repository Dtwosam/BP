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

Phase 10: build the **live prediction engine with money disabled**. Phase 9 is production-host accepted on candidate `023832db5a55c6fcb686d81bd5ab6a6185273481` with deterministic semantic reruns, zero source-offset mismatches, zero OOS/final-holdout overlap, zero executable-contract violations, zero cost-assumption violations, and zero train/validation/test selection-boundary violations.

The accepted Phase 9 5m run is `phase9-300-c9f0e00eb7836af08008c66909f8f179`. Validation selected `no_trade` in five ordinary folds and a trade threshold in one fold. Ordinary OOS produced 3 trades and +0.148014 assumed-cost P&L, but the untouched final holdout also produced 3 trades and **-0.418991** assumed-cost P&L. Do not promote the ordinary-OOS result into a profitability claim or retune against the final holdout.

The accepted 15m run is `phase9-900-15c234f25588b23cce73a12f87a2e2ea`. Validation selected `no_trade` in every ordinary fold and for the final holdout, yielding zero trades. That abstention is a valid research result and must not be overridden merely to create trading activity.

Phase 10 must run against live feeds with money disabled and persist immutable predictions before outcomes. Each prediction must preserve model version, feature version, timestamp, market, probability, predicted side, observed market bid/ask, edge, and decision. After official resolution, append outcome/evaluation without rewriting the original prediction. Acceptance requires proof that predictions existed before outcomes.

Dashboard, paper execution, live readiness, and live trading remain later phases. `LIVE_TRADING_ENABLED=false`, maximum trade size is zero, and maximum daily loss is zero. Real-money trading still requires the later live-readiness gate and explicit user authorization.
