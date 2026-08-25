# Phase 8 Walk-Forward Backtester Deployment and Acceptance

Phase 8 is a research-only, offline walk-forward backtest of the immutable Phase 7 market-price champion. It does not enable live trading, paper execution, order submission, or any real-money path.

## Frozen production acceptance inputs

The acceptance run is intentionally pinned to the same immutable Phase 6/7 research day:

- start: `2026-08-24T00:00:00Z`
- end: `2026-08-25T00:00:00Z`
- 5-minute source training run: `phase7-300-0a822e17ceced11742bf6d3bc8214f44`
- 15-minute source training run: `phase7-900-e36d978aecc29816c5b9e2b67b30d6e2`
- backtest version: `walk-forward-v1`
- dataset version: `supervised-core-v1`
- feature version: `core-v1`
- label version: `official-outcome-v1`

Default walk-forward settings are 8 hours training, 2 hours validation, 2 hours ordinary test, 2 hour step, 2 hour final holdout, and one-market embargo. Each horizon must produce at least three ordinary walk-forward folds.

## Candidate provenance

Run only a SHA that has already passed the exact-head CI, historical backfill smoke, live-recorder smoke, and recorder short-soak workflows. The Cloud Shell helper fetches the Phase 8 branch, verifies the requested SHA, creates a detached root-owned Git worktree for provenance, exports that exact SHA with `git archive`, and extracts it into a separate bp-owned non-Git candidate source directory. The acceptance process runs from the exported source and receives the verified SHA through `BP_VERIFIED_HEAD`.

Do not bypass Git ownership checks, mutate the deployed `/opt/bp` checkout, or replace the verified export architecture with a broader trust exception.

## Production command

After an exact candidate SHA has passed all GitHub gates, download `scripts/deploy/phase8_cloudshell_accept.sh` from that exact SHA and run it from Google Cloud Shell with `PHASE8_HEAD` set to the same SHA. The helper targets project `project-4397f2c0-7098-4c1c-abb`, zone `us-east1-c`, and VM `bp-recorder` unless explicitly overridden.

The user-run host transcript is saved at:

`/var/lib/bp/evidence/phase8-host-acceptance-latest.log`

Detailed timestamped evidence is stored under:

`/var/lib/bp/evidence/phase8-walk-forward-backtester/`

Backtest report artifacts are stored under:

`/var/lib/bp/artifacts/phase8-backtests/`

## Acceptance semantics

A successful host run must end with `VERDICT=PASS` and `PHASE8_HOST_ACCEPTANCE=PASS`. The gate requires:

- exact candidate HEAD provenance;
- recorder active before and after;
- disk health `ok` before and after;
- `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0` throughout;
- migration `0008_backtest_runs.sql` applied additively;
- exact accepted Phase 7 source training runs and semantic hashes;
- exactly the 5-minute and 15-minute backtests for the frozen day;
- at least three ordinary folds per horizon and a non-empty final holdout;
- no train/validation/test or final-holdout partition overlap;
- no ordinary test-market reuse and no overlap between ordinary OOS and final holdout;
- both Up and Down classes in each evaluated train, validation, test, and final partition;
- prediction coverage at or above 90% with no substitute offset for a missing selected snapshot;
- deterministic dataset, config, plan, fold-membership, selected-offset, metric, and semantic results on rerun;
- `REGISTRY_SECOND_RUN_DELTA=0` for immutable `backtest_runs` storage;
- finite metrics and explicit market counts;
- ordinary aggregate OOS and final holdout reported separately;
- UTC-session, volatility, and execution-availability regime counts that reconcile to their parent market counts;
- execution diagnostics based only on fresh observed selected-side asks, with unavailable fills kept unavailable.

## How to interpret the result

Phase 8 is an integrity and research-quality gate, not a profitability gate. A negative gross execution P&L is a valid research result. Low execution coverage is also a valid result when it reflects honest observed-ask availability. Neither should be hidden, backfilled with midpoint prices, or converted into a pass/fail threshold merely to improve the headline.

The execution diagnostic is explicitly before-costs. It excludes fees, latency, slippage beyond the observed ask, fill probability, adverse selection, market impact, and capital constraints. Therefore it is not net profitability and must not be presented as realized or executable profit.

Likewise, accuracy is descriptive research evidence rather than a guarantee. Phase 8 should surface calibration, log loss, Brier score, uncertainty intervals, regimes, execution coverage, and gross before-costs diagnostics alongside accuracy.

## Safety boundary

Completion of Phase 8 does not authorize live trading. Live trading remains disabled until the user gives separate explicit real-money authorization in a later phase and every additional safety gate is satisfied. A `VERDICT=PASS` only means the walk-forward research implementation and host acceptance contract passed.

## Failure handling

If the host command returns a non-zero status or `PHASE8_HOST_ACCEPTANCE=FAIL`, preserve `phase8-host-acceptance-latest.log` and debug the exact failing stage. Do not lower fold counts, class requirements, coverage thresholds, immutable conflict checks, execution semantics, or safety settings to force a pass. Fix genuine defects test-first and rerun all exact-head GitHub gates before producing a new pinned Cloud Shell command.
