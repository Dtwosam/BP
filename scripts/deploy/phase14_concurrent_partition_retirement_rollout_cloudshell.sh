#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT='project-4397f2c0-7098-4c1c-abb'
ZONE='us-east1-c'
VM='bp-recorder'
HELPER_HEAD="${PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_HELPER_HEAD:-}"
APPROVAL="${PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_APPROVAL:-}"

FROM_HEAD='7c3af78da1922a0e5187c24b799951130cc98887'
CANDIDATE_BRANCH='ops/phase14-concurrent-partition-retirement-candidate'
CANDIDATE_HEAD='ed7d930c69e417dda388b0cb62b3a543a4b8134f'
ENV_FILE='/etc/bp/bp.env'
STORAGE_EVIDENCE='/mnt/bp-data/evidence/phase14-partitioned-storage-rollout-20260909T070219Z.json'
STORAGE_EVIDENCE_SHA256='f33a28f5306e46c509b0000a176d226c079aa2d160d595095ca228118542ce19'

RUNTIME_SCRIPT_PATH='scripts/storage_maintenance.py'
MAINTENANCE_PATH='src/bp_engine/storage/maintenance.py'
PARTITIONED_PATH='src/bp_engine/storage/partitioned_raw.py'
TEST_PATH='tests/storage/test_partitioned_raw_postgres.py'

fail_local() {
  echo "PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_GATE=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$HELPER_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail_local "helper_head_invalid"

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
[[ -n "$ROOT" ]] || fail_local "local_repository_missing"
cd "$ROOT"
[[ "$(git rev-parse HEAD)" == "$HELPER_HEAD" ]] || fail_local "local_helper_head_mismatch"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || fail_local "local_working_tree_dirty"

REMOTE_MAIN=$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')
[[ "$REMOTE_MAIN" == "$HELPER_HEAD" ]] || fail_local "remote_main_changed"

git fetch --quiet origin "$CANDIDATE_BRANCH"
REMOTE_CANDIDATE=$(git rev-parse "refs/remotes/origin/$CANDIDATE_BRANCH")
[[ "$REMOTE_CANDIDATE" == "$CANDIDATE_HEAD" ]] || fail_local "candidate_branch_changed"
git merge-base --is-ancestor "$FROM_HEAD" "$CANDIDATE_HEAD" || fail_local "candidate_not_descendant_of_deployed_head"

EXPECTED_DIFF=$(printf '%s\n' "$RUNTIME_SCRIPT_PATH" "$MAINTENANCE_PATH" "$PARTITIONED_PATH" "$TEST_PATH" | sort)
ACTUAL_DIFF=$(git diff --name-only "$FROM_HEAD" "$CANDIDATE_HEAD" | sort)
[[ "$ACTUAL_DIFF" == "$EXPECTED_DIFF" ]] || fail_local "candidate_scope_mismatch"

blob_for() {
  git rev-parse "$1:$2"
}

MAIN_RUNTIME_SCRIPT_BLOB=$(blob_for "$HELPER_HEAD" "$RUNTIME_SCRIPT_PATH")
MAIN_MAINTENANCE_BLOB=$(blob_for "$HELPER_HEAD" "$MAINTENANCE_PATH")
MAIN_PARTITIONED_BLOB=$(blob_for "$HELPER_HEAD" "$PARTITIONED_PATH")
MAIN_TEST_BLOB=$(blob_for "$HELPER_HEAD" "$TEST_PATH")

[[ "$(blob_for "$CANDIDATE_HEAD" "$RUNTIME_SCRIPT_PATH")" == "$MAIN_RUNTIME_SCRIPT_BLOB" ]] || fail_local "candidate_runtime_script_blob_not_exact_main"
[[ "$(blob_for "$CANDIDATE_HEAD" "$MAINTENANCE_PATH")" == "$MAIN_MAINTENANCE_BLOB" ]] || fail_local "candidate_maintenance_blob_not_exact_main"
[[ "$(blob_for "$CANDIDATE_HEAD" "$PARTITIONED_PATH")" == "$MAIN_PARTITIONED_BLOB" ]] || fail_local "candidate_partitioned_blob_not_exact_main"
[[ "$(blob_for "$CANDIDATE_HEAD" "$TEST_PATH")" == "$MAIN_TEST_BLOB" ]] || fail_local "candidate_test_blob_not_exact_main"

EXPECTED_APPROVAL="I_APPROVE_PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT:${HELPER_HEAD}:${FROM_HEAD}:${CANDIDATE_HEAD}:${STORAGE_EVIDENCE_SHA256}"
[[ "$APPROVAL" == "$EXPECTED_APPROVAL" ]] || fail_local "production_approval_mismatch"

command -v gcloud >/dev/null 2>&1 || fail_local "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . || fail_local "gcloud_auth_missing"
gcloud config set project "$PROJECT" >/dev/null

printf -v HELPER_HEAD_Q '%q' "$HELPER_HEAD"
printf -v FROM_HEAD_Q '%q' "$FROM_HEAD"
printf -v CANDIDATE_BRANCH_Q '%q' "$CANDIDATE_BRANCH"
printf -v CANDIDATE_HEAD_Q '%q' "$CANDIDATE_HEAD"
printf -v ENV_FILE_Q '%q' "$ENV_FILE"
printf -v STORAGE_EVIDENCE_Q '%q' "$STORAGE_EVIDENCE"
printf -v STORAGE_EVIDENCE_SHA256_Q '%q' "$STORAGE_EVIDENCE_SHA256"
printf -v RUNTIME_SCRIPT_PATH_Q '%q' "$RUNTIME_SCRIPT_PATH"
printf -v MAINTENANCE_PATH_Q '%q' "$MAINTENANCE_PATH"
printf -v PARTITIONED_PATH_Q '%q' "$PARTITIONED_PATH"
printf -v TEST_PATH_Q '%q' "$TEST_PATH"
printf -v RUNTIME_SCRIPT_BLOB_Q '%q' "$MAIN_RUNTIME_SCRIPT_BLOB"
printf -v MAINTENANCE_BLOB_Q '%q' "$MAIN_MAINTENANCE_BLOB"
printf -v PARTITIONED_BLOB_Q '%q' "$MAIN_PARTITIONED_BLOB"
printf -v TEST_BLOB_Q '%q' "$MAIN_TEST_BLOB"

REMOTE_SCRIPT=$(cat <<'REMOTE'
set -Eeuo pipefail

HELPER_HEAD="${PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_HELPER_HEAD:?}"
FROM_HEAD="${PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_FROM_HEAD:?}"
CANDIDATE_BRANCH="${PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_CANDIDATE_BRANCH:?}"
CANDIDATE_HEAD="${PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_CANDIDATE_HEAD:?}"
ENV_FILE="${PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_ENV_FILE:?}"
STORAGE_EVIDENCE="${PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_STORAGE_EVIDENCE:?}"
STORAGE_EVIDENCE_SHA256="${PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_STORAGE_EVIDENCE_SHA256:?}"

RUNTIME_SCRIPT_PATH="${PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_RUNTIME_SCRIPT_PATH:?}"
MAINTENANCE_PATH="${PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_MAINTENANCE_PATH:?}"
PARTITIONED_PATH="${PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_PARTITIONED_PATH:?}"
TEST_PATH="${PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_TEST_PATH:?}"

EXPECTED_RUNTIME_SCRIPT_BLOB="${PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_RUNTIME_SCRIPT_BLOB:?}"
EXPECTED_MAINTENANCE_BLOB="${PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_MAINTENANCE_BLOB:?}"
EXPECTED_PARTITIONED_BLOB="${PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_PARTITIONED_BLOB:?}"
EXPECTED_TEST_BLOB="${PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_TEST_BLOB:?}"

REPO=/opt/bp
SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env
EVIDENCE_DIR=/var/lib/bp/evidence

RECORDER_UNIT=bp-recorder.service
MAINTENANCE_SERVICE=bp-storage-maintenance.service
MAINTENANCE_TIMER=bp-storage-maintenance.timer
DISK_HEALTH_SERVICE=bp-storage-disk-health.service
DISK_HEALTH_TIMER=bp-storage-disk-health.timer
V2_TIMER=bp-v2-forward-coverage.timer
V4_TIMER=bp-v4-forward-coverage.timer
V3_PREDICTOR=bp-v3-frozen-predictor.service
V3_EXECUTION=bp-v3-paper-execution.service

REQUIRED_ACTIVE_SERVICES=(
  bp-postgres.service
  bp-recorder.service
  bp-dashboard-api.service
  bp-dashboard-web.service
  bp-paper-execution.service
  bp-live-predictor.service
  bp-prospective-outcomes.service
  bp-v3-frozen-predictor.service
  bp-v3-paper-execution.service
)

ROLLBACK_ARMED=0
MUTATION_STARTED=0
DISK_BEFORE=''
DISK_AFTER=''
SOAK_FILE=''
SNAPSHOT_FILE=''
EVIDENCE_TMP=''
EVIDENCE_PATH=''
HOLDOUT_FINGERPRINT_BEFORE=''
RECORDER_PID=''
RECORDER_RESTARTS_BEFORE=''
RECORDER_RESTARTS_AFTER=''
V3_PREDICTOR_PID=''
V3_EXECUTION_PID=''
MAINTENANCE_STARTED_AT=''
MAINTENANCE_FINISHED_AT=''
MAINTENANCE_RUN_ID=''
MAINTENANCE_PARTITIONS_RETIRED=''
MAINTENANCE_DEDUPE_ROWS_REMOVED=''

fail() {
  echo "PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_GATE=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

read_env() {
  local path=$1
  local key=$2
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$path"
}

validate_deployed_checkout() {
  local entry code path
  while IFS= read -r entry; do
    [[ -n "$entry" ]] || continue
    code=${entry:0:2}
    path=${entry:3}
    if [[ "$code" == "??" ]]; then
      case "$path" in
        .node/*|apps/dashboard/.next/*|apps/dashboard/node_modules/*|apps/dashboard/tsconfig.tsbuildinfo) ;;
        *) fail "unexpected_deployed_checkout_change:$path" ;;
      esac
    else
      case "$path" in
        apps/dashboard/next-env.d.ts|apps/dashboard/tsconfig.json) ;;
        *) fail "unexpected_deployed_checkout_change:$path" ;;
      esac
    fi
  done < <(git -C "$REPO" status --porcelain --untracked-files=all)
}

validate_candidate_scope() {
  local expected actual
  expected=$(printf '%s\n' "$RUNTIME_SCRIPT_PATH" "$MAINTENANCE_PATH" "$PARTITIONED_PATH" "$TEST_PATH" | sort)
  actual=$(git -C "$REPO" diff --name-only "$FROM_HEAD" "$CANDIDATE_HEAD" | sort)
  [[ "$actual" == "$expected" ]] || fail "candidate_scope_mismatch"

  [[ "$(git -C "$REPO" rev-parse "$CANDIDATE_HEAD:$RUNTIME_SCRIPT_PATH")" == "$EXPECTED_RUNTIME_SCRIPT_BLOB" ]] || fail "candidate_runtime_script_blob_mismatch"
  [[ "$(git -C "$REPO" rev-parse "$CANDIDATE_HEAD:$MAINTENANCE_PATH")" == "$EXPECTED_MAINTENANCE_BLOB" ]] || fail "candidate_maintenance_blob_mismatch"
  [[ "$(git -C "$REPO" rev-parse "$CANDIDATE_HEAD:$PARTITIONED_PATH")" == "$EXPECTED_PARTITIONED_BLOB" ]] || fail "candidate_partitioned_blob_mismatch"
  [[ "$(git -C "$REPO" rev-parse "$CANDIDATE_HEAD:$TEST_PATH")" == "$EXPECTED_TEST_BLOB" ]] || fail "candidate_test_blob_mismatch"
}

validate_unit_file() {
  local unit=$1
  local repo_path=$2
  local fragment dropins
  fragment=$(systemctl show -p FragmentPath --value "$unit")
  [[ -r "$fragment" ]] || fail "unit_fragment_missing:$unit"
  cmp -s "$fragment" "$REPO/$repo_path" || fail "unit_fragment_mismatch:$unit"
  dropins=$(systemctl show -p DropInPaths --value "$unit")
  [[ -z "$dropins" ]] || fail "unit_dropins_present:$unit"
}

validate_unit_contracts() {
  validate_unit_file "$RECORDER_UNIT" deploy/systemd/bp-recorder.service
  validate_unit_file "$MAINTENANCE_SERVICE" deploy/systemd/bp-storage-maintenance.service
  validate_unit_file "$MAINTENANCE_TIMER" deploy/systemd/bp-storage-maintenance.timer
  validate_unit_file "$DISK_HEALTH_SERVICE" deploy/systemd/bp-storage-disk-health.service
  validate_unit_file "$DISK_HEALTH_TIMER" deploy/systemd/bp-storage-disk-health.timer

  local recorder_env maintenance_env
  recorder_env=$(systemctl show -p EnvironmentFiles --value "$RECORDER_UNIT")
  maintenance_env=$(systemctl show -p EnvironmentFiles --value "$MAINTENANCE_SERVICE")
  [[ "$recorder_env" == "$ENV_FILE (ignore_errors=no)" ]] || fail "recorder_environment_file_mismatch"
  [[ "$maintenance_env" == "$ENV_FILE (ignore_errors=no)" ]] || fail "maintenance_environment_file_mismatch"
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
if not values:
    raise SystemExit("PROJECT_STATE.json has no automatic_promotion field")
if any(value is not False for value in values):
    raise SystemExit("automatic_promotion must remain false")
PY
}

require_core_services_active() {
  local service
  for service in "${REQUIRED_ACTIVE_SERVICES[@]}"; do
    systemctl is-active --quiet "$service" || fail "required_service_not_active:$service"
  done
}

require_timer_active_enabled() {
  local timer=$1
  systemctl is-enabled --quiet "$timer" || fail "timer_not_enabled:$timer"
  systemctl is-active --quiet "$timer" || fail "timer_not_active:$timer"
}

require_timer_enabled_inactive() {
  local timer=$1
  systemctl is-enabled --quiet "$timer" || fail "timer_not_enabled:$timer"
  [[ "$(systemctl show -p ActiveState --value "$timer")" == "inactive" ]] || fail "timer_not_inactive:$timer"
}

require_oneshot_idle_success() {
  local service=$1
  [[ "$(systemctl show -p ActiveState --value "$service")" == "inactive" ]] || fail "oneshot_not_idle:$service"
  [[ "$(systemctl show -p Result --value "$service")" == "success" ]] || fail "oneshot_last_result_not_success:$service"
}

wait_for_oneshot_idle_success() {
  local service=$1
  local timeout_seconds=$2
  local waited=0
  local active_state
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

run_storage_health() {
  local destination=$1
  if ! sudo -u bp "$REPO/.venv/bin/python" "$REPO/scripts/storage_maintenance.py" disk-health --env-file "$ENV_FILE" > "$destination"; then
    cat "$destination" >&2 || true
    fail "storage_health_command_failed"
  fi
  "$REPO/.venv/bin/python" - "$destination" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("status") != "ok":
    raise SystemExit("storage health is not ok")
if payload.get("storage_mode") != "partitioned":
    raise SystemExit("storage mode is not partitioned")
guards = payload.get("guards") or {}
required = ("maintenance_fresh", "current_partition_present", "retention_current")
missing = [name for name in required if guards.get(name) is not True]
if missing:
    raise SystemExit(f"storage guards not satisfied: {missing}")
PY
}

gate_b_fingerprint() {
  "$REPO/.venv/bin/python" - "$EVIDENCE_DIR" <<'PY'
import hashlib
import sys
from pathlib import Path

root = Path(sys.argv[1])
digest = hashlib.sha256()
if root.exists():
    names = {"plan.json", "selection.json", "summary.json", "holdout-attempt.json"}
    for path in sorted(
        (item for item in root.glob("phase14-v2-gate-b-*/*") if item.name in names),
        key=lambda item: str(item),
    ):
        digest.update(str(path).encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
print(digest.hexdigest())
PY
}

require_no_detached_retirement_leftovers() {
  sudo -u bp bash -c '
set -a
source "$1"
set +a
exec "$2" - <<"PY"
from sqlalchemy import create_engine, text
from bp_engine.config import Settings

settings = Settings(_env_file="/etc/bp/bp.env")
engine = create_engine(settings.database_url)
with engine.connect() as connection:
    pending = int(connection.execute(text("""
        SELECT count(*)
        FROM pg_inherits AS inheritance
        JOIN pg_class AS parent ON parent.oid = inheritance.inhparent
        JOIN pg_namespace AS parent_ns ON parent_ns.oid = parent.relnamespace
        JOIN pg_class AS child ON child.oid = inheritance.inhrelid
        WHERE parent_ns.nspname = current_schema()
          AND parent.relname = :parent_name
          AND inheritance.inhdetachpending
    """), {"parent_name": "raw_market_events"}).scalar_one())
    detached = int(connection.execute(text("""
        SELECT count(*)
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = current_schema()
          AND relation.relkind = :relkind
          AND relation.relname ~ :pattern
          AND NOT EXISTS (
              SELECT 1
              FROM pg_inherits AS inheritance
              WHERE inheritance.inhrelid = relation.oid
          )
    """), {
        "relkind": "r",
        "pattern": "^raw_market_events_[0-9]{8}_[0-9]{2}$",
    }).scalar_one())
if pending or detached:
    raise SystemExit(f"raw retirement leftovers present: pending={pending} detached={detached}")
PY
' _ "$ENV_FILE" "$REPO/.venv/bin/python"
}

require_eligible_partition() {
  sudo -u bp bash -c '
set -a
source "$1"
set +a
exec "$2" - <<"PY"
from datetime import UTC, datetime, timedelta
from sqlalchemy import create_engine
from bp_engine.config import Settings
from bp_engine.storage.partitioned_raw import list_raw_partitions

settings = Settings(_env_file="/etc/bp/bp.env")
engine = create_engine(settings.database_url)
now = datetime.now(UTC)
eligible_end = (now - timedelta(hours=settings.storage_hot_raw_hours)).replace(
    minute=0, second=0, microsecond=0
)
eligible = [
    item for item in list_raw_partitions(engine)
    if item.end_at <= eligible_end
]
if not eligible:
    raise SystemExit("no_eligible_partition_for_acceptance")
print(eligible[0].name)
PY
' _ "$ENV_FILE" "$REPO/.venv/bin/python"
}

capture_service_identity() {
  RECORDER_PID=$(systemctl show -p MainPID --value "$RECORDER_UNIT")
  RECORDER_RESTARTS_BEFORE=$(systemctl show -p NRestarts --value "$RECORDER_UNIT")
  V3_PREDICTOR_PID=$(systemctl show -p MainPID --value "$V3_PREDICTOR")
  V3_EXECUTION_PID=$(systemctl show -p MainPID --value "$V3_EXECUTION")
  [[ "$RECORDER_PID" =~ ^[1-9][0-9]*$ ]] || fail "recorder_main_pid_invalid"
  [[ "$RECORDER_RESTARTS_BEFORE" =~ ^[0-9]+$ ]] || fail "recorder_restart_count_invalid"
  [[ "$V3_PREDICTOR_PID" =~ ^[1-9][0-9]*$ ]] || fail "v3_predictor_pid_invalid"
  [[ "$V3_EXECUTION_PID" =~ ^[1-9][0-9]*$ ]] || fail "v3_execution_pid_invalid"
}

require_service_identity_unchanged() {
  systemctl is-active --quiet "$RECORDER_UNIT" || fail "recorder_not_active_after_maintenance"
  systemctl is-active --quiet "$V3_PREDICTOR" || fail "v3_predictor_not_active_after_maintenance"
  systemctl is-active --quiet "$V3_EXECUTION" || fail "v3_execution_not_active_after_maintenance"
  [[ "$(systemctl show -p MainPID --value "$RECORDER_UNIT")" == "$RECORDER_PID" ]] || fail "recorder_main_pid_changed"
  [[ "$(systemctl show -p MainPID --value "$V3_PREDICTOR")" == "$V3_PREDICTOR_PID" ]] || fail "v3_predictor_pid_changed"
  [[ "$(systemctl show -p MainPID --value "$V3_EXECUTION")" == "$V3_EXECUTION_PID" ]] || fail "v3_execution_pid_changed"
  RECORDER_RESTARTS_AFTER=$(systemctl show -p NRestarts --value "$RECORDER_UNIT")
  [[ "$RECORDER_RESTARTS_AFTER" == "$RECORDER_RESTARTS_BEFORE" ]] || fail "recorder_restarted_during_maintenance"
}

run_acceptance_maintenance() {
  local since_iso
  MAINTENANCE_STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  since_iso="$MAINTENANCE_STARTED_AT"
  systemctl reset-failed "$MAINTENANCE_SERVICE" >/dev/null 2>&1 || true
  if ! systemctl start "$MAINTENANCE_SERVICE"; then
    journalctl -u "$MAINTENANCE_SERVICE" -n 160 --no-pager >&2 || true
    fail "maintenance_cycle_failed"
  fi
  MAINTENANCE_FINISHED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  [[ "$(systemctl show -p Result --value "$MAINTENANCE_SERVICE")" == "success" ]] || fail "maintenance_result_not_success"
  [[ "$(systemctl show -p ExecMainStatus --value "$MAINTENANCE_SERVICE")" == "0" ]] || fail "maintenance_exit_nonzero"

  read -r MAINTENANCE_RUN_ID MAINTENANCE_PARTITIONS_RETIRED MAINTENANCE_DEDUPE_ROWS_REMOVED < <(
    sudo -u bp bash -c '
set -a
source "$1"
set +a
exec "$2" - "$3" <<"PY"
import sys
from sqlalchemy import create_engine, text
from bp_engine.config import Settings

settings = Settings(_env_file="/etc/bp/bp.env")
engine = create_engine(settings.database_url)
with engine.connect() as connection:
    row = connection.execute(text("""
        SELECT id, partitions_retired, dedupe_rows_removed
        FROM storage_maintenance_runs
        WHERE status = :status
          AND started_at >= CAST(:since_at AS timestamptz)
        ORDER BY id DESC
        LIMIT 1
    """), {"status": "success", "since_at": sys.argv[1]}).one_or_none()
if row is None:
    raise SystemExit("acceptance maintenance success row missing")
print(int(row[0]), int(row[1]), int(row[2]))
PY
' _ "$ENV_FILE" "$REPO/.venv/bin/python" "$since_iso"
  )
  [[ "$MAINTENANCE_RUN_ID" =~ ^[0-9]+$ ]] || fail "maintenance_run_id_invalid"
  [[ "$MAINTENANCE_PARTITIONS_RETIRED" =~ ^[0-9]+$ ]] || fail "maintenance_partitions_retired_invalid"
  (( MAINTENANCE_PARTITIONS_RETIRED >= 1 )) || fail "maintenance_did_not_exercise_partition_retirement"
}

run_soak_gate() {
  SOAK_FILE=$(mktemp /var/tmp/bp-phase14-concurrent-retirement-soak.XXXXXX.json)
  if ! sudo -u bp bash -c 'set -a; source "$1"; set +a; exec "$2" "$3" --hours 0.01 --minimum-hours 0.008' _ "$ENV_FILE" "$REPO/.venv/bin/python" "$REPO/scripts/soak_report.py" > "$SOAK_FILE"; then
    cat "$SOAK_FILE" >&2 || true
    fail "natural_load_soak_failed"
  fi
  "$REPO/.venv/bin/python" - "$SOAK_FILE" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("passed") is not True:
    raise SystemExit(f"soak failed: {payload.get('failures')}")
required = {"polymarket/market", "bybit/spot", "bybit/linear", "coinbase/spot"}
feeds = payload.get("feeds") or {}
missing = sorted(label for label in required if int((feeds.get(label) or {}).get("event_count", 0)) <= 0)
if missing:
    raise SystemExit(f"required feeds missing events: {missing}")
for label in required:
    incidents = (payload.get("incidents") or {}).get(label) or {}
    if int(incidents.get("backpressure", 0)) != 0:
        raise SystemExit(f"backpressure recorded for {label}")
PY
}

verify_dashboard_safety() {
  SNAPSHOT_FILE=$(mktemp /var/tmp/bp-phase14-concurrent-retirement-snapshot.XXXXXX.json)
  curl -fsS http://127.0.0.1:8787/api/v1/snapshot > "$SNAPSHOT_FILE" || fail "dashboard_snapshot_unavailable"
  "$REPO/.venv/bin/python" - "$SNAPSHOT_FILE" <<'PY'
import json
import sys
from pathlib import Path

snapshot = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
mode = snapshot.get("mode") or {}
if mode.get("trading_mode") != "RESEARCH":
    raise SystemExit("dashboard left RESEARCH mode")
if mode.get("live_trading_enabled") is not False:
    raise SystemExit("dashboard reports live trading enabled")
if mode.get("execution_available") is not False:
    raise SystemExit("dashboard reports real execution available")
PY
}

reconcile_candidate_storage() {
  [[ "$(git -C "$REPO" rev-parse HEAD 2>/dev/null || true)" == "$CANDIDATE_HEAD" ]] || return 1
  systemctl stop "$MAINTENANCE_SERVICE" >/dev/null 2>&1 || true
  systemctl reset-failed "$MAINTENANCE_SERVICE" >/dev/null 2>&1 || true
  systemctl start "$MAINTENANCE_SERVICE" >/dev/null 2>&1 || return 1
  require_no_detached_retirement_leftovers
}

rollback() {
  set +e
  echo "PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_ROLLBACK=START" >&2
  systemctl stop "$MAINTENANCE_TIMER" >/dev/null 2>&1 || true

  local current_head
  current_head=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || true)

  if [[ "$current_head" != "$CANDIDATE_HEAD" ]]; then
    systemctl stop "$V3_EXECUTION" >/dev/null 2>&1 || true
    systemctl stop "$V3_PREDICTOR" >/dev/null 2>&1 || true
    systemctl stop "$RECORDER_UNIT" >/dev/null 2>&1 || true
    systemctl start "$MAINTENANCE_TIMER" >/dev/null 2>&1 || true
    echo "PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_ROLLBACK=COMPLETE_PRECHECKOUT" >&2
    echo "DEPLOYED_HEAD=$current_head" >&2
    echo "RECORDER_ACTIVE=$(systemctl is-active "$RECORDER_UNIT" 2>/dev/null || true)" >&2
    echo "MAINTENANCE_TIMER_ACTIVE=$(systemctl is-active "$MAINTENANCE_TIMER" 2>/dev/null || true)" >&2
    set -e
    return
  fi

  systemctl stop "$V3_EXECUTION" >/dev/null 2>&1 || true
  systemctl stop "$V3_PREDICTOR" >/dev/null 2>&1 || true
  systemctl stop "$RECORDER_UNIT" >/dev/null 2>&1 || true
  systemctl stop "$MAINTENANCE_SERVICE" >/dev/null 2>&1 || true

  if reconcile_candidate_storage; then
    git -C "$REPO" checkout --detach "$FROM_HEAD" >/dev/null 2>&1 || true
    systemctl start "$DISK_HEALTH_TIMER" >/dev/null 2>&1 || true
    systemctl start "$MAINTENANCE_TIMER" >/dev/null 2>&1 || true
    systemctl start "$V2_TIMER" >/dev/null 2>&1 || true
    systemctl start "$V4_TIMER" >/dev/null 2>&1 || true
    echo "PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_ROLLBACK=COMPLETE" >&2
  else
    systemctl start "$DISK_HEALTH_TIMER" >/dev/null 2>&1 || true
    systemctl start "$MAINTENANCE_TIMER" >/dev/null 2>&1 || true
    echo "PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_ROLLBACK=INCOMPLETE_RECONCILIATION_REQUIRED" >&2
  fi

  echo "DEPLOYED_HEAD=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || true)" >&2
  echo "RECORDER_ACTIVE=$(systemctl is-active "$RECORDER_UNIT" 2>/dev/null || true)" >&2
  echo "MAINTENANCE_TIMER_ACTIVE=$(systemctl is-active "$MAINTENANCE_TIMER" 2>/dev/null || true)" >&2
  set -e
}

cleanup() {
  local rc=$?
  trap - EXIT
  set +e
  if (( rc != 0 && ROLLBACK_ARMED )); then
    rollback
  fi
  [[ -n "$DISK_BEFORE" ]] && rm -f "$DISK_BEFORE"
  [[ -n "$DISK_AFTER" ]] && rm -f "$DISK_AFTER"
  [[ -n "$SOAK_FILE" ]] && rm -f "$SOAK_FILE"
  [[ -n "$SNAPSHOT_FILE" ]] && rm -f "$SNAPSHOT_FILE"
  [[ -n "$EVIDENCE_TMP" ]] && rm -f "$EVIDENCE_TMP"
  set -e
  exit "$rc"
}
trap cleanup EXIT

[[ -d "$REPO/.git" ]] || fail "deployed_repo_missing"
[[ -x "$REPO/.venv/bin/python" ]] || fail "python_runtime_missing"
[[ -r "$STORAGE_EVIDENCE" ]] || fail "storage_evidence_missing"
[[ "$(sha256sum "$STORAGE_EVIDENCE" | awk '{print $1}')" == "$STORAGE_EVIDENCE_SHA256" ]] || fail "storage_evidence_sha256_mismatch"
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$FROM_HEAD" ]] || fail "unexpected_deployed_head"
ROLLBACK_ARMED=1

validate_deployed_checkout
validate_unit_contracts
require_research_zero_money
require_automatic_promotion_false
require_core_services_active
require_timer_enabled_inactive "$MAINTENANCE_TIMER"
require_timer_active_enabled "$DISK_HEALTH_TIMER"
require_timer_active_enabled "$V2_TIMER"
require_timer_active_enabled "$V4_TIMER"
require_oneshot_idle_success "$MAINTENANCE_SERVICE"
wait_for_oneshot_idle_success "$DISK_HEALTH_SERVICE" 30
require_no_detached_retirement_leftovers

DISK_BEFORE=$(mktemp /var/tmp/bp-phase14-concurrent-retirement-disk-before.XXXXXX.json)
run_storage_health "$DISK_BEFORE"
HOLDOUT_FINGERPRINT_BEFORE=$(gate_b_fingerprint)
capture_service_identity

git -C "$REPO" fetch --quiet origin "refs/heads/$CANDIDATE_BRANCH:refs/remotes/origin/$CANDIDATE_BRANCH"
[[ "$(git -C "$REPO" rev-parse "refs/remotes/origin/$CANDIDATE_BRANCH")" == "$CANDIDATE_HEAD" ]] || fail "candidate_branch_changed"
git -C "$REPO" merge-base --is-ancestor "$FROM_HEAD" "$CANDIDATE_HEAD" || fail "candidate_not_descendant_of_deployed_head"
validate_candidate_scope

systemctl stop "$MAINTENANCE_TIMER"
require_timer_enabled_inactive "$MAINTENANCE_TIMER"
require_oneshot_idle_success "$MAINTENANCE_SERVICE"
require_eligible_partition

git -C "$REPO" checkout --detach "$CANDIDATE_HEAD" >/dev/null
MUTATION_STARTED=1
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$CANDIDATE_HEAD" ]] || fail "candidate_checkout_failed"
validate_deployed_checkout
validate_candidate_scope
validate_unit_contracts
require_research_zero_money
require_automatic_promotion_false
require_core_services_active
require_timer_active_enabled "$DISK_HEALTH_TIMER"
require_timer_active_enabled "$V2_TIMER"
require_timer_active_enabled "$V4_TIMER"

run_acceptance_maintenance
require_service_identity_unchanged
require_no_detached_retirement_leftovers
run_soak_gate
verify_dashboard_safety

DISK_AFTER=$(mktemp /var/tmp/bp-phase14-concurrent-retirement-disk-after.XXXXXX.json)
run_storage_health "$DISK_AFTER"

systemctl start "$MAINTENANCE_TIMER"
require_timer_active_enabled "$MAINTENANCE_TIMER"
require_timer_active_enabled "$DISK_HEALTH_TIMER"
require_timer_active_enabled "$V2_TIMER"
require_timer_active_enabled "$V4_TIMER"
require_core_services_active
require_service_identity_unchanged
require_research_zero_money
require_automatic_promotion_false
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$CANDIDATE_HEAD" ]] || fail "deployed_head_changed"
validate_deployed_checkout

HOLDOUT_FINGERPRINT_AFTER=$(gate_b_fingerprint)
[[ "$HOLDOUT_FINGERPRINT_AFTER" == "$HOLDOUT_FINGERPRINT_BEFORE" ]] || fail "gate_b_artifacts_changed"

install -d -o bp -g bp -m 0750 "$EVIDENCE_DIR"
EVIDENCE_TMP=$(mktemp /var/tmp/bp-phase14-concurrent-retirement-evidence.XXXXXX.json)
"$REPO/.venv/bin/python" -   "$DISK_BEFORE" "$DISK_AFTER" "$SOAK_FILE" "$EVIDENCE_TMP"   "$HELPER_HEAD" "$FROM_HEAD" "$CANDIDATE_HEAD" "$STORAGE_EVIDENCE" "$STORAGE_EVIDENCE_SHA256"   "$RECORDER_PID" "$RECORDER_RESTARTS_BEFORE" "$RECORDER_RESTARTS_AFTER"   "$V3_PREDICTOR_PID" "$V3_EXECUTION_PID"   "$MAINTENANCE_STARTED_AT" "$MAINTENANCE_FINISHED_AT" "$MAINTENANCE_RUN_ID"   "$MAINTENANCE_PARTITIONS_RETIRED" "$MAINTENANCE_DEDUPE_ROWS_REMOVED"   "$HOLDOUT_FINGERPRINT_AFTER" <<'PY'
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

(
    disk_before_path, disk_after_path, soak_path, output_path,
    helper_head, from_head, candidate_head, storage_evidence, storage_evidence_sha256,
    recorder_pid, restarts_before, restarts_after, v3_predictor_pid, v3_execution_pid,
    maintenance_started_at, maintenance_finished_at, maintenance_run_id,
    partitions_retired, dedupe_rows_removed, gate_b_fingerprint,
) = sys.argv[1:]

payload = {
    "verdict": "PASS",
    "recorded_at": datetime.now(UTC).isoformat(),
    "helper_head": helper_head,
    "from_head": from_head,
    "deployed_head": candidate_head,
    "storage_evidence": storage_evidence,
    "storage_evidence_sha256": storage_evidence_sha256,
    "maintenance": {
        "started_at": maintenance_started_at,
        "finished_at": maintenance_finished_at,
        "run_id": int(maintenance_run_id),
        "partitions_retired": int(partitions_retired),
        "dedupe_rows_removed": int(dedupe_rows_removed),
        "result": "success",
    },
    "recorder": {
        "active": True,
        "main_pid": int(recorder_pid),
        "restarts_before": int(restarts_before),
        "restarts_after": int(restarts_after),
    },
    "v3": {
        "predictor_active": True,
        "predictor_pid": int(v3_predictor_pid),
        "execution_active": True,
        "execution_pid": int(v3_execution_pid),
    },
    "v4": {"forward_timer_active": True},
    "storage": {
        "before": json.loads(Path(disk_before_path).read_text(encoding="utf-8")),
        "after": json.loads(Path(disk_after_path).read_text(encoding="utf-8")),
        "detached_retirement_leftovers": 0,
    },
    "soak": json.loads(Path(soak_path).read_text(encoding="utf-8")),
    "safety": {
        "mode": "research",
        "live_trading_enabled": False,
        "max_trade_size_usd": 0,
        "max_daily_loss_usd": 0,
        "automatic_promotion": False,
    },
    "gate_b": {
        "actions_performed": False,
        "artifacts_fingerprint": gate_b_fingerprint,
    },
}
Path(output_path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
EVIDENCE_PATH="$EVIDENCE_DIR/phase14-concurrent-partition-retirement-rollout-$STAMP.json"
install -o bp -g bp -m 0640 "$EVIDENCE_TMP" "$EVIDENCE_PATH"
ROLLBACK_ARMED=0

cat "$EVIDENCE_PATH"
echo "EVIDENCE_FILE=$EVIDENCE_PATH"
echo "PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_GATE=PASS"
REMOTE
)

REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 -w0)

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "HELPER_HEAD=$HELPER_HEAD"
echo "FROM_HEAD=$FROM_HEAD"
echo "CANDIDATE_BRANCH=$CANDIDATE_BRANCH"
echo "CANDIDATE_HEAD=$CANDIDATE_HEAD"
echo "STORAGE_EVIDENCE_SHA256=$STORAGE_EVIDENCE_SHA256"
echo "This helper mutates the production checkout and runs real storage maintenance under the active recorder."
echo "It does not enable live trading or nonzero money."
echo "Run only with the exact separate approval string."

gcloud compute ssh "$VM"   --project="$PROJECT"   --zone="$ZONE"   --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo env PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_HELPER_HEAD=$HELPER_HEAD_Q PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_FROM_HEAD=$FROM_HEAD_Q PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_CANDIDATE_BRANCH=$CANDIDATE_BRANCH_Q PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_CANDIDATE_HEAD=$CANDIDATE_HEAD_Q PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_ENV_FILE=$ENV_FILE_Q PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_STORAGE_EVIDENCE=$STORAGE_EVIDENCE_Q PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_STORAGE_EVIDENCE_SHA256=$STORAGE_EVIDENCE_SHA256_Q PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_RUNTIME_SCRIPT_PATH=$RUNTIME_SCRIPT_PATH_Q PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_MAINTENANCE_PATH=$MAINTENANCE_PATH_Q PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_PARTITIONED_PATH=$PARTITIONED_PATH_Q PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_TEST_PATH=$TEST_PATH_Q PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_RUNTIME_SCRIPT_BLOB=$RUNTIME_SCRIPT_BLOB_Q PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_MAINTENANCE_BLOB=$MAINTENANCE_BLOB_Q PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_PARTITIONED_BLOB=$PARTITIONED_BLOB_Q PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_TEST_BLOB=$TEST_BLOB_Q bash"
