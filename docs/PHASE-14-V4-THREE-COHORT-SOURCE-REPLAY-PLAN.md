# Phase 14 — three-cohort V4 source-fingerprint replay design

**Status:** Repository-only research candidate; **not run against production**. This is a follow-up to the one exact, read-only source replay of forward V4 feature `361963` at `2026-10-11T12:54:00Z`. It does not undo or replace the completed 22,604-row high-water hash/coverage audit.

## Preregistered selection — fixed before examining new replay outcomes

Script: `scripts/audit_phase14_v4_three_cohort_source_replay_readonly.py`.

Each fixed one-hour UTC window selects the *earliest* forward V4 row by `(feature_at, id)`, with no replacements if a window is empty:

| Cohort | Feature-time window, UTC | Maximum selections |
| --- | --- | --- |
| Early | 2026-09-22 12:00–13:00 | 1 |
| Middle | 2026-10-01 12:00–13:00 | 1 |
| Recent | 2026-10-10 12:00–13:00 | 1 |

The query uses the existing `market_features.feature_at` index and `LIMIT 1` per fixed window. All three feature IDs and feature timestamps are printed **before** replays, which prevents result-dependent substitutions. These dates cover deliberately separated historical periods, but are **not a random/representative statistical sample**. The selected rows may not all be from the prior high-water boundary; reported IDs must be retained separately.

Each selected row is replayed through the unchanged optimized V4 generator at its stored feature timestamp and generated time. The script compares stored-vs-rebuilt `input_fingerprint`, numerical feature map, missing flags, cutoff map, and `feature_hash`; also independently checks integrity of the stored feature hash.

## Read-only and work bounds

- Guard must confirm research mode, disabled live trading, all zero-money trade/loss/exposure limits, exact optimized V4 runtime, enabled/active V4 timer, and unchanged protected services before execution.
- The Python script uses `default_transaction_read_only=on` and `AUTOCOMMIT` (no held scan transaction); `statement_timeout=2000` ms; `lock_timeout=500` ms; `connect_timeout=3` seconds.
- Exactly three indexed `LIMIT 1` feature selections and **at most three** stored feature rebuilds, 250ms delay between replays. It checks a 45-second **soft wall budget** before/after stages; a running individual query may finish after that deadline but remains subject to its per-statement 2-second timeout.
- **Stop on first discrepancy**, missing cohort, query timeout/error, unsafe setting, or exceeded budget. Never auto-retry, replace a selected row, raise timeouts, or run the normal feature writer.
- Reports cohort, ID, feature timestamp, verdict, changed field names, and final verdict; **never** dumps raw market payloads or connection secrets. May emit exception class only.

**Success verdict:** `V4_THREE_COHORT_SOURCE_REPLAY=PASS_THREE_PREREGISTERED_EXACT_REPLAYS`, `DATABASE_READ_ONLY=PASS`, `PRODUCTION_MUTATION=false` and exit 0. If anything differs or is unavailable, treat as incomplete/inconclusive until read-only investigation. A three-cohort PASS establishes exact reproducibility for **three chosen rows under currently retained source views**, not full-history provenance, a population confidence interval, or source immutability.

## Operator invocation — only after reviewed merge and green exact-main CI

From the clean local BP `main` at the reviewed exact SHA, run this script via stdin, not by installing it on the production checkout:

```bash
set -o pipefail
gcloud compute ssh bp-recorder \
  --project=project-4397f2c0-7098-4c1c-abb \
  --zone=us-east1-c --quiet \
  --command='
set -Eeuo pipefail
RUNTIME=/var/lib/bp/runtime/v4-forward-2d82c38a526b8f1b579766100e3e832715977ac2
test "$(sudo -n readlink -f /var/lib/bp/runtime/v4-forward-current)" = "$RUNTIME"
test "$(systemctl is-active bp-v4-forward-coverage.timer)" = active
test "$(systemctl is-enabled bp-v4-forward-coverage.timer)" = enabled
for unit in bp-postgres.service bp-recorder.service bp-v3-frozen-predictor.service bp-v3-paper-execution.service; do
  test "$(systemctl is-active "$unit")" = active
done
sudo -n -u bp env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$RUNTIME/src" \
  /opt/bp/.venv/bin/python -
' < scripts/audit_phase14_v4_three_cohort_source_replay_readonly.py \
  2>&1 | tee "$HOME/bp-v4-three-cohort-replay.log"
```

Do not run the above before exact-main post-merge green checks. It is read-only verification, **not** permission to change the V4 runtime, frozen V3, database indexes or service units. User/operator output should be recorded in a separate evidence PR with the exact snapshot/retention limitations.

**Phase 14 HOLD continues.** Live trading and automatic model promotion stay disabled; no final-holdout access or policy tuning is permitted.
