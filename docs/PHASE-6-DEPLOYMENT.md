# Phase 6 Deployment — Leakage-Safe Feature Engine

## Scope

Phase 6 generates immutable `core-v1` research feature snapshots for already labeled short-duration BTC Polymarket markets. The feature engine is offline: it reads stored observations from PostgreSQL and does not fetch HTTP or WebSocket data.

Phase 6 does not train models, run backtests, create predictions, place orders, or enable trading.

## Feature contract

Each persisted row has natural key `(condition_id, feature_at, feature_version)` with `feature_version = core-v1`.

The production acceptance contract is fail closed:

- feature timestamps are strictly inside the market window;
- Polymarket prices are selected only at or before feature time;
- Coinbase candles must be fully closed by feature time;
- compact-state `bucket_at` and `last_event_at` must not exceed feature time;
- stale compact state is flagged rather than silently treated as current;
- raw trade flow uses only `(feature_at - 60s, feature_at]` and is unavailable when the window overlaps known Phase 3 raw exclusions or feed coverage is unproven;
- source-reported trade side is preserved and never inferred from price movement;
- official outcome and label/reference metadata are not feature inputs;
- `official_reference_distance` remains NULL and `official_reference_missing=true` in `core-v1`;
- identical reruns are existing/no-op; changed semantics at the same natural key fail closed.

## Migration

Phase 6 adds only `migrations/0006_market_features.sql`. It creates `market_features` and its immutable-key/check constraints. It does not alter or delete recorder, raw, compact-state, historical-backfill, or label tables.

The production acceptance helper applies migration 0006 using PostgreSQL fail-fast mode before generation.

## Offline operator command

Generate features for a half-open market-start window:

```bash
sudo -u bp env PYTHONPATH=/opt/bp/src /opt/bp/.venv/bin/python /opt/bp/scripts/generate_features.py \
  --start 2026-08-24T18:00:00Z \
  --end 2026-08-24T19:00:00Z \
  --env-file /etc/bp/bp.env \
  --step-seconds 60
```

The command reads only static target columns from `market_labels`: `condition_id`, `slug`, `horizon_seconds`, `market_start_at`, and `market_end_at`. It does not select official outcomes, reference prices, resolution metadata, or label provenance.

Output JSON contains `targets_considered`, `planned_rows`, `inserted`, `existing`, and `missing_group_counts`.

## Frozen-candidate gates

Before production acceptance, freeze one exact candidate SHA from `build/phase-6-feature-engine` and require all of these GitHub checks to pass on that SHA:

- CI;
- Historical Backfill Smoke;
- Live Recorder Smoke; and
- Recorder Short Soak.

Any commit after those checks changes the candidate and requires a fresh exact-head gate set.

## Production acceptance window

The canonical Phase 6 production window is the same accepted market-start interval used for Phase 4 historical backfill and Phase 5 labels:

`2026-08-24T18:00:00Z <= market_start_at < 2026-08-24T19:00:00Z`

The host gate runs feature generation twice with a 60-second step and requires the second run to insert zero rows.

## One-line Cloud Shell gate

Run this from a checkout of the repository in Google Cloud Shell after replacing the placeholder with the exact verified candidate SHA:

```bash
PHASE6_HEAD=<verified-sha> bash scripts/deploy/phase6_cloudshell_accept.sh
```

The helper connects to `bp-recorder` in `us-east1-c`, fetches only `build/phase-6-feature-engine`, verifies that the fetched branch head is exactly `PHASE6_HEAD`, creates a detached candidate worktree under `/var/tmp`, and invokes `phase6_host_acceptance.sh` with `BP_REPO` pointed at that worktree.

All `/opt/bp` operations occur inside the SSH payload. The deployed recorder checkout is not replaced by the candidate.

## Host acceptance gates

`scripts/deploy/phase6_host_acceptance.sh EXPECTED_HEAD` verifies:

1. the candidate worktree HEAD exactly matches `EXPECTED_HEAD`;
2. `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`;
3. `bp-recorder` is active before acceptance;
4. migration 0006 applies successfully and additively;
5. the first offline feature run accounts for every planned row;
6. the immediate second run has the same targets/planned rows and inserts zero rows;
7. persisted target and feature-row counts match the generator output;
8. no persisted source cutoff is after its `feature_at`;
9. no duplicate immutable feature natural keys exist;
10. no official-outcome, label-reference, resolution, or label-provenance key appears in feature payloads, missing flags, or source cutoffs;
11. every `core-v1` row contains a NULL `official_reference_distance` and `official_reference_missing=true`;
12. the deployed Phase 3 storage report returns `disk.status=ok`; and
13. `bp-recorder` remains active after acceptance.

Disk thresholds are not lowered to make this gate pass, and raw/archive data must not be manually deleted or truncated to create headroom.

## Required PASS summary

A valid acceptance summary includes at least:

```text
VERDICT=PASS
HEAD=<exact-candidate-sha>
TARGET_MARKETS=<positive integer>
FEATURE_ROWS=<positive integer>
SECOND_RUN_INSERTED=0
INVALID_FUTURE_CUTOFFS=0
DUPLICATE_KEYS=0
LABEL_KEY_VIOLATIONS=0
OFFICIAL_REFERENCE_VIOLATIONS=0
DISK_STATUS=ok
RECORDER_BEFORE=active
RECORDER_AFTER=active
LIVE_TRADING_ENABLED=false
MAX_TRADE_SIZE_USD=0
MAX_DAILY_LOSS_USD=0
```

Evidence is written under `/var/lib/bp/evidence/phase6-feature-engine/<UTC timestamp>/`, including:

- `migration.txt`;
- `features-first.json`;
- `features-second.json`;
- `storage-report.json`; and
- `final-summary.txt`.

The Cloud Shell wrapper also records `/var/lib/bp/evidence/phase6-host-acceptance-latest.log`.

## Failure policy

Any future-data cutoff, duplicate key, label/outcome leakage, official-reference substitution, idempotence failure, storage warning/critical state, recorder regression, candidate-head mismatch, or trading-safety regression is a hard failure.

Do not rewrite feature rows, labels, historical observations, raw data, or verified archives to force a PASS. Diagnose the source-data/code/storage condition and fix the actual cause.

## Phase boundary

Phase 7 model training remains blocked until Phase 6 host acceptance returns PASS, durable closeout evidence is committed, the closeout HEAD passes the complete exact-head GitHub gate set, PR #5 is marked ready and merged with an expected-head guard, and `main` is verified after merge.
