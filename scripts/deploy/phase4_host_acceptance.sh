#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_HEAD="${1:-}"
START="${PHASE4_ACCEPTANCE_START:-2026-08-24T18:00:00Z}"
END="${PHASE4_ACCEPTANCE_END:-2026-08-24T19:00:00Z}"
HOST_REPO=/opt/bp
REPO="${BP_REPO:-$HOST_REPO}"
ENV_FILE=/etc/bp/bp.env
COMPOSE_FILE="$HOST_REPO/docker-compose.prod.yml"
PY="$HOST_REPO/.venv/bin/python"
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

if [[ ! -x "$PY" ]]; then
  echo "missing host virtualenv Python at $PY" >&2
  exit 2
fi

read_env() {
  local key=$1
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE"
}

candidate_python() {
  sudo -u bp env PYTHONPATH="$REPO/src" "$PY" "$@"
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

candidate_python "$REPO/scripts/historical_backfill_smoke.py" \
  | tee "$run_dir/live-source-smoke.json"

read -r smoke_core_ok smoke_bybit_ok smoke_bybit_status < <(
  "$PY" - "$run_dir/live-source-smoke.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
polymarket = payload.get("polymarket", {})
coinbase = payload.get("coinbase", {})
bybit = payload.get("bybit", {})
core_ok = (
    int(polymarket.get("up_price_points") or 0) > 0
    and int(polymarket.get("down_price_points") or 0) > 0
    and coinbase.get("status") == "ok"
    and int(coinbase.get("spot_candles") or 0) > 0
)
bybit_status = str(bybit.get("status"))
bybit_ok = bybit_status in {"ok", "environment_blocked_http_403"}
print(int(core_ok), int(bybit_ok), bybit_status)
PY
)
if [[ "$smoke_core_ok" != "1" ]]; then
  echo "live-source smoke did not verify Polymarket and Coinbase core sources" >&2
  exit 5
fi
if [[ "$smoke_bybit_ok" != "1" ]]; then
  echo "live-source smoke returned an unclassified Bybit state: $smoke_bybit_status" >&2
  exit 5
fi

baseline_max_id=$(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T postgres \
  psql -U bp -d bp -At -c "SELECT COALESCE(max(id), 0) FROM historical_backfill_runs;" \
  | tr -d '[:space:]')

candidate_python "$REPO/scripts/historical_backfill.py" standard \
  --start "$START" --end "$END" --env-file "$ENV_FILE" \
  | tee "$run_dir/standard-first.json"

candidate_python "$REPO/scripts/historical_backfill.py" standard \
  --start "$START" --end "$END" --env-file "$ENV_FILE" \
  | tee "$run_dir/standard-second.json"

read -r first_core second_core bybit_audited second_inserted bybit_unavailable < <(
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
core = {"polymarket_markets", "polymarket_prices", "coinbase_spot"}
bybit = {"bybit_spot", "bybit_linear"}
first = json.loads(Path(sys.argv[1]).read_text())
second = json.loads(Path(sys.argv[2]).read_text())

def core_first_ok() -> bool:
    return expected == set(first) and all(
        first[name]["status"] == "success"
        and int(first[name]["rows_inserted"]) + int(first[name]["rows_existing"]) > 0
        and int(first[name]["chunks_fetched"]) > 0
        for name in core
    )


def core_second_ok() -> bool:
    return expected == set(second) and all(
        second[name]["status"] == "success"
        and int(second[name]["rows_existing"]) > 0
        and int(second[name]["chunks_fetched"]) > 0
        for name in core
    )


def bybit_item_ok(item: dict, *, rerun: bool) -> bool:
    if item.get("status") == "unavailable":
        return (
            "HTTP 403" in str(item.get("reason"))
            and int(item.get("rows_inserted", 0)) == 0
            and int(item.get("rows_existing", 0)) == 0
            and int(item.get("chunks_fetched", 0)) == 0
        )
    if item.get("status") != "success":
        return False
    if int(item.get("chunks_fetched", 0)) <= 0:
        return False
    if rerun:
        return int(item.get("rows_existing", 0)) > 0
    return int(item.get("rows_inserted", 0)) + int(item.get("rows_existing", 0)) > 0

bybit_ok = expected == set(first) == set(second) and all(
    bybit_item_ok(first[name], rerun=False) and bybit_item_ok(second[name], rerun=True)
    for name in bybit
)
second_inserted = sum(int(second[name]["rows_inserted"]) for name in expected)
unavailable = sum(
    int(result[name].get("status") == "unavailable")
    for result in (first, second)
    for name in bybit
)
print(int(core_first_ok()), int(core_second_ok()), int(bybit_ok), second_inserted, unavailable)
PY
)
if [[ "$first_core" != "1" ]]; then
  echo "first standard backfill did not return non-empty core-source coverage" >&2
  exit 5
fi
if [[ "$second_core" != "1" ]]; then
  echo "second standard backfill did not return existing core-source coverage" >&2
  exit 5
fi
if [[ "$bybit_audited" != "1" ]]; then
  echo "Bybit results were neither verified nor explicitly audited as HTTP 403 unavailable" >&2
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

invalid_new_runs=$(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T postgres \
  psql -U bp -d bp -At -c "
SELECT count(*)
FROM historical_backfill_runs
WHERE id > $baseline_max_id
  AND status NOT IN ('success', 'unavailable');" | tr -d '[:space:]')
if [[ "$invalid_new_runs" != "0" ]]; then
  echo "one or more Phase 4 acceptance runs have an invalid terminal status" >&2
  exit 5
fi

invalid_unavailable=$(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T postgres \
  psql -U bp -d bp -At -c "
SELECT count(*)
FROM historical_backfill_runs
WHERE id > $baseline_max_id
  AND status = 'unavailable'
  AND (
    source <> 'bybit'
    OR dataset NOT IN ('bybit_spot', 'bybit_linear')
    OR error NOT LIKE '%HTTP 403%'
    OR rows_inserted <> 0
    OR rows_existing <> 0
    OR chunks_fetched <> 0
  );" | tr -d '[:space:]')
if [[ "$invalid_unavailable" != "0" ]]; then
  echo "unavailable provenance is not limited to audited Bybit HTTP 403 runs" >&2
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

SELECT id, dataset, source, status, rows_inserted, rows_existing, chunks_fetched, error
FROM historical_backfill_runs
WHERE id > $baseline_max_id
ORDER BY id;
" > "$run_dir/database-summary.txt"

# Keep the Phase 3 storage-health check on the deployed recorder checkout. The acceptance
# worktree must not replace the running recorder's code or service configuration.
storage_report=$(sudo -u bp "$PY" "$HOST_REPO/scripts/storage_maintenance.py" report --env-file "$ENV_FILE")
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
CANDIDATE_REPO=$REPO
DEPLOYED_RECORDER_REPO=$HOST_REPO
WINDOW_START=$START
WINDOW_END=$END
LIVE_TRADING_ENABLED=$live_trading
MAX_TRADE_SIZE_USD=$max_trade
MAX_DAILY_LOSS_USD=$max_loss
RECORDER_BEFORE=$recorder_before
RECORDER_AFTER=$recorder_after
MAINTENANCE_TIMER=$maint_timer
DISK_TIMER=$disk_timer
SMOKE_CORE_OK=$smoke_core_ok
SMOKE_BYBIT_STATUS=$smoke_bybit_status
FIRST_RUN_CORE_NONEMPTY=$first_core
SECOND_RUN_CORE_EXISTING=$second_core
BYBIT_RESULTS_AUDITED=$bybit_audited
BYBIT_UNAVAILABLE_RUNS=$bybit_unavailable
SECOND_RUN_ROWS_INSERTED=$second_inserted
NEW_BACKFILL_RUNS=$new_runs
INVALID_NEW_RUNS=$invalid_new_runs
INVALID_UNAVAILABLE_RUNS=$invalid_unavailable
DISK_STATUS=$disk_status
DISK_FREE_BYTES=$disk_free
FORENSIC_SHA=$forensic_sha
EVIDENCE_DIR=$run_dir
EOF

cat "$run_dir/final-summary.txt"
