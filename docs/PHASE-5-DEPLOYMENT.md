# Phase 5 Deployment — Official Outcome/Label Pipeline

## Scope

Phase 5 creates immutable research labels from already stored Phase 4 Polymarket Gamma snapshots. It does not fetch live data and does not create features, models, backtests, predictions, orders, or trades.

The authoritative V1 target is the official resolved Polymarket outcome (`Up` or `Down`) parsed from stored Gamma snapshots. The label contract is:

- `label_version = official-outcome-v1`
- `label_source = polymarket_gamma_snapshot`
- natural key `(condition_id, label_version)`
- source evidence must have `source_observed_at >= market_end_at`
- identical reruns are no-ops
- changed semantic labels at the same natural key fail closed
- unresolved or ambiguous markets are not labeled
- conflicting resolved snapshots fail closed
- the earliest eligible agreeing resolved snapshot is canonical

## Reference-price policy

`start_reference` and `end_reference` remain NULL in V1. They represent the official first-party prices used by the market's resolution process, not an exchange proxy. Phase 5 does not substitute Coinbase, Bybit, or another exchange price for a missing official reference value.

## Migration

Phase 5 adds only `migrations/0005_market_labels.sql`. The migration creates `market_labels`, its constraints, and its start-time index. It does not alter or delete recorder/raw/history tables.

Apply on the production host with PostgreSQL's fail-fast mode:

```bash
sudo bash -lc 'docker compose --env-file /etc/bp/bp.env -f /opt/bp/docker-compose.prod.yml exec -T postgres psql -v ON_ERROR_STOP=1 -U bp -d bp < /opt/bp/migrations/0005_market_labels.sql'
```

## Offline operator command

Generate labels for a half-open market-start window:

```bash
sudo -u bp env PYTHONPATH=/opt/bp/src /opt/bp/.venv/bin/python /opt/bp/scripts/generate_labels.py \
  --start 2026-08-24T18:00:00Z \
  --end 2026-08-24T19:00:00Z \
  --env-file /etc/bp/bp.env
```

Output is JSON containing `conditions_considered`, `inserted`, `existing`, and `skipped`. The command is deliberately offline: it opens the configured database and calls the label service only.

## Production acceptance

The canonical Phase 5 acceptance window is the already accepted Phase 4 historical interval:

`2026-08-24T18:00:00Z <= market_start_at < 2026-08-24T19:00:00Z`

Run `scripts/deploy/phase5_host_acceptance.sh EXPECTED_HEAD` from an isolated candidate worktree by setting `BP_REPO` to that worktree. The script:

1. verifies the candidate HEAD exactly;
2. verifies live trading remains disabled and trade/loss limits remain zero;
3. verifies `bp-recorder` is active before the gate;
4. applies migration 0005 additively;
5. runs offline label generation twice for the acceptance window;
6. requires a non-empty resolved-label result;
7. requires the second run to insert zero labels and return existing labels;
8. verifies persisted labels have no pre-end source evidence;
9. verifies V1 official reference prices remain NULL;
10. verifies the V1 source/version/outcome contract;
11. verifies every persisted label joins exactly back to its stored Gamma snapshot provenance;
12. verifies no duplicate immutable natural keys; and
13. verifies the recorder remains active after the gate.

A valid final summary contains:

```text
VERDICT=PASS
HEAD=<exact-candidate-sha>
FIRST_RUN_NONEMPTY=1
SECOND_RUN_IDEMPOTENT=1
CONSIDERED_MATCH=1
TARGET_LABELS=<positive integer>
INVALID_LEAKAGE=0
INVALID_REFERENCE_PRICES=0
INVALID_CONTRACT=0
MISSING_PROVENANCE=0
DUPLICATE_KEYS=0
RECORDER_BEFORE=active
RECORDER_AFTER=active
LIVE_TRADING_ENABLED=false
MAX_TRADE_SIZE_USD=0
MAX_DAILY_LOSS_USD=0
```

Evidence is written under `/var/lib/bp/evidence/phase5-official-labels/<UTC timestamp>/`.

## Failure policy

Any leakage, provenance mismatch, semantic conflict, unexpected non-NULL reference price, duplicate natural key, recorder regression, or trading-safety regression is a hard failure. Do not delete or rewrite historical snapshots or labels to make the gate pass. Diagnose the source of the inconsistency and fix the code or source-data contract.

## Phase boundary

Phase 6 feature engineering remains blocked until Phase 5 host acceptance passes, durable closeout evidence is committed, the exact closeout HEAD passes CI and recorder/backfill regression gates, and the Phase 5 PR is merged to `main`.
