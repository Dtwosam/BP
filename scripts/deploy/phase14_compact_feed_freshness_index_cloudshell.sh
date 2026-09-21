#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_COMPACT_FEED_FRESHNESS_INDEX_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_COMPACT_FEED_FRESHNESS_INDEX_ZONE:-us-east1-c}"
VM="${PHASE14_COMPACT_FEED_FRESHNESS_INDEX_VM:-bp-recorder}"
HELPER_HEAD="${PHASE14_COMPACT_FEED_FRESHNESS_INDEX_HELPER_HEAD:-}"
DEPLOYED_HEAD="${PHASE14_COMPACT_FEED_FRESHNESS_INDEX_DEPLOYED_HEAD:-7c3af78da1922a0e5187c24b799951130cc98887}"
APPROVAL="${PHASE14_COMPACT_FEED_FRESHNESS_INDEX_APPROVAL:-}"
INDEX_NAME='ix_market_state_1s_feed_last_event'

fail_local() {
  echo "PHASE14_COMPACT_FEED_FRESHNESS_INDEX_GATE=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$HELPER_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail_local "helper_head_invalid"
[[ "$DEPLOYED_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail_local "deployed_head_invalid"

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
[[ -n "$ROOT" ]] || fail_local "local_repository_missing"
cd "$ROOT"
[[ "$(git rev-parse HEAD)" == "$HELPER_HEAD" ]] || fail_local "local_helper_head_mismatch"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || fail_local "local_working_tree_dirty"
REMOTE_MAIN=$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')
[[ "$REMOTE_MAIN" == "$HELPER_HEAD" ]] || fail_local "remote_main_changed"

EXPECTED_APPROVAL="I_APPROVE_PHASE14_COMPACT_FEED_FRESHNESS_INDEX:${HELPER_HEAD}:${DEPLOYED_HEAD}"
[[ "$APPROVAL" == "$EXPECTED_APPROVAL" ]] || fail_local "production_approval_mismatch"

command -v gcloud >/dev/null 2>&1 || fail_local "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . || fail_local "gcloud_auth_missing"
gcloud config set project "$PROJECT" >/dev/null

printf -v DEPLOYED_HEAD_Q '%q' "$DEPLOYED_HEAD"
printf -v INDEX_NAME_Q '%q' "$INDEX_NAME"

REMOTE_SCRIPT=$(cat <<'REMOTE'
set -Eeuo pipefail

DEPLOYED_HEAD="${PHASE14_COMPACT_FEED_FRESHNESS_INDEX_DEPLOYED_HEAD:?}"
INDEX_NAME="${PHASE14_COMPACT_FEED_FRESHNESS_INDEX_NAME:?}"
REPO=/opt/bp
ENV_FILE=/etc/bp/bp.env
SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env
EVIDENCE_DIR=/var/lib/bp/evidence

RECORDER_UNIT=bp-recorder.service
V3_PREDICTOR=bp-v3-frozen-predictor.service
V3_EXECUTION=bp-v3-paper-execution.service
MAINTENANCE_SERVICE=bp-storage-maintenance.service
MAINTENANCE_TIMER=bp-storage-maintenance.timer
DISK_HEALTH_SERVICE=bp-storage-disk-health.service
DISK_HEALTH_TIMER=bp-storage-disk-health.timer
V2_TIMER=bp-v2-forward-coverage.timer
V4_TIMER=bp-v4-forward-coverage.timer

fail() {
  echo "PHASE14_COMPACT_FEED_FRESHNESS_INDEX_GATE=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

read_env() {
  local path=$1 key=$2
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$path"
}

require_research_zero_money() {
  local path mode live trade loss
  for path in "$ENV_FILE" "$SAFETY_FILE"; do
    [[ -r "$path" ]] || fail "safety_file_missing:$path"
    mode=$(read_env "$path" MODE)
    live=$(read_env "$path" LIVE_TRADING_ENABLED)
    trade=$(read_env "$path" MAX_TRADE_SIZE_USD)
    loss=$(read_env "$path" MAX_DAILY_LOSS_USD)
    [[ "$mode" == "research" ]] || fail "mode_not_research:$path"
    [[ "$live" == "false" ]] || fail "live_trading_enabled:$path"
    [[ "$trade" == "0" ]] || fail "max_trade_size_nonzero:$path"
    [[ "$loss" == "0" ]] || fail "max_daily_loss_nonzero:$path"
  done
}

require_automatic_promotion_false() {
  "$REPO/.venv/bin/python" - "$REPO/PROJECT_STATE.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
values = []

def walk(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "automatic_promotion":
                values.append(item)
            walk(item)
    elif isinstance(value, list):
        for item in value:
            walk(item)

walk(payload)
if not values or any(value is not False for value in values):
    raise SystemExit("automatic_promotion must remain false")
PY
}

require_timer_active_enabled() {
  local timer=$1
  systemctl is-enabled --quiet "$timer" || fail "timer_not_enabled:$timer"
  systemctl is-active --quiet "$timer" || fail "timer_not_active:$timer"
}

wait_for_oneshot_idle_success() {
  local service=$1 timeout_seconds=$2 waited=0 active_state
  while true; do
    active_state=$(systemctl show -p ActiveState --value "$service")
    case "$active_state" in
      inactive) break ;;
      active|activating)
        (( waited < timeout_seconds )) || fail "oneshot_wait_timeout:$service"
        sleep 5
        waited=$((waited + 5))
        ;;
      *) fail "oneshot_unexpected_state:$service:$active_state" ;;
    esac
  done
  [[ "$(systemctl show -p Result --value "$service")" == "success" ]] || fail "oneshot_last_result_not_success:$service"
}

require_next_hour_headroom() {
  local minimum_seconds=$1 now_epoch next_epoch remaining
  now_epoch=$(date -u +%s)
  next_epoch=$(( (now_epoch / 3600 + 1) * 3600 ))
  remaining=$(( next_epoch - now_epoch ))
  (( remaining >= minimum_seconds )) || fail "insufficient_next_hour_headroom:$remaining"
  echo "NEXT_HOUR_HEADROOM_SECONDS=$remaining"
}

[[ -d "$REPO/.git" ]] || fail "deployed_repo_missing"
[[ -x "$REPO/.venv/bin/python" ]] || fail "python_runtime_missing"
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]] || fail "unexpected_deployed_head"

require_research_zero_money
require_automatic_promotion_false

for unit in "$RECORDER_UNIT" "$V3_PREDICTOR" "$V3_EXECUTION"; do
  systemctl is-active --quiet "$unit" && fail "fail_closed_unit_active:$unit" || true
done

require_timer_active_enabled "$MAINTENANCE_TIMER"
require_timer_active_enabled "$DISK_HEALTH_TIMER"
require_timer_active_enabled "$V2_TIMER"
require_timer_active_enabled "$V4_TIMER"
wait_for_oneshot_idle_success "$MAINTENANCE_SERVICE" 3600
wait_for_oneshot_idle_success "$DISK_HEALTH_SERVICE" 30
require_next_hour_headroom 1200

install -d -o bp -g bp -m 0750 "$EVIDENCE_DIR"
EVIDENCE_TMP=$(mktemp /var/tmp/bp-phase14-compact-feed-index.XXXXXX.json)

sudo -u bp "$REPO/.venv/bin/python" - "$ENV_FILE" "$INDEX_NAME" > "$EVIDENCE_TMP" <<'PY'
import json
import sys
import time
from datetime import UTC, datetime

from sqlalchemy import create_engine, text
from bp_engine.config import Settings

env_file, index_name = sys.argv[1:]
settings = Settings(_env_file=env_file)
engine = create_engine(settings.database_url)
feeds = (
    ("bybit", "spot"),
    ("bybit", "linear"),
    ("coinbase", "spot"),
    ("polymarket", "market"),
)

with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
    existing = connection.execute(
        text("""
            SELECT indexrelid::regclass::text, indisvalid, indisready
            FROM pg_index
            WHERE indexrelid = to_regclass(:index_name)
        """),
        {"index_name": index_name},
    ).one_or_none()
    if existing is not None and (not bool(existing[1]) or not bool(existing[2])):
        connection.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}"))

    connection.execute(text("SET lock_timeout = '5s'"))
    connection.execute(text("SET statement_timeout = '15min'"))
    connection.execute(text(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name} ON market_state_1s (source, stream, last_event_at DESC)"))

    row = connection.execute(
        text("""
            SELECT
                pg_get_indexdef(indexrelid) AS indexdef,
                indisvalid,
                indisready
            FROM pg_index
            WHERE indexrelid = to_regclass(:index_name)
        """),
        {"index_name": index_name},
    ).mappings().one_or_none()
    if row is None:
        raise SystemExit("compact-feed freshness index missing after create")
    if row["indisvalid"] is not True or row["indisready"] is not True:
        raise SystemExit("compact-feed freshness index is not valid and ready")
    indexdef = str(row["indexdef"])
    required_fragments = (
        "market_state_1s",
        "(source, stream, last_event_at DESC)",
    )
    if any(fragment not in indexdef for fragment in required_fragments):
        raise SystemExit(f"compact-feed freshness index definition drifted: {indexdef}")

    connection.execute(text("SET statement_timeout = '10s'"))
    latest = {}
    plans = {}
    elapsed_ms = {}
    for source, stream in feeds:
        params = {"source": source, "stream": stream}
        plan_lines = connection.execute(
            text("""
                EXPLAIN
                SELECT last_event_at
                FROM market_state_1s
                WHERE source = :source
                  AND stream = :stream
                ORDER BY last_event_at DESC
                LIMIT 1
            """),
            params,
        ).scalars().all()
        plan = "\n".join(str(line) for line in plan_lines)
        if index_name not in plan:
            raise SystemExit(f"planner did not select {index_name} for {source}/{stream}: {plan}")
        plans[f"{source}/{stream}"] = plan
        started = time.monotonic()
        value = connection.execute(
            text("""
                SELECT last_event_at
                FROM market_state_1s
                WHERE source = :source
                  AND stream = :stream
                ORDER BY last_event_at DESC
                LIMIT 1
            """),
            params,
        ).scalar_one_or_none()
        elapsed_ms[f"{source}/{stream}"] = round((time.monotonic() - started) * 1000, 3)
        if value is None:
            raise SystemExit(f"required compact feed has no state: {source}/{stream}")
        latest[f"{source}/{stream}"] = value.astimezone(UTC).isoformat()

payload = {
    "verdict": "PASS",
    "recorded_at": datetime.now(UTC).isoformat(),
    "deployed_head": None,
    "index_name": index_name,
    "index_definition": indexdef,
    "latest_by_feed": latest,
    "query_elapsed_ms": elapsed_ms,
    "plans": plans,
    "safety": {
        "mode": "research",
        "live_trading_enabled": False,
        "max_trade_size_usd": 0,
        "max_daily_loss_usd": 0,
        "automatic_promotion": False,
    },
}
print(json.dumps(payload, indent=2, sort_keys=True))
PY

"$REPO/.venv/bin/python" - "$EVIDENCE_TMP" "$DEPLOYED_HEAD" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
payload["deployed_head"] = sys.argv[2]
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
EVIDENCE_PATH="$EVIDENCE_DIR/phase14-compact-feed-freshness-index-$STAMP.json"
install -o bp -g bp -m 0640 "$EVIDENCE_TMP" "$EVIDENCE_PATH"
sync -f "$EVIDENCE_PATH"
rm -f "$EVIDENCE_TMP"

require_research_zero_money
require_automatic_promotion_false
for unit in "$RECORDER_UNIT" "$V3_PREDICTOR" "$V3_EXECUTION"; do
  systemctl is-active --quiet "$unit" && fail "fail_closed_unit_became_active:$unit" || true
done
require_timer_active_enabled "$MAINTENANCE_TIMER"
require_timer_active_enabled "$DISK_HEALTH_TIMER"
require_timer_active_enabled "$V2_TIMER"
require_timer_active_enabled "$V4_TIMER"

cat "$EVIDENCE_PATH"
echo "EVIDENCE_FILE=$EVIDENCE_PATH"
echo "PHASE14_COMPACT_FEED_FRESHNESS_INDEX_GATE=PASS"
echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"
echo "LIVE_TRADING_ENABLED=false"
echo "MAX_TRADE_SIZE_USD=0"
echo "MAX_DAILY_LOSS_USD=0"
REMOTE
)

REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 -w0)

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "HELPER_HEAD=$HELPER_HEAD"
echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"
echo "INDEX_NAME=$INDEX_NAME"
echo "This helper creates one concurrent PostgreSQL index while recorder/V3 remain fail-closed stopped."
echo "It does not enable live trading, change money limits, or start research services."
echo "Run only with the exact separate approval string."

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo env PHASE14_COMPACT_FEED_FRESHNESS_INDEX_DEPLOYED_HEAD=$DEPLOYED_HEAD_Q PHASE14_COMPACT_FEED_FRESHNESS_INDEX_NAME=$INDEX_NAME_Q bash"
