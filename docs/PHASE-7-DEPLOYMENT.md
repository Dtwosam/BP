# Phase 7 — Baseline Modeling Deployment

**Phase:** 7 — leakage-safe baseline modeling and model training  
**Live trading:** Disabled

Phase 7 builds reproducible research models from immutable `core-v1` feature rows joined to `official-outcome-v1` labels. It trains the verified 5m and 15m horizons separately and does not place orders, enable paper execution, or enable live trading.

## Safety invariants

The production acceptance gate requires:

```text
LIVE_TRADING_ENABLED=false
MAX_TRADE_SIZE_USD=0
MAX_DAILY_LOSS_USD=0
RECORDER_BEFORE=active
RECORDER_AFTER=active
DISK_STATUS=ok
```

Disk health is checked twice: once before candidate installation/backfill/training and again after the research run. A non-`ok` preflight fails before expensive work begins.

The Cloud Shell helper creates a root-owned detached candidate worktree only to verify the exact commit SHA, then exports that verified commit with `git archive` into a separate `bp`-owned, non-Git source directory. Candidate package installation and Phase 7 execution use the exported source, not the Git worktree. The deployed `/opt/bp` recorder checkout is not replaced. Phase 7 also creates an isolated temporary virtual environment under `/var/tmp/bp-phase7-venv-*`; candidate ML dependencies are installed there rather than into the recorder virtual environment. Temporary candidate source, worktree, and virtual-environment paths are removed automatically.

Model binaries are external research artifacts under:

```text
/var/lib/bp/artifacts/phase7-baseline-modeling
```

Git stores code, manifests, hashes, and closeout evidence only.

## Frozen research contract

Production acceptance uses exactly:

```text
2026-08-24T00:00:00Z <= market_start_at < 2026-08-25T00:00:00Z
```

Required coverage is at least 100 unique 5m labels and at least 30 unique 15m labels. The gate expands that fixed day through the already accepted pipelines in order:

1. Phase 4 standard historical backfill;
2. Phase 5 official outcome labels;
3. Phase 6 `core-v1` features at 60-second steps;
4. Phase 7 baseline training for 300s and 900s horizons.

The standard Phase 4 backfill preserves the already accepted Bybit behavior: if Bybit historical REST returns HTTP 403, that source is recorded as restricted/unavailable and the standard run continues with verified core sources unless `--require-bybit` is explicitly used. Phase 7 does not route around that restriction and does not synthesize Bybit history.

### Additive immutable feature expansion

Phase 7 historical expansion is additive. Historical backfill can legitimately add source observations whose event/effective timestamps are before an already-frozen `feature_at`, even though those observations were not present when Phase 6 originally materialized that `core-v1` row. A normal strict recomputation of such a row can therefore produce a different input fingerprint and correctly raise `FeatureConflict`.

The Phase 7 host gate must never delete, update, or rewrite that frozen row to make the newer history win. Instead it records the number of `core-v1` rows already present in the fixed day, then runs feature generation with the explicit `--preserve-existing` expansion mode. In that mode:

- an existing `(condition_id, feature_at, feature_version)` key is located before source readers run;
- its static slug/horizon/market-window/offset metadata must still match the current target or the run fails closed;
- the existing feature payload, missing flags, source cutoffs, input fingerprint, feature hash, and original `generated_at` remain untouched;
- only previously missing natural keys are computed and inserted;
- the reported existing count must equal the pre-expansion database row count.

The default feature-generation mode remains strict and continues to recompute/compare an existing natural key, raising `FeatureConflict` on semantic drift. `--preserve-existing` exists only for controlled additive historical expansion after source backfill.

This means a small set of Phase 6 rows can retain the original missingness that was true when those immutable snapshots were frozen, while newly materialized rows can use history recovered later. Missing flags and input fingerprints preserve that provenance explicitly; Phase 7 does not backdate recovered data by rewriting old snapshots.

The model ladder is naive weighted prior, Polymarket Up-price baseline, logistic regression, and deterministic XGBoost. `xgboost-cpu` is used because production research is CPU-only.

## Leakage and split rules

The supervised dataset is `supervised-core-v1`; the split is `chronological-market-v1`. Every `condition_id` belongs wholly to one train, validation, test, or embargo partition. Assignment is chronological by market start, not by feature row. Preprocessing is fitted on training data only.

The validation champion is frozen before final test comparison. Test performance cannot rewrite the validation choice. XGBoost is marked promotion-eligible only when it beats the simple baselines under the documented validation/test rule. A failed promotion rule is a valid research result, not a reason to alter the test set.

The host gate also requires both target classes in every non-embargo partition, zero cross-partition market overlap, exact artifact SHA-256 matches, and identical semantic results on an immediate rerun.

## Local/offline training command

After the additive migration is applied, a bounded training run is:

```bash
python scripts/train_baselines.py \
  --start 2026-08-24T00:00:00Z \
  --end 2026-08-25T00:00:00Z \
  --env-file /etc/bp/bp.env \
  --output-dir /var/lib/bp/artifacts/phase7-baseline-modeling \
  --horizon-seconds 300 \
  --horizon-seconds 900 \
  --min-markets 30
```

The command reads PostgreSQL only. Training-run identity, dataset/split hashes, evaluation metrics, champion choice, model configuration, and artifact hashes are persisted in `model_training_runs`. Identical semantic reruns are existing/no-op records; conflicting reuse of a `run_id` fails closed.

## Production host acceptance

Before running the host gate, freeze a candidate SHA only after all four GitHub workflows are green on that exact SHA: CI, Historical Backfill Smoke, Live Recorder Smoke, and Recorder Short Soak.

From Google Cloud Shell, run the one-line gate from any BP checkout:

```bash
PHASE7_HEAD=<verified-sha> bash scripts/deploy/phase7_cloudshell_accept.sh
```

If the local checkout does not yet contain the helper, the same file can be fetched from the frozen candidate SHA and run with `PHASE7_HEAD` set to that SHA.

The helper verifies that `build/phase-7-baseline-modeling` still points to the expected SHA, creates a detached worktree on `bp-recorder`, verifies it, exports that exact tree into an unprivileged source directory, and invokes `phase7_host_acceptance.sh` from the exported source. Candidate execution never replaces `/opt/bp`.

## Required PASS summary

A successful gate ends with fields including:

```text
VERDICT=PASS
HEAD=<exact-candidate-sha>
FEATURE_ROWS_BEFORE=<non-negative integer>
PRESERVED_FEATURE_ROWS=<same integer as FEATURE_ROWS_BEFORE>
LABELS_5M=<integer >= 100>
LABELS_15M=<integer >= 30>
FEATURE_ROWS_5M=<positive integer>
FEATURE_ROWS_15M=<positive integer>
RUN_ID_5M=<immutable run id>
RUN_ID_15M=<immutable run id>
DATASET_SHA_5M=<sha256>
DATASET_SHA_15M=<sha256>
SPLIT_SHA_5M=<sha256>
SPLIT_SHA_15M=<sha256>
SEMANTIC_SHA_5M=<sha256>
SEMANTIC_SHA_15M=<sha256>
VALIDATION_CHAMPION_5M=<family>
VALIDATION_CHAMPION_15M=<family>
SEMANTIC_RERUN_MATCH=1
REGISTRY_SECOND_RUN_DELTA=0
PARTITION_VIOLATIONS=0
SINGLE_CLASS_PARTITIONS=0
ARTIFACT_HASH_VIOLATIONS=0
DISK_STATUS_BEFORE=ok
DISK_STATUS=ok
RECORDER_BEFORE=active
RECORDER_AFTER=active
LIVE_TRADING_ENABLED=false
MAX_TRADE_SIZE_USD=0
MAX_DAILY_LOSS_USD=0
```

`BOOSTED_PROMOTION_ELIGIBLE_5M` and `BOOSTED_PROMOTION_ELIGIBLE_15M` are evidence fields, not hard-coded PASS values. The correct result may be `false`; that means the simple baseline remains the research champion and more model complexity is not justified yet.

## Evidence

Each production gate writes durable evidence under:

```text
/var/lib/bp/evidence/phase7-baseline-modeling/<UTC timestamp>/
```

Expected files include the preflight disk-health result, candidate installation output, migration output, historical backfill output, labels, features, first and second model reports, research summary, post-run storage report, and `final-summary.txt`. The Cloud Shell wrapper also writes:

```text
/var/lib/bp/evidence/phase7-host-acceptance-latest.log
```

## Hard failures

Do not override or delete data to force a PASS. The gate fails on candidate-head drift, enabled trading or non-zero trade/loss limits, inactive recorder, non-OK disk preflight, migration/backfill/label/feature/training failure, a preserved-feature count that does not equal the pre-existing immutable row count, contradictory static metadata on a preserved key, insufficient labeled market coverage, semantic rerun differences, a new registry row on the second run, partition overlap, a single-class evaluation partition, artifact hash mismatch, or non-OK post-run disk status.

Phase 8 remains blocked until Phase 7 production acceptance passes, durable closeout evidence is committed, the closeout HEAD passes the complete exact-head workflow set, PR #6 is marked ready and merged with an expected-head guard, and `main` is verified after merge.
