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

Phase 11: build **Dashboard V1**. Phase 10 — the money-disabled prospective live prediction engine — is production-host accepted on exact operational candidate `39101a60cdf712650f57a833849015c49da24946`.

Fresh exact-head Phase 10 gates passed on that candidate: CI #1130, Historical Backfill Smoke #439, Live Recorder Smoke #544, and Recorder Short Soak #510. Production acceptance then returned `VERDICT=PASS` and `PHASE10_HOST_ACCEPTANCE=PASS` with one new prospective 5m prediction and one new prospective 15m prediction, maximum lateness 5,563 ms, and zero pre-outcome, source-cutoff, semantic-hash, duplicate-key, prediction-mutation, evaluation-mutation, or order-side-effect violations.

Phase 10 preserves immutable `live-prediction-v1` evidence and append-only official-outcome evaluation. No official label became available inside the bounded acceptance window, so `EVALUATION_STATUS=pending` is accepted evidence rather than a reason to backfill or infer an outcome. Legacy PostgreSQL float-bound prediction hashes are verified without tolerance or mutation by reconstructing the complete original live input from frozen policy provenance and recorder evidence; overwritten compact one-second book states are recovered by replaying immutable raw events only up to the stored cutoff, then requiring exact stored `input_fingerprint` and semantic SHA-256 equality.

Phase 11 should make the system understandable without opening PostgreSQL. Show active markets, model probabilities, observed market prices, edge/action, feed health, prediction history, evaluation-backed accuracy/calibration, and current mode. Do not fabricate paper P&L: paper execution is Phase 12 and remains unbuilt.

Paper execution, live readiness, and live trading remain later phases. `LIVE_TRADING_ENABLED=false`; no order/wallet/signing path is authorized; and real-money trading still requires the later live-readiness gate plus explicit user authorization.
