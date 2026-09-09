#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT='project-4397f2c0-7098-4c1c-abb'
ZONE='us-east1-c'
VM='bp-recorder'
HELPER_HEAD="${PHASE14_RECORDER_DEADLOCK_RECOVERY_HELPER_HEAD:-}"

FROM_HEAD='895c6bd2f9409f16bf5d544b26b30e20ecbfe43a'
CANDIDATE_BRANCH='ops/phase14-storage-deadlock-recovery-candidate'
CANDIDATE_HEAD='e9c7afc1536880e4612cb6e3d1a7282fa37c69f5'
ENV_FILE='/etc/bp/bp.env'
STORAGE_EVIDENCE='/mnt/bp-data/evidence/phase14-partitioned-storage-rollout-20260909T070219Z.json'
STORAGE_EVIDENCE_SHA256='f33a28f5306e46c509b0000a176d226c079aa2d160d595095ca228118542ce19'
RUNTIME_PATH='src/bp_engine/storage/partitioned_raw.py'
TEST_PATH='tests/storage/test_partitioned_raw_postgres.py'

fail_local() {
  echo "PHASE14_RECORDER_DEADLOCK_RECOVERY_GATE=FAIL" >&2
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

EXPECTED_DIFF=$(printf '%s\n' "$RUNTIME_PATH" "$TEST_PATH" | sort)
ACTUAL_DIFF=$(git diff --name-only "$FROM_HEAD" "$CANDIDATE_HEAD" | sort)
[[ "$ACTUAL_DIFF" == "$EXPECTED_DIFF" ]] || fail_local "candidate_scope_mismatch"

MAIN_RUNTIME_BLOB=$(git rev-parse "$HELPER_HEAD:$RUNTIME_PATH")
CANDIDATE_RUNTIME_BLOB=$(git rev-parse "$CANDIDATE_HEAD:$RUNTIME_PATH")
[[ "$CANDIDATE_RUNTIME_BLOB" == "$MAIN_RUNTIME_BLOB" ]] || fail_local "candidate_runtime_blob_not_exact_main"

MAIN_TEST_BLOB=$(git rev-parse "$HELPER_HEAD:$TEST_PATH")
CANDIDATE_TEST_BLOB=$(git rev-parse "$CANDIDATE_HEAD:$TEST_PATH")
[[ "$CANDIDATE_TEST_BLOB" == "$MAIN_TEST_BLOB" ]] || fail_local "candidate_test_blob_not_exact_main"

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
printf -v RUNTIME_PATH_Q '%q' "$RUNTIME_PATH"
printf -v TEST_PATH_Q '%q' "$TEST_PATH"
printf -v RUNTIME_BLOB_Q '%q' "$CANDIDATE_RUNTIME_BLOB"
printf -v TEST_BLOB_Q '%q' "$CANDIDATE_TEST_BLOB"

REMOTE_SCRIPT=$(cat <<'REMOTE'
set -Eeuo pipefail

HELPER_HEAD="${PHASE14_RECORDER_DEADLOCK_RECOVERY_HELPER_HEAD:?}"
FROM_HEAD="${PHASE14_RECORDER_DEADLOCK_RECOVERY_FROM_HEAD:?}"
CANDIDATE_BRANCH="${PHASE14_RECORDER_DEADLOCK_RECOVERY_CANDIDATE_BRANCH:?}"
CANDIDATE_HEAD="${PHASE14_RECORDER_DEADLOCK_RECOVERY_CANDIDATE_HEAD:?}"
ENV_FILE="${PHASE14_RECORDER_DEADLOCK_RECOVERY_ENV_FILE:?}"
STORAGE_EVIDENCE="${PHASE14_RECORDER_DEADLOCK_RECOVERY_STORAGE_EVIDENCE:?}"
STORAGE_EVIDENCE_SHA256="${PHASE14_RECORDER_DEADLOCK_RECOVERY_STORAGE_EVIDENCE_SHA256:?}"
RUNTIME_PATH="${PHASE14_RECORDER_DEADLOCK_RECOVERY_RUNTIME_PATH:?}"
TEST_PATH="${PHASE14_RECORDER_DEADLOCK_RECOVERY_TEST_PATH:?}"
EXPECTED_RUNTIME_BLOB="${PHASE14_RECORDER_DEADLOCK_RECOVERY_RUNTIME_BLOB:?}"
EXPECTED_TEST_BLOB="${PHASE14_RECORDER_DEADLOCK_RECOVERY_TEST_BLOB:?}"

REPO=/opt/bp
SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env
EVIDENCE_DIR=/var/lib/bp/evidence
RECORDER_UNIT=bp-recorder.service
MAINTENANCE_SERVICE=bp-storage-maintenance.service
MAINTENANCE_TIMER=bp-storage-maintenance.timer
DISK_HEALTH_SERVICE=bp-storage-disk-health.service
DISK_HEALTH_TIMER=bp-storage-disk-health.timer
V2_SERVICE=bp-v2-forward-coverage.service
V2_TIMER=bp-v2-forward-coverage.timer

REQUIRED_ACTIVE_SERVICES=(
  bp-postgres.service
  bp-dashboard-api.service
  bp-dashboard-web.service
  bp-paper-execution.service
  bp-live-predictor.service
  bp-prospective-outcomes.service
)

ROLLBACK_ARMED=0
DISK_BEFORE=''
DISK_AFTER_PRESTART=''
DISK_AFTER_ACTIVE=''
SOAK_FILE=''
SNAPSHOT_FILE=''
EVIDENCE_TMP=''
EVIDENCE_PATH=''
HOLDOUT_FINGERPRINT_BEFORE=''
MAIN_PID=''
CONFIG_WORKERS=''
RESTARTS_BASELINE=''
RESTARTS_FINAL=''

fail() {
  echo "PHASE14_RECORDER_DEADLOCK_RECOVERY_GATE=FAIL" >&2
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
  local actual expected runtime_blob test_blob
  git -C "$REPO" merge-base --is-ancestor "$FROM_HEAD" "$CANDIDATE_HEAD" || fail "candidate_not_descendant_of_deployed_head"
  expected=$(printf '%s\n' "$RUNTIME_PATH" "$TEST_PATH" | sort)
  actual=$(git -C "$REPO" diff --name-only "$FROM_HEAD" "$CANDIDATE_HEAD" | sort)
  [[ "$actual" == "$expected" ]] || fail "candidate_scope_mismatch"

  runtime_blob=$(git -C "$REPO" rev-parse "$CANDIDATE_HEAD:$RUNTIME_PATH")
  test_blob=$(git -C "$REPO" rev-parse "$CANDIDATE_HEAD:$TEST_PATH")
  [[ "$runtime_blob" == "$EXPECTED_RUNTIME_BLOB" ]] || fail "candidate_runtime_blob_mismatch"
  [[ "$test_blob" == "$EXPECTED_TEST_BLOB" ]] || fail "candidate_test_blob_mismatch"
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
  validate_unit_file "$V2_SERVICE" "deploy/$V2_SERVICE"
  validate_unit_file "$V2_TIMER" "deploy/$V2_TIMER"

  local environment_files
  environment_files=$(systemctl show -p EnvironmentFiles --value "$RECORDER_UNIT")
  [[ "$environment_files" == "$ENV_FILE (ignore_errors=no)" ]] || fail "recorder_environment_file_mismatch"
}

require_research_zero_money() {
  local mode live trade loss safe_mode safe_live safe_trade safe_loss
  [[ -r "$ENV_FILE" ]] || fail "environment_file_missing"
  [[ -r "$SAFETY_FILE" ]] || fail "prospective_safety_file_missing"

  mode=$(read_env "$ENV_FILE" MODE)
  live=$(read_env "$ENV_FILE" LIVE_TRADING_ENABLED)
  trade=$(read_env "$ENV_FILE" MAX_TRADE_SIZE_USD)
  loss=$(read_env "$ENV_FILE" MAX_DAILY_LOSS_USD)
  safe_mode=$(read_env "$SAFETY_FILE" MODE)
  safe_live=$(read_env "$SAFETY_FILE" LIVE_TRADING_ENABLED)
  safe_trade=$(read_env "$SAFETY_FILE" MAX_TRADE_SIZE_USD)
  safe_loss=$(read_env "$SAFETY_FILE" MAX_DAILY_LOSS_USD)

  [[ "$mode" == "research" ]] || fail "mode_not_research"
  [[ "$live" == "false" ]] || fail "live_trading_enabled"
  [[ "$trade" == "0" ]] || fail "max_trade_size_nonzero"
  [[ "$loss" == "0" ]] || fail "max_daily_loss_nonzero"
  [[ "$safe_mode" == "research" ]] || fail "safety_mode_not_research"
  [[ "$safe_live" == "false" ]] || fail "safety_live_trading_enabled"
  [[ "$safe_trade" == "0" ]] || fail "safety_max_trade_size_nonzero"
  [[ "$safe_loss" == "0" ]] || fail "safety_max_daily_loss_nonzero"
}

require_automatic_promotion_false() {
  "$REPO/.venv/bin/python" - "$REPO/PROJECT_STATE.json" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
values: list[object] = []


def walk(value: object) -> None:
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

read_recorder_config_workers() {
  env -u RECORDER_WRITER_WORKERS "$REPO/.venv/bin/python" - "$ENV_FILE" <<'PY'
from __future__ import annotations

import sys
from bp_engine.config import Settings

print(Settings(_env_file=sys.argv[1]).recorder_writer_workers)
PY
}

require_recorder_workers_four() {
  [[ $(grep -c '^RECORDER_WRITER_WORKERS=' "$ENV_FILE" || true) -eq 1 ]] || fail "writer_worker_setting_count_not_1"
  [[ "$(read_env "$ENV_FILE" RECORDER_WRITER_WORKERS)" == "4" ]] || fail "writer_workers_not_4"
  CONFIG_WORKERS=$(read_recorder_config_workers)
  [[ "$CONFIG_WORKERS" == "4" ]] || fail "recorder_config_worker_count_not_4"
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

require_oneshot_idle_success() {
  local service=$1
  local active result
  active=$(systemctl show -p ActiveState --value "$service")
  result=$(systemctl show -p Result --value "$service")
  [[ "$active" == "inactive" ]] || fail "oneshot_not_idle:$service:$active"
  [[ "$result" == "success" ]] || fail "oneshot_last_result_not_success:$service:$result"
}

run_storage_health() {
  local destination=$1
  if ! sudo -u bp "$REPO/.venv/bin/python" "$REPO/scripts/storage_maintenance.py" disk-health --env-file "$ENV_FILE" > "$destination"; then
    cat "$destination" >&2 || true
    fail "storage_health_command_failed"
  fi

  "$REPO/.venv/bin/python" - "$destination" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("status") != "ok":
    raise SystemExit(f"storage health status is not ok: {payload.get('status')!r}")
if payload.get("storage_mode") != "partitioned":
    raise SystemExit(f"storage mode is not partitioned: {payload.get('storage_mode')!r}")
guards = payload.get("guards") or {}
required = ("maintenance_fresh", "current_partition_present", "retention_current")
missing = [name for name in required if guards.get(name) is not True]
if missing:
    raise SystemExit(f"storage guards not satisfied: {missing}")
PY
}

run_maintenance_cycle() {
  local label=$1
  systemctl reset-failed "$MAINTENANCE_SERVICE" >/dev/null 2>&1 || true
  if ! systemctl start "$MAINTENANCE_SERVICE"; then
    journalctl -u "$MAINTENANCE_SERVICE" -n 120 --no-pager >&2 || true
    fail "maintenance_cycle_failed:$label"
  fi
  [[ "$(systemctl show -p Result --value "$MAINTENANCE_SERVICE")" == "success" ]] || fail "maintenance_result_not_success:$label"
  [[ "$(systemctl show -p ExecMainStatus --value "$MAINTENANCE_SERVICE")" == "0" ]] || fail "maintenance_exit_nonzero:$label"
}

run_soak_gate() {
  SOAK_FILE=$(mktemp /var/tmp/bp-phase14-recorder-deadlock-recovery-soak.XXXXXX.json)
  if ! sudo -u bp bash -c       'set -a; source "$1"; set +a; exec "$2" "$3" --hours 0.01 --minimum-hours 0.008'       _ "$ENV_FILE" "$REPO/.venv/bin/python" "$REPO/scripts/soak_report.py" > "$SOAK_FILE"; then
    cat "$SOAK_FILE" >&2 || true
    fail "natural_load_soak_failed"
  fi

  "$REPO/.venv/bin/python" - "$SOAK_FILE" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
required = {"polymarket/market", "bybit/spot", "bybit/linear", "coinbase/spot"}
if payload.get("passed") is not True:
    raise SystemExit(f"soak report did not pass: {payload.get('failures')}")
feeds = payload.get("feeds") or {}
missing = sorted(
    label
    for label in required
    if int((feeds.get(label) or {}).get("event_count", 0)) <= 0
)
if missing:
    raise SystemExit(f"required feeds missing post-recovery events: {missing}")
for label in required:
    incidents = (payload.get("incidents") or {}).get(label) or {}
    if int(incidents.get("backpressure", 0)) != 0:
        raise SystemExit(f"required feed recorded backpressure: {label}")
PY
}

verify_dashboard_safety() {
  SNAPSHOT_FILE=$(mktemp /var/tmp/bp-phase14-recorder-deadlock-recovery-snapshot.XXXXXX.json)
  curl -fsS http://127.0.0.1:8787/api/v1/snapshot > "$SNAPSHOT_FILE" || fail "dashboard_snapshot_unavailable"

  "$REPO/.venv/bin/python" - "$SNAPSHOT_FILE" <<'PY'
from __future__ import annotations

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
if mode.get("paper_execution_available") is not True:
    raise SystemExit("dashboard paper execution unavailable")
PY
}

gate_b_fingerprint() {
  "$REPO/.venv/bin/python" - "$EVIDENCE_DIR" <<'PY'
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

root = Path(sys.argv[1])
digest = hashlib.sha256()
if root.exists():
    names = {"plan.json", "selection.json", "holdout.json", "summary.json"}
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

rollback() {
  set +e
  echo "PHASE14_RECORDER_DEADLOCK_RECOVERY_ROLLBACK=START" >&2

  systemctl stop "$RECORDER_UNIT" >/dev/null 2>&1 || true
  systemctl stop "$V2_TIMER" "$MAINTENANCE_TIMER" "$DISK_HEALTH_TIMER" >/dev/null 2>&1 || true
  systemctl stop "$V2_SERVICE" "$MAINTENANCE_SERVICE" "$DISK_HEALTH_SERVICE" >/dev/null 2>&1 || true

  if [[ "$(git -C "$REPO" rev-parse HEAD 2>/dev/null || true)" != "$FROM_HEAD" ]]; then
    git -C "$REPO" checkout --detach "$FROM_HEAD" >/dev/null 2>&1 || true
  fi

  systemctl start "$DISK_HEALTH_TIMER" >/dev/null 2>&1 || true
  systemctl start "$MAINTENANCE_TIMER" >/dev/null 2>&1 || true
  systemctl start "$V2_TIMER" >/dev/null 2>&1 || true

  echo "DEPLOYED_HEAD=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || true)" >&2
  echo "RECORDER_ACTIVE=$(systemctl is-active "$RECORDER_UNIT" 2>/dev/null || true)" >&2
  echo "MAINTENANCE_TIMER_ACTIVE=$(systemctl is-active "$MAINTENANCE_TIMER" 2>/dev/null || true)" >&2
  echo "DISK_HEALTH_TIMER_ACTIVE=$(systemctl is-active "$DISK_HEALTH_TIMER" 2>/dev/null || true)" >&2
  echo "V2_TIMER_ACTIVE=$(systemctl is-active "$V2_TIMER" 2>/dev/null || true)" >&2
  echo "PHASE14_RECORDER_DEADLOCK_RECOVERY_ROLLBACK=COMPLETE" >&2
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
  [[ -n "$DISK_AFTER_PRESTART" ]] && rm -f "$DISK_AFTER_PRESTART"
  [[ -n "$DISK_AFTER_ACTIVE" ]] && rm -f "$DISK_AFTER_ACTIVE"
  [[ -n "$SOAK_FILE" ]] && rm -f "$SOAK_FILE"
  [[ -n "$SNAPSHOT_FILE" ]] && rm -f "$SNAPSHOT_FILE"
  [[ -n "$EVIDENCE_TMP" ]] && rm -f "$EVIDENCE_TMP"
  set -e
  exit "$rc"
}
trap cleanup EXIT

[[ -d "$REPO/.git" ]] || fail "deployed_repo_missing"
[[ -x "$REPO/.venv/bin/python" ]] || fail "python_runtime_missing"
[[ -r "$ENV_FILE" ]] || fail "environment_file_missing"
[[ -r "$STORAGE_EVIDENCE" ]] || fail "storage_evidence_missing"
[[ "$(sha256sum "$STORAGE_EVIDENCE" | awk '{print $1}')" == "$STORAGE_EVIDENCE_SHA256" ]] || fail "storage_evidence_sha256_mismatch"
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$FROM_HEAD" ]] || fail "unexpected_deployed_head"
validate_deployed_checkout
require_research_zero_money
require_automatic_promotion_false
require_recorder_workers_four
require_core_services_active
validate_unit_contracts

[[ "$(systemctl show -p ActiveState --value "$RECORDER_UNIT")" == "inactive" ]] || fail "recorder_not_inactive"
require_timer_active_enabled "$MAINTENANCE_TIMER"
require_timer_active_enabled "$DISK_HEALTH_TIMER"
require_timer_active_enabled "$V2_TIMER"
require_oneshot_idle_success "$MAINTENANCE_SERVICE"
require_oneshot_idle_success "$DISK_HEALTH_SERVICE"
require_oneshot_idle_success "$V2_SERVICE"

DISK_BEFORE=$(mktemp /var/tmp/bp-phase14-recorder-deadlock-recovery-disk-before.XXXXXX.json)
run_storage_health "$DISK_BEFORE"
HOLDOUT_FINGERPRINT_BEFORE=$(gate_b_fingerprint)

git -C "$REPO" fetch --quiet origin "refs/heads/$CANDIDATE_BRANCH:refs/remotes/origin/$CANDIDATE_BRANCH"
[[ "$(git -C "$REPO" rev-parse "refs/remotes/origin/$CANDIDATE_BRANCH")" == "$CANDIDATE_HEAD" ]] || fail "candidate_branch_changed"
validate_candidate_scope

ROLLBACK_ARMED=1
systemctl stop "$V2_TIMER" "$MAINTENANCE_TIMER" "$DISK_HEALTH_TIMER"
require_oneshot_idle_success "$MAINTENANCE_SERVICE"
require_oneshot_idle_success "$DISK_HEALTH_SERVICE"
require_oneshot_idle_success "$V2_SERVICE"

git -C "$REPO" checkout --detach "$CANDIDATE_HEAD" >/dev/null
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$CANDIDATE_HEAD" ]] || fail "candidate_checkout_failed"
validate_deployed_checkout
validate_candidate_scope
validate_unit_contracts
require_research_zero_money
require_automatic_promotion_false
require_recorder_workers_four

run_storage_health "$DISK_BEFORE"
run_maintenance_cycle "prestart"

DISK_AFTER_PRESTART=$(mktemp /var/tmp/bp-phase14-recorder-deadlock-recovery-disk-prestart.XXXXXX.json)
run_storage_health "$DISK_AFTER_PRESTART"

systemctl start "$DISK_HEALTH_TIMER"
systemctl is-active --quiet "$DISK_HEALTH_TIMER" || fail "disk_health_timer_not_active_before_recorder_start"

systemctl reset-failed "$RECORDER_UNIT" >/dev/null 2>&1 || true
systemctl start "$RECORDER_UNIT"
for _ in $(seq 1 45); do
  systemctl is-active --quiet "$RECORDER_UNIT" && break
  sleep 1
done
systemctl is-active --quiet "$RECORDER_UNIT" || fail "recorder_not_active_after_start"

MAIN_PID=$(systemctl show -p MainPID --value "$RECORDER_UNIT")
[[ "$MAIN_PID" =~ ^[1-9][0-9]*$ ]] || fail "recorder_main_pid_invalid"
RESTARTS_BASELINE=$(systemctl show -p NRestarts --value "$RECORDER_UNIT")
[[ "$RESTARTS_BASELINE" =~ ^[0-9]+$ ]] || fail "recorder_restart_count_invalid"

sleep 45
systemctl is-active --quiet "$RECORDER_UNIT" || fail "recorder_not_active_after_stability_wait"
[[ "$(systemctl show -p MainPID --value "$RECORDER_UNIT")" == "$MAIN_PID" ]] || fail "recorder_main_pid_changed_before_maintenance"
require_recorder_workers_four
require_core_services_active
require_research_zero_money

run_maintenance_cycle "active_recorder"
systemctl is-active --quiet "$RECORDER_UNIT" || fail "recorder_not_active_after_maintenance"
[[ "$(systemctl show -p MainPID --value "$RECORDER_UNIT")" == "$MAIN_PID" ]] || fail "recorder_main_pid_changed_during_maintenance"
RESTARTS_FINAL=$(systemctl show -p NRestarts --value "$RECORDER_UNIT")
[[ "$RESTARTS_FINAL" == "$RESTARTS_BASELINE" ]] || fail "recorder_restarted_during_maintenance"

run_soak_gate
verify_dashboard_safety

DISK_AFTER_ACTIVE=$(mktemp /var/tmp/bp-phase14-recorder-deadlock-recovery-disk-after.XXXXXX.json)
run_storage_health "$DISK_AFTER_ACTIVE"

systemctl start "$MAINTENANCE_TIMER"
systemctl start "$V2_TIMER"
require_timer_active_enabled "$DISK_HEALTH_TIMER"
require_timer_active_enabled "$MAINTENANCE_TIMER"
require_timer_active_enabled "$V2_TIMER"

require_core_services_active
systemctl is-active --quiet "$RECORDER_UNIT" || fail "recorder_not_active_at_acceptance"
require_research_zero_money
require_automatic_promotion_false
require_recorder_workers_four
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$CANDIDATE_HEAD" ]] || fail "deployed_head_changed"
validate_deployed_checkout

HOLDOUT_FINGERPRINT_AFTER=$(gate_b_fingerprint)
[[ "$HOLDOUT_FINGERPRINT_AFTER" == "$HOLDOUT_FINGERPRINT_BEFORE" ]] || fail "gate_b_artifacts_changed"

install -d -o bp -g bp -m 0750 "$EVIDENCE_DIR"
EVIDENCE_TMP=$(mktemp /var/tmp/bp-phase14-recorder-deadlock-recovery-evidence.XXXXXX.json)
"$REPO/.venv/bin/python" -   "$DISK_BEFORE"   "$DISK_AFTER_PRESTART"   "$DISK_AFTER_ACTIVE"   "$SOAK_FILE"   "$EVIDENCE_TMP"   "$HELPER_HEAD"   "$FROM_HEAD"   "$CANDIDATE_BRANCH"   "$CANDIDATE_HEAD"   "$STORAGE_EVIDENCE"   "$STORAGE_EVIDENCE_SHA256"   "$EXPECTED_RUNTIME_BLOB"   "$EXPECTED_TEST_BLOB"   "$MAIN_PID"   "$CONFIG_WORKERS"   "$RESTARTS_BASELINE"   "$RESTARTS_FINAL"   "$HOLDOUT_FINGERPRINT_AFTER" <<'PY'
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

(
    disk_before_path,
    disk_prestart_path,
    disk_after_path,
    soak_path,
    output_path,
    helper_head,
    from_head,
    candidate_branch,
    candidate_head,
    storage_evidence,
    storage_evidence_sha256,
    runtime_blob,
    test_blob,
    main_pid,
    config_workers,
    restarts_baseline,
    restarts_final,
    gate_b_fingerprint,
) = sys.argv[1:]

payload = {
    "verdict": "PASS",
    "recorded_at": datetime.now(UTC).isoformat(),
    "helper_head": helper_head,
    "from_head": from_head,
    "candidate_branch": candidate_branch,
    "deployed_head": candidate_head,
    "storage_evidence": storage_evidence,
    "storage_evidence_sha256": storage_evidence_sha256,
    "candidate_scope": {
        "runtime_path": "src/bp_engine/storage/partitioned_raw.py",
        "runtime_blob": runtime_blob,
        "test_path": "tests/storage/test_partitioned_raw_postgres.py",
        "test_blob": test_blob,
        "runtime_files_changed": 1,
    },
    "recorder": {
        "active": True,
        "main_pid": int(main_pid),
        "config_workers": int(config_workers),
        "restarts_before_active_maintenance": int(restarts_baseline),
        "restarts_after_active_maintenance": int(restarts_final),
    },
    "maintenance": {
        "prestart_cycle_result": "success",
        "active_recorder_cycle_result": "success",
    },
    "storage": {
        "before": json.loads(Path(disk_before_path).read_text(encoding="utf-8")),
        "after_prestart": json.loads(Path(disk_prestart_path).read_text(encoding="utf-8")),
        "after_active_maintenance": json.loads(Path(disk_after_path).read_text(encoding="utf-8")),
    },
    "soak": json.loads(Path(soak_path).read_text(encoding="utf-8")),
    "timers": {
        "storage_maintenance_active": True,
        "storage_disk_health_active": True,
        "v2_forward_coverage_active": True,
    },
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
        "holdout_touched": False,
    },
}
Path(output_path).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
EVIDENCE_PATH="$EVIDENCE_DIR/phase14-recorder-deadlock-recovery-$STAMP.json"
install -o bp -g bp -m 0640 "$EVIDENCE_TMP" "$EVIDENCE_PATH"
ROLLBACK_ARMED=0

cat "$EVIDENCE_PATH"
echo "EVIDENCE_FILE=$EVIDENCE_PATH"
echo "PHASE14_RECORDER_DEADLOCK_RECOVERY_GATE=PASS"
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
echo "STORAGE_EVIDENCE=$STORAGE_EVIDENCE"
echo "STORAGE_EVIDENCE_SHA256=$STORAGE_EVIDENCE_SHA256"
echo "RUNTIME_BLOB=$CANDIDATE_RUNTIME_BLOB"
echo "TEST_BLOB=$CANDIDATE_TEST_BLOB"
echo "This helper performs a production checkout mutation and starts bp-recorder.service."
echo "Run only after separate explicit production authorization."

gcloud compute ssh "$VM"   --project="$PROJECT"   --zone="$ZONE"   --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo env PHASE14_RECORDER_DEADLOCK_RECOVERY_HELPER_HEAD=$HELPER_HEAD_Q PHASE14_RECORDER_DEADLOCK_RECOVERY_FROM_HEAD=$FROM_HEAD_Q PHASE14_RECORDER_DEADLOCK_RECOVERY_CANDIDATE_BRANCH=$CANDIDATE_BRANCH_Q PHASE14_RECORDER_DEADLOCK_RECOVERY_CANDIDATE_HEAD=$CANDIDATE_HEAD_Q PHASE14_RECORDER_DEADLOCK_RECOVERY_ENV_FILE=$ENV_FILE_Q PHASE14_RECORDER_DEADLOCK_RECOVERY_STORAGE_EVIDENCE=$STORAGE_EVIDENCE_Q PHASE14_RECORDER_DEADLOCK_RECOVERY_STORAGE_EVIDENCE_SHA256=$STORAGE_EVIDENCE_SHA256_Q PHASE14_RECORDER_DEADLOCK_RECOVERY_RUNTIME_PATH=$RUNTIME_PATH_Q PHASE14_RECORDER_DEADLOCK_RECOVERY_TEST_PATH=$TEST_PATH_Q PHASE14_RECORDER_DEADLOCK_RECOVERY_RUNTIME_BLOB=$RUNTIME_BLOB_Q PHASE14_RECORDER_DEADLOCK_RECOVERY_TEST_BLOB=$TEST_BLOB_Q bash"
