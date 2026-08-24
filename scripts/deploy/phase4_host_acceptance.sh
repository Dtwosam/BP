#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_HEAD="${1:-}"
START="${PHASE4_ACCEPTANCE_START:-2026-08-24T18:00:00Z}"
END="${PHASE4_ACCEPTANCE_END:-2026-08-24T19:00:00Z}"
REPO=/opt/bp
ENV_FILE=/etc/bp/bp.env
COMPOSE_FILE=/opt/bp/docker-compose.prod.yml
PY=/opt/bp/.venv/bin/python
EVIDENCE_ROOT=/var/lib/bp/evidence/phase4-historical-backfill
FORENSIC_FILE=/var/lib/bp/evidence/phase3-data-integrity-incident/raw-20260822T200000Z-20260822T210000Z.jsonl.gz
FORENSIC_SHA=423f22c58ed356a207684b794f401537ba60e009f08aa89fe54fc7f58efbe9ef

if [[ -z "$EXPECTED_HEAD" ]]; then
  echo "usage: $0 EXPECTED_HEAD" >&2
  exit 2
fi

actual_head=$(git -C "$REPO" rev-parse HEAD)
if [[ "$actual_head" != "$EXPECTED_HEAD" ]]; then
  echo "expected HEAD $EXPECTED_HEAD but found $actual_head" >&2
  exit 2
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing $ENV_FILE" >&2
  exit 2
fi

read_env() {
  local key=$1
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE"
}

live_trading=$(read_env LIVE_TRADING_ENABLED)
max_trade=$(read_env MAX_TRADE_SIZE_USD)
max_loss=$(read_env MAX_DAILY_LOSS_USD)
if [[ "$live_trading" != "false" || "$max_trade" != "0" || "$max_loss" != "0" ]]; then
  echo "Phase 4 acceptance requires live trading disabled and zero trade/loss limits" >&2
  exit 3
fi

acceptance_started=$(date +%s)
install -d -o bp -g bp "$EVIDENCE_ROOT"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
run_dir="$EVIDENCE_ROOT/$stamp"
install -d -o bp -g bp "$run_dir"

recorder_before=$(systemctl is-active bp-recorder || true)
maint_timer=$(systemctl is-enabled bp-storage-maintenance.timer || true)
disk_timer=$(systemctl is-enabled bp-storage-disk-health.timer || true)
if [[ "$recorder_before" != "active" ]]; then
  echo "bp-recorder is not active before Phase 4 acceptance" >&2
  exit 4
fi
if [[ "$maint_timer" != "enabled" || "$disk_timer" != "enabled" ]]; then
  echo "Phase 3 storage timers are not enabled" >&2
  exit 4
fi

# Phase 4 migration is additive and idempotent. It does not alter raw recorder tables.
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U bp -d bp < "$REPO/migrations/0004_historical_backfill.sql" \
  > "$run_dir/migration.txt"

sudo -u bp "$PY" "$REPO/scripts/historical_backfill_smoke.py" --require-all \
  | tee "$run_dir/live-source-smoke.json"

baseline_max_id=$(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T postgres \
  psql -U bp -d bp -At -c "SELECT COALESCE(max(id), 0) FROM historical_backfill_runs;" \
  | tr -d '[:space:]')

sudo -u bp "$PY" "$REPO/scripts/historical_backfill.py" standard \
  --start "$START" --end "$END" --env-file "$ENV_FILE" \
  | tee "$run_dir/standard-first.json"

sudo -u bp "$PY" "$REPO/scripts/historical_backfill.py" standard \
  --start "$START" --end "$END" --env-file "$ENV_FILE" \
  | tee "$run_dir/standard-second.json"

read -r first_covered second_covered second_inserted < <(
  "$PY" - "$run_dir/standard-first.json" "$run_dir/standard-second.json" <<'PY'
import json
import sys
from pathlib import Path

expected = {
    "polymarket_markets",
    "polymarket_prices",
    "bybit_spot",
    "bybit_linear",
    "coinbase_spot",
}
first = json.loads(Path(sys.argv[1]).read_text())
second = json.loads(Path(sys.argv[2]).read_text())
first_covered = expected == set(first) and all(
    int(first[name]["rows_inserted"]) + int(first[name]["rows_existing"]) > 0
    and int(first[name]["chunks_fetched"]) > 0
    for name in expected
)
second_covered = expected == set(second) and all(
    int(second[name]["rows_existing"]) > 0
    and int(second[name]["chunks_fetched"]) > 0
    for name in expected
)
second_inserted = sum(int(second[name]["rows_inserted"]) for name in expected if name in second)
print(int(first_covered), int(second_covered), second_inserted)
PY
)
if [[ "$first_covered" != "1" ]]; then
  echo "first standard backfill did not return non-empty coverage for every dataset" >&2
  exit 5
fi
if [[ "$second_covered" != "1" ]]; then
  echo "second standard backfill did not return existing coverage for every dataset" >&2
  exit 5
fi
if [[ "$second_inserted" != "0" ]]; then
  echo "second standard backfill inserted $second_inserted rows; rerun is not idempotent" >&2
  exit 5
fi

new_runs=$(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T postgres \
  psql -U bp -d bp -At -c "SELECT count(*) FROM historical_backfill_runs WHERE id > $baseline_max_id;" \
  | tr -d '[:space:]')
if [[ "$new_runs" -ne 10 ]]; then
  echo "expected 10 new dataset run records across two standard runs, found $new_runs" >&2
  exit 5
fi

failed_new_runs=$(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T postgres \
  psql -U bp -d bp -At -c "
SELECT count(*)
FROM historical_backfill_runs
WHERE id > $baseline_max_id
  AND status <> 'success';" | tr -d '[:space:]')
if [[ "$failed_new_runs" != "0" ]]; then
  echo "one or more Phase 4 acceptance runs are not successful" >&2
  exit 5
fi

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T postgres \
  psql -U bp -d bp -P pager=off -c "
SELECT source, market_type, symbol, interval_seconds,
       count(*) AS rows, min(bucket_at) AS first_bucket, max(bucket_at) AS last_bucket
FROM btc_candles
WHERE bucket_at >= TIMESTAMPTZ '$START' AND bucket_at < TIMESTAMPTZ '$END'
GROUP BY source, market_type, symbol, interval_seconds
ORDER BY source, market_type, symbol, interval_seconds;

SELECT count(*) AS price_rows,
       count(DISTINCT asset_id) AS assets,
       min(observed_at) AS first_observation,
       max(observed_at) AS last_observation
FROM polymarket_price_history
WHERE observed_at >= TIMESTAMPTZ '$START' AND observed_at < TIMESTAMPTZ '$END';

SELECT id, dataset, source, status, rows_inserted, rows_existing, chunks_fetched
FROM historical_backfill_runs
WHERE id > $baseline_max_id
ORDER BY id;
" > "$run_dir/database-summary.txt"

storage_report=$(sudo -u bp "$PY" "$REPO/scripts/storage_maintenance.py" report --env-file "$ENV_FILE")
printf '%s\n' "$storage_report" > "$run_dir/storage-report.json"
disk_status=$(printf '%s' "$storage_report" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["disk"]["status"])')
disk_free=$(printf '%s' "$storage_report" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["disk"]["free_bytes"])')
if [[ "$disk_status" != "ok" ]]; then
  echo "disk status is $disk_status after Phase 4 acceptance" >&2
  exit 6
fi

recorder_after=$(systemctl is-active bp-recorder || true)
if [[ "$recorder_after" != "active" ]]; then
  echo "bp-recorder is not active after Phase 4 acceptance" >&2
  exit 6
fi

if journalctl -u bp-recorder --since "@$acceptance_started" --no-pager \
  | grep -Eiq 'traceback|fatal|panic'; then
  echo "recorder journal contains a fatal error signature during acceptance window" >&2
  exit 6
fi

forensic_sha=$(sha256sum "$FORENSIC_FILE" | awk '{print $1}')
if [[ "$forensic_sha" != "$FORENSIC_SHA" ]]; then
  echo "Phase 3 forensic evidence checksum changed" >&2
  exit 7
fi

cat > "$run_dir/final-summary.txt" <<EOF
PHASE4 HOST ACCEPTANCE
VERDICT=PASS
HEAD=$actual_head
WINDOW_START=$START
WINDOW_END=$END
LIVE_TRADING_ENABLED=$live_trading
MAX_TRADE_SIZE_USD=$max_trade
MAX_DAILY_LOSS_USD=$max_loss
RECORDER_BEFORE=$recorder_before
RECORDER_AFTER=$recorder_after
MAINTENANCE_TIMER=$maint_timer
DISK_TIMER=$disk_timer
FIRST_RUN_ALL_DATASETS_NONEMPTY=$first_covered
SECOND_RUN_ALL_DATASETS_EXISTING=$second_covered
SECOND_RUN_ROWS_INSERTED=$second_inserted
NEW_BACKFILL_RUNS=$new_runs
FAILED_NEW_RUNS=$failed_new_runs
DISK_STATUS=$disk_status
DISK_FREE_BYTES=$disk_free
FORENSIC_SHA=$forensic_sha
EVIDENCE_DIR=$run_dir
EOF

cat "$run_dir/final-summary.txt"
