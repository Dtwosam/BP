# Phase 14 — V2 Gate B Research Package

**Date:** 9 September 2026  
**Status:** Engineering package only; Gate B not yet accepted

## Goal

Evaluate a timestamp-coherent 5-minute V2 market-price policy without reusing the V1 timestamp-incoherent probability path, without changing the frozen coverage-only freshness grid, and without exposing the final holdout during policy selection.

This package is research-only and read-only against the production database.

## Frozen inputs

The package is bound to:

- feature version: `core-v2-last-trade`;
- supervised dataset version: `supervised-core-v2-last-trade-v1`;
- label version: `official-outcome-v1`;
- coverage-only input SHA-256: `aab75574aa7faf18e65358353403e5ec1a2b89dd42424eb7b0e3329bf683b099`;
- `max_last_trade_age_seconds` candidates: exactly `[1, 2, 5, 10]`;
- explicit `no_trade`;
- selected-book freshness: unchanged 10 seconds;
- prediction offsets: exactly 60, 120, 180, and 240 seconds.

The freshness candidate set cannot be changed by Gate B.

## Source model

The first V2 source model is deliberately simple:

```text
family    = market_last_trade
predictor = pm_up_last_trade_price
```

There is no V1 `pm_up_price` input and no training-prior fallback. Missing, malformed, or policy-stale timestamped last-trade evidence is explicit no-trade.

Eligibility uses the dedicated Up-token last-trade probability and its receive/availability age. Dedicated provider/source age must also be present and finite. The selected side must have an observed non-stale best ask under the existing 10-second book contract.

## Three-stage leakage boundary

### 1. `plan` — unlabeled only

`scripts/run_v2_gate_b_research.py plan` reads only immutable `core-v2-last-trade` feature metadata.

It:

- requires exactly four V2 rows per market at 60/120/180/240 seconds;
- orders markets chronologically by `(market_start_at, condition_id)`;
- creates non-overlapping ordinary test segments;
- reserves the latest final holdout;
- inserts whole-market embargoes;
- freezes the research cost/search configuration before labels are read;
- writes an exclusive/no-clobber plan artifact.

Default unlabeled partition configuration reuses the accepted Phase 8 walk-forward geometry:

```text
train_duration            = 8 hours
validation_duration       = 2 hours
test_duration             = 2 hours
step_duration             = 2 hours
final_holdout_duration    = 2 hours
embargo_markets           = 1
min_train_markets         = 24
min_validation_markets    = 6
min_test_markets          = 6
```

The plan is constructed from feature metadata only. It requires at least three eligible ordinary folds, forbids ordinary-test reuse, and reserves the latest two-hour segment as the final holdout.

The plan also freezes these research assumptions before labels:

```text
fee_rate                  = 0.07
slippage_buffer           = 0.01
min_edge_grid             = [0.0, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15]
min_validation_trades     = 8
min_train_eligible        = 24
min_validation_eligible   = 8
```

The edge candidate geometry reuses the pre-existing Phase 9 research grid for comparability; Gate B does **not** inherit any V1-selected `min_edge`. A V2 threshold is selected afresh from validation only.

### 2. `prepare` — train/validation + ordinary test only

`prepare` verifies the feature-only plan and loads labels only for non-holdout condition IDs.

For every ordinary fold and for the final train/validation pair it searches only:

- offsets: 60/120/180/240;
- frozen last-trade max ages: 1/2/5/10 seconds;
- calibration: identity baseline plus a newly fitted V2 Platt challenger;
- the pre-labeled V2 edge grid plus `no_trade`.

Calibration fits on eligible train rows and selects on validation only.

A trade-threshold candidate is validation-eligible only when it has at least the frozen minimum number of validation trades and both total and mean realized P&L after the explicit fee/slippage assumptions are positive. Otherwise `no_trade` is valid.

The selected validation policy is then evaluated on that fold's ordinary test segment. Ordinary test markets are never used to change the selection.

The final train/validation choice is serialized into a frozen selection artifact. The final-holdout condition IDs are recorded, but their labels are not queried and no holdout metric exists in the preparation artifact.

### 3. `evaluate-holdout` — separate one-shot evidence command

The final command requires the exact plan and exact frozen selection hashes. Only then may it query labels for the final-holdout condition IDs.

The selected timing, freshness, calibration fit, and edge threshold cannot change.

The output path is exclusive/no-clobber. This is the operator evidence path for the one allowed final-holdout evaluation.

The command emits Gate B evidence but keeps:

```text
gate_b_authorized = false
automatic_promotion = false
```

Repository review and source-of-truth acceptance remain separate after holdout evidence exists.

## Fail-closed rules

Fail when:

- the preregistered coverage hash or freshness grid changes;
- feature rows are incomplete or metadata drifts within a market;
- ordinary test markets repeat;
- final holdout overlaps ordinary test;
- labeled preparation attempts to alter the feature-only research config;
- prepared data includes a final-holdout market;
- a selected V2 probability lacks dedicated source or receive age;
- the final selection artifact or plan hash changes before holdout evaluation;
- an evidence output path already exists.

Do not fail merely because the selected result is `no_trade`, eligible coverage is low, calibration remains identity, or holdout economics are negative.

## Exact-main production evidence runner

After this package is merged, use a clean local `main` checkout whose HEAD equals the merged helper SHA. The Cloud Shell wrapper refuses to run when local HEAD, remote `main`, or the configured helper SHA differ.

```bash
export PHASE14_V2_GATE_B_PROJECT='project-4397f2c0-7098-4c1c-abb'
export PHASE14_V2_GATE_B_ZONE='us-east1-c'
export PHASE14_V2_GATE_B_VM='bp-recorder'

export PHASE14_V2_GATE_B_HELPER_HEAD="$(git rev-parse HEAD)"
export PHASE14_V2_GATE_B_DEPLOYED_HEAD='895c6bd2f9409f16bf5d544b26b30e20ecbfe43a'

export PHASE14_V2_GATE_B_ENV_FILE='/etc/bp/bp.env'
export PHASE14_V2_GATE_B_STORAGE_EVIDENCE='/mnt/bp-data/evidence/phase14-partitioned-storage-rollout-20260909T070219Z.json'
export PHASE14_V2_GATE_B_STORAGE_EVIDENCE_SHA256='f33a28f5306e46c509b0000a176d226c079aa2d160d595095ca228118542ce19'

bash scripts/deploy/phase14_v2_gate_b_research_cloudshell.sh
```

The wrapper:

- verifies local HEAD, clean working tree, and remote `main` all equal `PHASE14_V2_GATE_B_HELPER_HEAD`;
- verifies the accepted production checkout and storage evidence without changing either;
- requires recorder/core services, V2 forward timer, storage timers, four recorder workers, and research/zero-money safety;
- creates an exact `git archive` of the merged helper and binds its SHA-256;
- copies only that archive to a temporary VM path and extracts it under `/var/tmp`;
- runs `plan`, `prepare`, and `evaluate-holdout` as the `bp` user using the production virtualenv plus candidate `PYTHONPATH`;
- relies on the CLI's `SET TRANSACTION READ ONLY` boundary for every database stage;
- writes no-clobber evidence under `/var/lib/bp/evidence/phase14-v2-gate-b-<timestamp>/`;
- removes the temporary candidate code/archive on exit;
- never changes `/opt/bp`, systemd state, installed packages, live/money settings, or production research registries.

A PASS from this runner is Gate B **evidence**, not Gate B authorization. The resulting summary/frozen selection/holdout evidence must still be reviewed and committed before Gate B can be accepted.

## Non-goals

This package does not:

- modify production rows or registries;
- deploy application code to production;
- select or run logistic/XGBoost challengers;
- create `live-prediction-v2`;
- activate V2 paper execution;
- alter the recorder or V2 forward collector;
- authorize Gate B automatically;
- change Phase 15 or live-trading state.

A more complex model is a later question only if the timestamp-coherent simple baseline justifies it under the project's existing complexity-stop rules.
