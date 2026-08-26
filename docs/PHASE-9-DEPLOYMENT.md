# Phase 9 Production Acceptance

Phase 9 validates the probability-calibration and executable-edge research pipeline on the production recorder host while keeping live trading disabled. The gate is reproducibility and research-integrity acceptance; it is **not a profitability claim** and it does not authorize order placement.

## Frozen production inputs

The production acceptance window is exactly:

- start: `2026-08-24T00:00:00Z`
- end: `2026-08-25T00:00:00Z`

The only accepted Phase 8 source backtests are:

- 5m: `phase8-300-efdf493067e9d56419afc4d88452bec6`
  - semantic SHA-256: `efdf493067e9d56419afc4d88452bec6effb871482664d19f109b3bbe4dd1d93`
  - accepted ordinary-fold offsets: `240,240,240,240,240,240`
- 15m: `phase8-900-64aaf2b1774ee7af37bd110b84b37ec1`
  - semantic SHA-256: `64aaf2b1774ee7af37bd110b84b37ec19f85bdc875a283986d4dba16ae921828`
  - accepted ordinary-fold offsets: `840,840,780,780,840,840`

The analysis must explicitly use `fee-rate 0.07` and `slippage-buffer 0.01`. These are frozen research cost assumptions for this acceptance run; they are not a statement that realized future execution costs will equal those values.

## Acceptance semantics

The host gate applies `0009_calibration_edge_runs.sql`, verifies the exact immutable Phase 8 source identities above, runs `scripts/run_calibration_edge.py` twice, and requires semantic equality after excluding invocation `created_at`. The second run must create zero new immutable registry rows.

Phase 9 must reuse each Phase 8 selected offset exactly. Calibration is fit on train data, calibration method and edge threshold are selected only with validation data, ordinary test markets are evaluation-only, and the final holdout is separate from ordinary OOS markets. The execution contract is observed selected-side best ask only, with missing or stale selected-side books unavailable for trading. Fees and the slippage buffer are subtracted from expected edge before an edge threshold can authorize a research trade.

A fold or final evaluation selecting `no_trade` is a **valid research result**. Negative ordinary-OOS or final-holdout realized P&L after the stated assumed costs is also a **valid research result**. Neither result makes host acceptance fail by itself. Acceptance fails only for provenance, leakage, reproducibility, execution-contract, cost-assumption, storage, recorder, or safety violations.

## Host safety

Before and after analysis the gate requires bounded `storage_maintenance.py disk-health` status `ok` and `bp-recorder` active. `/etc/bp/bp.env` must still report:

- `LIVE_TRADING_ENABLED=false`
- `MAX_TRADE_SIZE_USD=0`
- `MAX_DAILY_LOSS_USD=0`

Phase 9 does not change those settings. Live trading remains disabled.

## Evidence

Each run writes evidence beneath:

`/var/lib/bp/evidence/phase9-calibration-edge/<UTC-stamp>/`

Important files include:

- `candidate-install.txt`
- `migration.txt`
- `edge-source-contract.txt`
- `calibration-first.json`
- `calibration-second.json`
- `research-summary.txt`
- `storage-disk-health-before.json`
- `storage-disk-health-after.json`
- `final-summary.txt`

The Cloud Shell wrapper also tees the complete latest host log to:

`/var/lib/bp/evidence/phase9-host-acceptance-latest.log`

A successful host run ends with both `VERDICT=PASS` and `PHASE9_HOST_ACCEPTANCE=PASS`.

## Verified-export architecture

The Cloud Shell wrapper never runs candidate code from the host checkout. It fetches the requested Phase 9 branch, verifies that the remote branch resolves to the pinned candidate SHA, creates a root-owned detached Git worktree at that exact SHA, then uses `git archive` to export that exact tree into a bp-owned non-Git directory. The host gate receives only that exported directory through `BP_REPO` and the verified worktree SHA through `BP_VERIFIED_HEAD`.

This preserves Git provenance without changing ownership of the Git worktree or adding global Git trust exceptions.

## Operator command

Only after CI, Historical Backfill Smoke, Live Recorder Smoke, and Recorder Short Soak all pass on the exact same candidate SHA should the operator run the pinned Cloud Shell command published in the PR/ChatGPT session. The wrapper requires `PHASE9_HEAD` to equal that verified SHA.

Do not close Phase 9, enable live trading, or merge the Phase 9 PR merely because the research metrics look favorable. Host acceptance, closeout evidence, fresh closeout-head gates, and the expected-head merge guard remain mandatory.
