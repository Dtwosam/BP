# Phase 14 — V3 Gate B Successor Preregistration Implementation Plan

> **For implementation:** execute this plan test-first. Keep every step repository-only until a separately authorized production boundary exists.

**Goal:** implement `v3-gate-b-preregister-v2` before `2026-09-16T13:45:00Z`, preserving the existing V3 search contract while replacing unrecoverable historical-ID runtime exclusions with a wholly prospective structural epoch boundary.

**Architecture:** retain the existing `bp_engine.v3_research` readiness/plan pipeline and `core-v3-btc-native` feature family. Change only the frozen research-plan identity/epoch and the contamination gate. Successor readiness and planning must operate on markets satisfying `market_start_at >= epoch_start AND market_start_at < epoch_end`; pre-epoch markets are never candidates, so historical diagnosis and consumed-V2 manifests are evidence-only and no longer runtime arguments.

**Safety:** no model fitting, no label/outcome inspection, no final-holdout access or evaluation, no activation, no production mutation, no deployment/restart, no live trading, no money-limit changes.

## Frozen successor contract

- retired research plan: `v3-gate-b-preregister-v1`
- successor research plan: `v3-gate-b-preregister-v2`
- dataset: `supervised-core-v3-btc-native-v1`
- feature: `core-v3-btc-native`
- label: `official-outcome-v1`
- horizon: 300 seconds
- offsets: 60 / 120 / 180 / 240 seconds
- successor epoch: `2026-09-16T13:45:00Z` through `2026-09-19T13:45:00Z`
- eligibility: `market_start_at >= epoch_start AND market_start_at < epoch_end`
- train/validation/test/step: 24h / 6h / 6h / 6h
- exactly five ordinary folds
- final holdout: 12h
- one-market embargo
- minimum train/validation/test/final: 240 / 60 / 60 / 120
- readiness: outcome-blind
- planning: feature-only/read-only
- pre-epoch markets: structurally ineligible
- diagnosis runtime manifest: not required
- consumed-V2 runtime manifest: not required
- historical contamination evidence: preserved, including the recovered 48 consumed-V2 identities

All v1 predictor, candidate, calibration, economic, chronology, and safety rules remain unchanged.

## Task 1 — Freeze the successor config

**Files:**
- Modify: `src/bp_engine/v3_research/config.py`
- Modify: `tests/v3_research/test_config_and_exclusions.py` or add a successor-focused config test

**RED:**

Add assertions requiring:
- `research_plan_version == "v3-gate-b-preregister-v2"`
- epoch start `2026-09-16T13:45:00Z`
- epoch end `2026-09-19T13:45:00Z`
- unchanged 72h geometry, five ordinary folds, 12h final holdout
- unchanged predictor list, model candidates, calibration rules, costs, edge grid, and validation economics.

Run the targeted test and verify it fails against v1 identity/dates.

**GREEN:**

Make the smallest config change that updates only plan identity and epoch. Do not modify feature version, predictors, candidates, thresholds, costs, or geometry.

Run targeted config tests and confirm green.

## Task 2 — Make the successor contamination boundary structural

**Files:**
- Modify: `src/bp_engine/v3_research/readiness.py`
- Modify: `src/bp_engine/v3_research/plan.py`
- Modify: `tests/v3_research/test_readiness.py`
- Modify: `tests/v3_research/test_plan.py`

**RED:**

Add tests proving:
1. rows with `market_start_at < 2026-09-16T13:45:00Z` are excluded even if otherwise complete;
2. a boundary row at exactly epoch start is eligible;
3. a row at exactly epoch end is ineligible;
4. no pre-epoch `condition_id` can enter any train/validation/test/final-holdout membership;
5. successor readiness/plan hashes bind the epoch eligibility policy;
6. no historical diagnosis or consumed-V2 identity can alter successor membership by being supplied or omitted.

Tests must fail against the existing manifest-bound v1 path.

**GREEN:**

- Keep all feature queries half-open on the successor epoch.
- Remove runtime dependence on `diagnosis_exclusions` and `consumed_v2_final_holdout_exclusions` from successor readiness/planning APIs.
- Remove epoch-exclusion-list logic that exists solely to reject historical manifests containing prospective IDs.
- Do not add an empty manifest, magic sentinel, or compatibility bypass that silently preserves the v1 prerequisite.
- Bind the structural eligibility text/identity and successor config into deterministic readiness/plan hashes.
- Preserve outcome blindness and training isolation.

Run readiness/plan tests.

## Task 3 — Update the successor CLI without weakening read-only guarantees

**Files:**
- Modify: `src/bp_engine/v3_research/cli.py`
- Modify: `tests/v3_research/test_v3_preregistration_cli.py`

**RED:**

Require the successor `readiness` and `plan` commands to:
- no longer accept or require `--diagnosis-exclusions`;
- no longer accept or require `--consumed-v2-final-holdout-exclusions`;
- still require `--as-of`;
- still run PostgreSQL with `SET TRANSACTION READ ONLY`;
- keep readiness artifact-free;
- keep plan output exclusive-create/no-clobber;
- expose no model-training, holdout-evaluation, activation, deployment, or money override flags.

**GREEN:**

Remove the historical manifest arguments and forwarding only. Keep the read-only transaction and output guards unchanged.

Run CLI tests.

## Task 4 — Prove the v1 search contract did not drift

**Files:**
- Modify/add tests under `tests/v3_research/`

Add regression assertions that successor v2 exactly preserves:

### Predictors

```text
coinbase_return_from_market_start
coinbase_return_30s
coinbase_return_60s
coinbase_return_120s
bybit_spot_return_from_market_start
bybit_spot_return_30s
bybit_spot_return_60s
bybit_spot_return_120s
bybit_linear_return_from_market_start
bybit_linear_return_30s
bybit_linear_return_60s
bybit_linear_return_120s
coinbase_bybit_spot_return_spread
coinbase_bybit_spot_direction_agree
spot_linear_direction_agree
bybit_linear_vs_spot_basis
bybit_linear_funding_rate
bybit_linear_open_interest
seconds_elapsed
seconds_remaining
fraction_elapsed
```

### Candidates

```text
training_prior
coinbase_momentum_sign_diagnostic
single_feature_btc_logistic
full_v3_logistic
full_v3_xgboost
```

### Search/economic rules

- primary metric log loss;
- tie-breakers Brier/calibration/simpler model;
- XGBoost replacement requires strict improvement on both validation log loss and Brier;
- identity/Platt calibration; Platt must improve both and be nonnegative;
- fee `0.07`;
- slippage `0.01`;
- edge grid `0, .01, .02, .03, .05, .075, .10, .15`;
- explicit no-trade candidate;
- selected book age <=10s;
- minimum 8 validation trades/fold;
- >=4 nonnegative validation folds;
- positive aggregate validation P&L.

Any unrelated semantic drift fails the implementation.

## Task 5 — Update canonical source truth atomically with runtime implementation

**Files:**
- Modify: `PROJECT_STATE.json`
- Modify: `START-HERE.md`
- Modify: `docs/BUILD-ORDER.md`
- Modify: `docs/DECISION-LOG.md`
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/MASTER-SOURCE-OF-TRUTH.md`
- Preserve/reference: `docs/evidence/phase-14-v3-gate-b-successor-preregistration-20260914.json`

Record:
- v1 retired as non-executable for policy selection because exact diagnosis identities were not durably frozen;
- Sep13–16 epoch retained as engineering/coverage evidence only;
- no post-hoc diagnosis reconstruction;
- successor v2 exact identity and Sep16–19 epoch;
- structural pre-epoch ineligibility;
- recovered 48 consumed-V2 identities remain historical contamination evidence;
- no readiness before successor epoch end;
- no model fitting or final-holdout access.

Use a new decision-log entry (next available D-number; expected D-048 if still free). Do not rewrite historical v1 entries as though they never existed.

## Task 6 — Verification before merge

Run the full repository verification on the exact implementation head:

```bash
ruff check .
pytest
```

Require the dashboard test/typecheck/build job to pass.

Require the existing operational workflows to pass on the same PR head:
- CI
- Live Recorder Smoke
- Recorder Short Soak
- Historical Backfill Smoke

Review `main...head` and require only successor-contract/runtime/source-truth files expected by this plan. No deployment assets, money configuration, model artifacts, labels, or production mutation scripts should change unless separately reviewed and justified.

## Task 7 — Pre-epoch gate

Before treating v2 as frozen for prospective execution:

- confirm the successor implementation merged before `2026-09-16T13:45:00Z`;
- confirm source truth names v2 and the exact future epoch;
- confirm no readiness or plan artifact was created early;
- confirm recorder continuity remains an operational diagnostic only;
- confirm no outcome labels were inspected for successor membership or planning.

If implementation is not merged before the epoch begins, stop. Do not backdate the preregistration. Move the epoch forward under a new preregistration decision before collection begins.

## Task 8 — Post-epoch execution boundary

Only after `2026-09-19T13:45:00Z`:

1. run outcome-blind v2 successor readiness;
2. preserve its deterministic input/config hashes;
3. if and only if readiness returns ready, run the feature-only plan;
4. verify exactly five ordinary folds plus the final 12h reserve;
5. update source truth with the frozen plan artifact/hashes;
6. stop before model fitting and final-holdout access.

Final-holdout labels/evaluation, model activation, paper activation, deployment, live trading, and nonzero money all remain separate explicit authorization boundaries.