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

Phase 8: build the **walk-forward backtester** from the Phase 7 accepted baseline-modeling outputs. Phase 7 is production-host accepted on candidate `66bae5c71eab5e2c154cff1144ce509101d6e985` with 288 labeled 5m markets, 96 labeled 15m markets, deterministic dataset/split/model reruns, zero partition leakage, and verified external artifact hashes.

The Phase 7 validation champion for both verified horizons is the simple Polymarket `market_price` baseline; XGBoost did not satisfy the promotion rule for either horizon. Phase 8 should therefore focus on chronological walk-forward evaluation, purging/embargo, timing/regime breakdown, and realistic executable prices rather than adding model complexity.

Live prediction, paper trading, and live trading remain blocked by later phase gates. Live trading remains disabled and trade/loss limits remain zero.
