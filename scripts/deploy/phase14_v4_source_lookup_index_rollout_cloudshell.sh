#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V4_SOURCE_LOOKUP_INDEX_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_V4_SOURCE_LOOKUP_INDEX_ZONE:-us-east1-c}"
VM="${PHASE14_V4_SOURCE_LOOKUP_INDEX_VM:-bp-recorder}"
HELPER_HEAD="${PHASE14_V4_SOURCE_LOOKUP_INDEX_HELPER_HEAD:-}"
APPROVAL="${PHASE14_V4_SOURCE_LOOKUP_INDEX_APPROVAL:-}"
PREFLIGHT_ONLY="${PHASE14_V4_SOURCE_LOOKUP_INDEX_PREFLIGHT_ONLY:-false}"

FROM_HEAD='3316f22d89689a4dfe8c7de2da6956b8eb846d1f'
CANDIDATE_BRANCH='ops/phase14-v4-source-lookup-index-candidate-20261007'
CANDIDATE_HEAD='9d9a8c3c34c25e7c547e64270d92b5e12480e381'
EXPECTED_SHADOW_RUN_ID='v4-fresh-book-shadow-20261007T200650Z-d58b2cbedc3f'
EXPECTED_SHADOW_UNIT='bp-v4-fresh-book-shadow-20261007T200650Z-d58b2cbedc3f.service'
EXPECTED_MODEL_SHA256='6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf'
INDEX_SUFFIX='_v4_source_lookup_idx'

PARTITIONED_PATH='src/bp_engine/storage/partitioned_raw.py'
PARTITION_TEST_PATH='tests/storage/test_partitioned_raw_postgres.py'
MIGRATION_PATH='scripts/deploy/phase14_v4_source_lookup_index_migration.py'

fail_local() {
  echo "PHASE14_V4_SOURCE_LOOKUP_INDEX_ROLLOUT_GATE=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$HELPER_HEAD" =~ ^[0-9a-f]{40}$ ]] ||
  fail_local "helper_head_invalid"

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$ROOT" ]] || fail_local "local_repository_missing"
cd "$ROOT"

[[ "$(git rev-parse HEAD)" == "$HELPER_HEAD" ]] ||
  fail_local "local_helper_head_mismatch"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  fail_local "local_working_tree_dirty"

REMOTE_MAIN="$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')"
[[ "$REMOTE_MAIN" == "$HELPER_HEAD" ]] ||
  fail_local "remote_main_changed"

git fetch --quiet origin \
  "$CANDIDATE_BRANCH:refs/remotes/origin/$CANDIDATE_BRANCH"
REMOTE_CANDIDATE="$(
  git rev-parse "refs/remotes/origin/$CANDIDATE_BRANCH"
)"
[[ "$REMOTE_CANDIDATE" == "$CANDIDATE_HEAD" ]] ||
  fail_local "candidate_branch_changed"
git merge-base --is-ancestor "$FROM_HEAD" "$CANDIDATE_HEAD" ||
  fail_local "candidate_not_descendant_of_deployed_head"

EXPECTED_DIFF="$(
  printf '%s\n' \
    "$MIGRATION_PATH" \
    "$PARTITIONED_PATH" \
    "$PARTITION_TEST_PATH" |
    sort
)"
ACTUAL_DIFF="$(
  git diff --name-only "$FROM_HEAD" "$CANDIDATE_HEAD" |
    sort
)"
[[ "$ACTUAL_DIFF" == "$EXPECTED_DIFF" ]] ||
  fail_local "candidate_scope_mismatch"

for path in \
  "$MIGRATION_PATH" \
  "$PARTITIONED_PATH" \
  "$PARTITION_TEST_PATH"
do
  [[ "$(git rev-parse "$CANDIDATE_HEAD:$path")" == \
      "$(git rev-parse "$HELPER_HEAD:$path")" ]] ||
    fail_local "candidate_blob_not_exact_main:$path"
done

EXPECTED_APPROVAL="I_APPROVE_PHASE14_V4_SOURCE_LOOKUP_INDEX_ROLLOUT:${HELPER_HEAD}:${FROM_HEAD}:${CANDIDATE_HEAD}:${EXPECTED_SHADOW_RUN_ID}:${EXPECTED_MODEL_SHA256}"

case "$PREFLIGHT_ONLY" in
  true|false) ;;
  *) fail_local "preflight_only_invalid" ;;
esac

if [[ "$PREFLIGHT_ONLY" == "true" ]]; then
  echo "PHASE14_V4_SOURCE_LOOKUP_INDEX_ROLLOUT_PREFLIGHT=PASS"
  echo "HELPER_HEAD=$HELPER_HEAD"
  echo "FROM_HEAD=$FROM_HEAD"
  echo "CANDIDATE_HEAD=$CANDIDATE_HEAD"
  echo "EXPECTED_SHADOW_RUN_ID=$EXPECTED_SHADOW_RUN_ID"
  echo "EXPECTED_MODEL_SHA256=$EXPECTED_MODEL_SHA256"
  echo "INDEX_SUFFIX=$INDEX_SUFFIX"
  echo "EXPECTED_APPROVAL=$EXPECTED_APPROVAL"
  echo "PRODUCTION_HOST_CONTACTED=false"
  echo "PRODUCTION_MUTATION_PERFORMED=false"
  echo "DATABASE_DDL_PERFORMED=false"
  echo "LIVE_TRADING_ENABLED=false"
  echo "REAL_MONEY_USD=0"
  exit 0
fi

[[ "$APPROVAL" == "$EXPECTED_APPROVAL" ]] ||
  fail_local "production_approval_mismatch"

command -v gcloud >/dev/null 2>&1 ||
  fail_local "gcloud_missing"
gcloud auth list \
  --filter=status:ACTIVE \
  --format='value(account)' |
  grep -q . ||
  fail_local "gcloud_auth_missing"

gcloud config set project "$PROJECT" >/dev/null

read -r -d '' REMOTE_SCRIPT <<'REMOTE' || true
set -Eeuo pipefail

FROM_HEAD='3316f22d89689a4dfe8c7de2da6956b8eb846d1f'
CANDIDATE_BRANCH='ops/phase14-v4-source-lookup-index-candidate-20261007'
CANDIDATE_HEAD='9d9a8c3c34c25e7c547e64270d92b5e12480e381'
EXPECTED_SHADOW_RUN_ID='v4-fresh-book-shadow-20261007T200650Z-d58b2cbedc3f'
EXPECTED_SHADOW_UNIT='bp-v4-fresh-book-shadow-20261007T200650Z-d58b2cbedc3f.service'
EXPECTED_MODEL_SHA256='6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf'

REPO=/opt/bp
ENV_FILE=/etc/bp/bp.env
SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env
EVIDENCE_DIR=/var/lib/bp/evidence
RECORDER_UNIT=bp-recorder.service
MAINTENANCE_SERVICE=bp-storage-maintenance.service
MAINTENANCE_TIMER=bp-storage-maintenance.timer
DISK_HEALTH_SERVICE=bp-storage-disk-health.service
DISK_HEALTH_TIMER=bp-storage-disk-health.timer
FAST_LIVE_SOURCE=bp-phase15-fast-live-source.service
EXPECTED_EVIDENCE="$EVIDENCE_DIR/$EXPECTED_SHADOW_RUN_ID.jsonl"
MIGRATION="$REPO/scripts/deploy/phase14_v4_source_lookup_index_migration.py"

MUTATION_STARTED=0
ROLLBACK_ARMED=0
MIGRATION_COMPLETE=0
BACKUP_DIR=''
MIGRATION_TMP=''
DISK_BEFORE=''
DISK_AFTER=''
SHADOW_PID=''
SHADOW_RESTARTS=''
RECORDER_PID=''
RECORDER_RESTARTS=''

fail() {
  echo "PHASE14_V4_SOURCE_LOOKUP_INDEX_ROLLOUT_GATE=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

read_env() {
  local path=$1 key=$2
  awk -F= -v key="$key" \
    '$1 == key {sub(/^[^=]*=/, ""); print; exit}' \
    "$path"
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
  done < <(
    git -C "$REPO" status --porcelain --untracked-files=all
  )
}

require_research_zero_money() {
  local path mode live trade loss
  for path in "$ENV_FILE" "$SAFETY_FILE"; do
    [[ -r "$path" ]] || fail "safety_file_missing:$path"
    mode="$(read_env "$path" MODE)"
    live="$(read_env "$path" LIVE_TRADING_ENABLED)"
    trade="$(read_env "$path" MAX_TRADE_SIZE_USD)"
    loss="$(read_env "$path" MAX_DAILY_LOSS_USD)"
    [[ "$mode" == "research" ]] ||
      fail "mode_not_research:$path"
    [[ "$live" == "false" ]] ||
      fail "live_trading_enabled:$path"
    [[ "$trade" == "0" ]] ||
      fail "max_trade_size_nonzero:$path"
    [[ "$loss" == "0" ]] ||
      fail "max_daily_loss_nonzero:$path"
  done
}

require_recorder_batch100() {
  sudo -u bp env \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$REPO/src" \
    "$REPO/.venv/bin/python" - "$ENV_FILE" <<'PY'
import sys
from bp_engine.config import Settings

settings = Settings(_env_file=sys.argv[1])
if settings.recorder_batch_size != 100:
    raise SystemExit(
        f"RECORDER_BATCH_SIZE must equal 100, "
        f"got {settings.recorder_batch_size}"
    )
print(f"RECORDER_BATCH_SIZE={settings.recorder_batch_size}")
PY
}

require_timer_active_enabled() {
  local timer=$1
  systemctl is-enabled --quiet "$timer" ||
    fail "timer_not_enabled:$timer"
  systemctl is-active --quiet "$timer" ||
    fail "timer_not_active:$timer"
}

require_timer_enabled_inactive() {
  local timer=$1
  systemctl is-enabled --quiet "$timer" ||
    fail "timer_not_enabled:$timer"
  [[ "$(systemctl show -p ActiveState --value "$timer")" == \
     "inactive" ]] ||
    fail "timer_not_inactive:$timer"
}

wait_for_oneshot_idle_success() {
  local service=$1 timeout_seconds=$2 waited=0 active_state
  while true; do
    active_state="$(
      systemctl show -p ActiveState --value "$service"
    )"
    case "$active_state" in
      inactive) break ;;
      active|activating)
        (( waited < timeout_seconds )) ||
          fail "oneshot_wait_timeout:$service"
        sleep 5
        waited=$((waited + 5))
        ;;
      *)
        fail "oneshot_unexpected_state:$service:$active_state"
        ;;
    esac
  done
  [[ "$(systemctl show -p Result --value "$service")" == \
     "success" ]] ||
    fail "oneshot_last_result_not_success:$service"
}

validate_shadow_contract() {
  systemctl is-active --quiet "$EXPECTED_SHADOW_UNIT" ||
    fail "current_shadow_not_active"
  systemctl is-active --quiet "$FAST_LIVE_SOURCE" &&
    fail "fast_live_source_active"
  systemctl is-enabled --quiet "$FAST_LIVE_SOURCE" &&
    fail "fast_live_source_enabled"
  [[ -f "$EXPECTED_EVIDENCE" && ! -L "$EXPECTED_EVIDENCE" ]] ||
    fail "shadow_evidence_missing_or_invalid"

  "$REPO/.venv/bin/python" - \
    "$EXPECTED_EVIDENCE" \
    "$EXPECTED_MODEL_SHA256" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
expected_model = sys.argv[2]
starts = []
for raw in path.read_text(encoding="utf-8").splitlines():
    if not raw.startswith("{"):
        continue
    try:
        row = json.loads(raw)
    except json.JSONDecodeError:
        continue
    if (
        isinstance(row, dict)
        and row.get("event") == "v4_fresh_book_shadow_started"
    ):
        starts.append(row)
if len(starts) != 1:
    raise SystemExit(
        f"expected exactly one shadow start record, got {len(starts)}"
    )
start = starts[0]
if start.get("model_sha256") != expected_model:
    raise SystemExit("shadow model digest mismatch")
if start.get("database_read_only") is not True:
    raise SystemExit("shadow database_read_only mismatch")
if start.get("order_submission_enabled") is not False:
    raise SystemExit("shadow order submission unexpectedly enabled")
if start.get("holdout_labels_read") is not False:
    raise SystemExit("shadow holdout label contract mismatch")
PY
}

run_storage_health() {
  local destination=$1
  if ! sudo -u bp \
    "$REPO/.venv/bin/python" \
    "$REPO/scripts/storage_maintenance.py" \
    disk-health \
    --env-file "$ENV_FILE" \
    > "$destination"
  then
    cat "$destination" >&2 || true
    fail "storage_health_command_failed"
  fi

  "$REPO/.venv/bin/python" - "$destination" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(
    Path(sys.argv[1]).read_text(encoding="utf-8")
)
if payload.get("status") != "ok":
    raise SystemExit("storage health is not ok")
if payload.get("storage_mode") != "partitioned":
    raise SystemExit("storage mode is not partitioned")
guards = payload.get("guards") or {}
for name in (
    "maintenance_fresh",
    "current_partition_present",
    "retention_current",
):
    if guards.get(name) is not True:
        raise SystemExit(f"storage guard not true: {name}")
PY
}

restore_generated_files() {
  [[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]] || return 0
  if [[ -f "$BACKUP_DIR/next-env.d.ts" ]]; then
    cp -a \
      "$BACKUP_DIR/next-env.d.ts" \
      "$REPO/apps/dashboard/next-env.d.ts"
  fi
  if [[ -f "$BACKUP_DIR/tsconfig.json" ]]; then
    cp -a \
      "$BACKUP_DIR/tsconfig.json" \
      "$REPO/apps/dashboard/tsconfig.json"
  fi
}

rollback() {
  set +e
  echo "PHASE14_V4_SOURCE_LOOKUP_INDEX_ROLLBACK=START" >&2

  if (( MIGRATION_COMPLETE == 1 )) && \
     [[ -s "$MIGRATION_TMP" ]] && \
     [[ -r "$MIGRATION" ]]
  then
    sudo -u bp env \
      PYTHONDONTWRITEBYTECODE=1 \
      PYTHONPATH="$REPO/src" \
      "$REPO/.venv/bin/python" \
      "$MIGRATION" \
      --env-file "$ENV_FILE" \
      --rollback-evidence "$MIGRATION_TMP" \
      >&2 || true
  fi

  git -C "$REPO" checkout \
    --detach \
    --force \
    "$FROM_HEAD" \
    >/dev/null 2>&1 || true
  restore_generated_files
  systemctl start "$MAINTENANCE_TIMER" >/dev/null 2>&1 || true

  echo "DEPLOYED_HEAD=$(
    git -C "$REPO" rev-parse HEAD 2>/dev/null || true
  )" >&2
  echo "RECORDER_ACTIVE=$(
    systemctl is-active "$RECORDER_UNIT" 2>/dev/null || true
  )" >&2
  echo "SHADOW_ACTIVE=$(
    systemctl is-active "$EXPECTED_SHADOW_UNIT" 2>/dev/null || true
  )" >&2
  echo "MAINTENANCE_TIMER_ACTIVE=$(
    systemctl is-active "$MAINTENANCE_TIMER" 2>/dev/null || true
  )" >&2
  echo "PHASE14_V4_SOURCE_LOOKUP_INDEX_ROLLBACK=COMPLETE" >&2
  set -e
}

cleanup() {
  local rc=$?
  trap - EXIT
  set +e
  if (( rc != 0 && MUTATION_STARTED == 1 && ROLLBACK_ARMED == 1 )); then
    rollback
  fi
  rm -f \
    "$MIGRATION_TMP" \
    "$DISK_BEFORE" \
    "$DISK_AFTER"
  [[ -n "$BACKUP_DIR" ]] && rm -rf "$BACKUP_DIR"
  set -e
  exit "$rc"
}
trap cleanup EXIT

[[ -d "$REPO/.git" ]] ||
  fail "deployed_repo_missing"
[[ -x "$REPO/.venv/bin/python" ]] ||
  fail "python_runtime_missing"
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$FROM_HEAD" ]] ||
  fail "unexpected_deployed_head"

validate_deployed_checkout
require_research_zero_money
require_recorder_batch100
validate_shadow_contract

systemctl is-active --quiet "$RECORDER_UNIT" ||
  fail "recorder_not_active"
require_timer_active_enabled "$MAINTENANCE_TIMER"
require_timer_active_enabled "$DISK_HEALTH_TIMER"
wait_for_oneshot_idle_success "$MAINTENANCE_SERVICE" 3600
wait_for_oneshot_idle_success "$DISK_HEALTH_SERVICE" 60

DISK_BEFORE="$(
  mktemp /var/tmp/bp-v4-source-lookup-disk-before.XXXXXX.json
)"
run_storage_health "$DISK_BEFORE"

SHADOW_PID="$(
  systemctl show -p MainPID --value "$EXPECTED_SHADOW_UNIT"
)"
SHADOW_RESTARTS="$(
  systemctl show -p NRestarts --value "$EXPECTED_SHADOW_UNIT"
)"
RECORDER_PID="$(
  systemctl show -p MainPID --value "$RECORDER_UNIT"
)"
RECORDER_RESTARTS="$(
  systemctl show -p NRestarts --value "$RECORDER_UNIT"
)"

git -C "$REPO" fetch \
  --quiet \
  origin \
  "$CANDIDATE_BRANCH:refs/remotes/origin/$CANDIDATE_BRANCH"

[[ "$(
  git -C "$REPO" rev-parse \
    "refs/remotes/origin/$CANDIDATE_BRANCH"
)" == "$CANDIDATE_HEAD" ]] ||
  fail "remote_candidate_changed"

git -C "$REPO" merge-base \
  --is-ancestor \
  "$FROM_HEAD" \
  "$CANDIDATE_HEAD" ||
  fail "candidate_not_descendant_of_deployed_head"

EXPECTED_DIFF="$(
  printf '%s\n' \
    scripts/deploy/phase14_v4_source_lookup_index_migration.py \
    src/bp_engine/storage/partitioned_raw.py \
    tests/storage/test_partitioned_raw_postgres.py |
    sort
)"
ACTUAL_DIFF="$(
  git -C "$REPO" diff \
    --name-only \
    "$FROM_HEAD" \
    "$CANDIDATE_HEAD" |
    sort
)"
[[ "$ACTUAL_DIFF" == "$EXPECTED_DIFF" ]] ||
  fail "candidate_scope_mismatch"

BACKUP_DIR="$(
  mktemp -d /var/tmp/bp-v4-source-lookup-backup.XXXXXX
)"
if ! git -C "$REPO" diff \
  --quiet \
  HEAD \
  -- apps/dashboard/next-env.d.ts
then
  cp -a \
    "$REPO/apps/dashboard/next-env.d.ts" \
    "$BACKUP_DIR/next-env.d.ts"
fi
if ! git -C "$REPO" diff \
  --quiet \
  HEAD \
  -- apps/dashboard/tsconfig.json
then
  cp -a \
    "$REPO/apps/dashboard/tsconfig.json" \
    "$BACKUP_DIR/tsconfig.json"
fi

MUTATION_STARTED=1
ROLLBACK_ARMED=1

systemctl stop "$MAINTENANCE_TIMER"
require_timer_enabled_inactive "$MAINTENANCE_TIMER"
wait_for_oneshot_idle_success "$MAINTENANCE_SERVICE" 3600

git -C "$REPO" checkout \
  --detach \
  --force \
  "$CANDIDATE_HEAD"
restore_generated_files

[[ "$(git -C "$REPO" rev-parse HEAD)" == "$CANDIDATE_HEAD" ]] ||
  fail "candidate_checkout_failed"
validate_deployed_checkout

grep -Fq \
  '_V4_SOURCE_LOOKUP_INDEX_SUFFIX = "_v4_source_lookup_idx"' \
  "$REPO/src/bp_engine/storage/partitioned_raw.py" ||
  fail "candidate_lookup_index_contract_missing"

[[ -r "$MIGRATION" ]] ||
  fail "candidate_migration_missing"

MIGRATION_TMP="$(
  mktemp /var/tmp/bp-v4-source-lookup-migration.XXXXXX.json
)"

if ! sudo -u bp env \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="$REPO/src" \
  "$REPO/.venv/bin/python" \
  "$MIGRATION" \
  --env-file "$ENV_FILE" \
  > "$MIGRATION_TMP"
then
  cat "$MIGRATION_TMP" >&2 || true
  fail "index_migration_failed"
fi

grep -Fq \
  'PHASE14_V4_SOURCE_LOOKUP_INDEX_MIGRATION=PASS' \
  "$MIGRATION_TMP" ||
  fail "index_migration_pass_marker_missing"
MIGRATION_COMPLETE=1

require_research_zero_money
require_recorder_batch100
validate_shadow_contract

[[ "$(
  systemctl show -p MainPID --value "$EXPECTED_SHADOW_UNIT"
)" == "$SHADOW_PID" ]] ||
  fail "shadow_pid_changed"
[[ "$(
  systemctl show -p NRestarts --value "$EXPECTED_SHADOW_UNIT"
)" == "$SHADOW_RESTARTS" ]] ||
  fail "shadow_restarted"
[[ "$(
  systemctl show -p MainPID --value "$RECORDER_UNIT"
)" == "$RECORDER_PID" ]] ||
  fail "recorder_pid_changed"
[[ "$(
  systemctl show -p NRestarts --value "$RECORDER_UNIT"
)" == "$RECORDER_RESTARTS" ]] ||
  fail "recorder_restarted"

DISK_AFTER="$(
  mktemp /var/tmp/bp-v4-source-lookup-disk-after.XXXXXX.json
)"
run_storage_health "$DISK_AFTER"

systemctl start "$MAINTENANCE_TIMER"
require_timer_active_enabled "$MAINTENANCE_TIMER"

require_research_zero_money
validate_shadow_contract

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE_PATH="$EVIDENCE_DIR/phase14-v4-source-lookup-index-$STAMP.txt"
install -o bp -g bp -m 0640 \
  "$MIGRATION_TMP" \
  "$EVIDENCE_PATH"
sync -f "$EVIDENCE_PATH"

ROLLBACK_ARMED=0

cat "$MIGRATION_TMP"
echo "EVIDENCE_PATH=$EVIDENCE_PATH"
echo "PHASE14_V4_SOURCE_LOOKUP_INDEX_ROLLOUT_GATE=PASS"
echo "FROM_HEAD=$FROM_HEAD"
echo "DEPLOYED_HEAD=$(git -C "$REPO" rev-parse HEAD)"
echo "RECORDER_ACTIVE=true"
echo "RECORDER_RESTARTED=false"
echo "SHADOW_RUN_ID=$EXPECTED_SHADOW_RUN_ID"
echo "SHADOW_UNIT_ACTIVE=true"
echo "SHADOW_RESTARTED=false"
echo "MAINTENANCE_TIMER_ACTIVE=active"
echo "LIVE_TRADING_ENABLED=false"
echo "MAX_TRADE_SIZE_USD=0"
echo "MAX_DAILY_LOSS_USD=0"
REMOTE

REMOTE_B64="$(printf '%s' "$REMOTE_SCRIPT" | base64 | tr -d '\n')"

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "HELPER_HEAD=$HELPER_HEAD"
echo "FROM_HEAD=$FROM_HEAD"
echo "CANDIDATE_HEAD=$CANDIDATE_HEAD"
echo "EXPECTED_SHADOW_RUN_ID=$EXPECTED_SHADOW_RUN_ID"
echo "INDEX_SUFFIX=$INDEX_SUFFIX"
echo "This helper checks out the narrow storage candidate and creates"
echo "child-local V4 lookup indexes concurrently while recorder and"
echo "the active zero-money V4 shadow remain running."
echo "It stops only the storage-maintenance timer during the mutation."
echo "Failure rolls back newly created indexes and the checkout."

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet \
  --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo bash"
