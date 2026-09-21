#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_RECORDER_V3_RECOVERY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_RECORDER_V3_RECOVERY_ZONE:-us-east1-c}"
VM="${PHASE14_RECORDER_V3_RECOVERY_VM:-bp-recorder}"
HELPER_HEAD="${PHASE14_RECORDER_V3_RECOVERY_HELPER_HEAD:-}"
DEPLOYED_HEAD="${PHASE14_RECORDER_V3_RECOVERY_DEPLOYED_HEAD:-7c3af78da1922a0e5187c24b799951130cc98887}"
APPROVAL="${PHASE14_RECORDER_V3_RECOVERY_APPROVAL:-}"

fail_local() {
  echo "PHASE14_RECORDER_V3_RECOVERY_GATE=FAIL" >&2
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

EXPECTED_APPROVAL="I_APPROVE_PHASE14_RECORDER_V3_RECOVERY:${HELPER_HEAD}:${DEPLOYED_HEAD}"
[[ "$APPROVAL" == "$EXPECTED_APPROVAL" ]] || fail_local "production_approval_mismatch"
command -v gcloud >/dev/null 2>&1 || fail_local "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . || fail_local "gcloud_auth_missing"
gcloud config set project "$PROJECT" >/dev/null

printf -v DEPLOYED_HEAD_Q '%q' "$DEPLOYED_HEAD"

read -r -d '' REMOTE_SCRIPT <<'REMOTE' || true
set -Eeuo pipefail
DEPLOYED_HEAD="${PHASE14_RECORDER_V3_RECOVERY_DEPLOYED_HEAD:?}"
REPO=/opt/bp
ENV_FILE=/etc/bp/bp.env
SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env
EVIDENCE_DIR=/var/lib/bp/evidence
V3_CURRENT=/var/lib/bp/runtime/v3-paper-current
V3_STATE=/var/lib/bp/v3-paper
RECORDER_UNIT=bp-recorder.service
V3_PREDICTOR=bp-v3-frozen-predictor.service
V3_EXECUTION=bp-v3-paper-execution.service
MAINTENANCE_SERVICE=bp-storage-maintenance.service
MAINTENANCE_TIMER=bp-storage-maintenance.timer
DISK_HEALTH_SERVICE=bp-storage-disk-health.service
DISK_HEALTH_TIMER=bp-storage-disk-health.timer
V2_TIMER=bp-v2-forward-coverage.timer
V4_TIMER=bp-v4-forward-coverage.timer
MUTATION_STARTED=0
DISK_BEFORE=''
DISK_AFTER=''
SOAK_FILE=''
HOLDOUT_BEFORE=''
HOLDOUT_AFTER=''
RECORDER_PID=''
PREDICTOR_PID=''
EXECUTION_PID=''
RECORDER_RESTARTS=''
PREDICTOR_RESTARTS=''
EXECUTION_RESTARTS=''

fail() {
  echo "PHASE14_RECORDER_V3_RECOVERY_GATE=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

read_env() {
  local path=$1 key=$2
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

require_workers_four() {
  sudo -u bp env -u RECORDER_WRITER_WORKERS "$REPO/.venv/bin/python" - "$ENV_FILE" <<'PY'
import sys
from bp_engine.config import Settings
settings = Settings(_env_file=sys.argv[1])
if settings.recorder_writer_workers != 4:
    raise SystemExit(f"recorder writer workers must equal 4, got {settings.recorder_writer_workers}")
PY
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

require_timer_headroom() {
  local timer=$1
  local minimum_seconds=$2
  local next_elapse next_epoch now_epoch headroom
  next_elapse=$(systemctl show -p NextElapseUSecRealtime --value "$timer")
  [[ -n "$next_elapse" && "$next_elapse" != "n/a" ]] || fail "timer_next_elapse_unavailable:$timer"
  next_epoch=$(date -d "$next_elapse" +%s 2>/dev/null) || fail "timer_next_elapse_unparseable:$timer"
  now_epoch=$(date +%s)
  headroom=$((next_epoch - now_epoch))
  (( headroom >= minimum_seconds )) || fail "timer_headroom_insufficient:$timer:$headroom"
}

validate_unit_file() {
  local unit=$1 expected=$2 fragment dropins
  fragment=$(systemctl show -p FragmentPath --value "$unit")
  [[ -r "$fragment" ]] || fail "unit_fragment_missing:$unit"
  cmp -s "$fragment" "$expected" || fail "unit_fragment_mismatch:$unit"
  dropins=$(systemctl show -p DropInPaths --value "$unit")
  [[ -z "$dropins" ]] || fail "unit_dropins_present:$unit"
}

validate_units() {
  validate_unit_file "$RECORDER_UNIT" "$REPO/deploy/systemd/bp-recorder.service"
  [[ -L "$V3_CURRENT" ]] || fail "v3_current_link_missing"
  [[ -r "$V3_CURRENT/deploy/bp-v3-frozen-predictor.service" ]] || fail "v3_predictor_runtime_unit_missing"
  [[ -r "$V3_CURRENT/deploy/bp-v3-paper-execution.service" ]] || fail "v3_execution_runtime_unit_missing"
  validate_unit_file "$V3_PREDICTOR" "$V3_CURRENT/deploy/bp-v3-frozen-predictor.service"
  validate_unit_file "$V3_EXECUTION" "$V3_CURRENT/deploy/bp-v3-paper-execution.service"
  [[ "$(systemctl show -p EnvironmentFiles --value "$RECORDER_UNIT")" == "$ENV_FILE (ignore_errors=no)" ]] || fail "recorder_environment_file_mismatch"
}

validate_v3_activation() {
  [[ -r "$V3_STATE/activation.json" ]] || fail "v3_activation_missing"
  [[ -r "$V3_STATE/frozen-model.joblib" ]] || fail "v3_model_missing"
  "$REPO/.venv/bin/python" - "$V3_STATE/activation.json" "$V3_STATE/frozen-model.joblib" <<'PY'
import hashlib
import json
import sys
from pathlib import Path
activation = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
model_sha = hashlib.sha256(Path(sys.argv[2]).read_bytes()).hexdigest()
if activation.get("model_sha256") != model_sha:
    raise SystemExit("v3 activation/model digest mismatch")
if activation.get("prediction_version") != "v3-frozen-paper-v1":
    raise SystemExit("unexpected V3 prediction version")
if activation.get("execution_version") != "paper-execution-v3-frozen-v1":
    raise SystemExit("unexpected V3 execution version")
PY
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
for name in ("maintenance_fresh", "current_partition_present", "retention_current"):
    if guards.get(name) is not True:
        raise SystemExit(f"storage guard not true: {name}")
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
    for path in sorted((p for p in root.glob("phase14-v2-gate-b-*/*") if p.name in names), key=lambda p: str(p)):
        digest.update(str(path).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
print(digest.hexdigest())
PY
}

run_soak() {
  SOAK_FILE=$(mktemp /var/tmp/bp-phase14-recorder-v3-recovery-soak.XXXXXX.json)
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
missing = sorted(
    label
    for label in required
    if int((feeds.get(label) or {}).get("event_count", 0)) <= 0
)
if missing:
    raise SystemExit(f"required feeds missing events: {missing}")
for label in required:
    incidents = (payload.get("incidents") or {}).get(label) or {}
    if int(incidents.get("backpressure", 0)) != 0:
        raise SystemExit(f"backpressure recorded for {label}")
PY
}

verify_dashboard_safety() {
  local snapshot
  snapshot=$(mktemp /var/tmp/bp-phase14-recorder-v3-recovery-dashboard.XXXXXX.json)
  curl -fsS http://127.0.0.1:8787/api/v1/snapshot > "$snapshot" || fail "dashboard_snapshot_unavailable"
  "$REPO/.venv/bin/python" - "$snapshot" <<'PY'
import json
import sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
mode = payload.get("mode") or {}
if mode.get("mode") != "research":
    raise SystemExit("dashboard mode is not research")
if mode.get("live_trading_enabled") is not False:
    raise SystemExit("dashboard reports live trading enabled")
if float(mode.get("max_trade_size_usd", -1)) != 0:
    raise SystemExit("dashboard max trade size nonzero")
if float(mode.get("max_daily_loss_usd", -1)) != 0:
    raise SystemExit("dashboard max daily loss nonzero")
PY
  rm -f "$snapshot"
}

rollback() {
  set +e
  echo "PHASE14_RECORDER_V3_RECOVERY_ROLLBACK=START" >&2
  systemctl stop "$V3_EXECUTION" >/dev/null 2>&1 || true
  systemctl stop "$V3_PREDICTOR" >/dev/null 2>&1 || true
  systemctl stop "$RECORDER_UNIT" >/dev/null 2>&1 || true
  systemctl start "$MAINTENANCE_TIMER" >/dev/null 2>&1 || true
  echo "RECORDER_ACTIVE=$(systemctl is-active "$RECORDER_UNIT" 2>/dev/null || true)" >&2
  echo "V3_PREDICTOR_ACTIVE=$(systemctl is-active "$V3_PREDICTOR" 2>/dev/null || true)" >&2
  echo "V3_EXECUTION_ACTIVE=$(systemctl is-active "$V3_EXECUTION" 2>/dev/null || true)" >&2
  echo "MAINTENANCE_TIMER_ACTIVE=$(systemctl is-active "$MAINTENANCE_TIMER" 2>/dev/null || true)" >&2
  echo "PHASE14_RECORDER_V3_RECOVERY_ROLLBACK=COMPLETE" >&2
  set -e
}

cleanup() {
  local rc=$?
  if (( rc != 0 && MUTATION_STARTED == 1 )); then rollback; fi
  rm -f "$DISK_BEFORE" "$DISK_AFTER" "$SOAK_FILE"
  exit "$rc"
}
trap cleanup EXIT

[[ -d "$REPO/.git" ]] || fail "deployed_repo_missing"
[[ -x "$REPO/.venv/bin/python" ]] || fail "python_runtime_missing"
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]] || fail "unexpected_deployed_head"
validate_deployed_checkout
require_research_zero_money
require_automatic_promotion_false
require_workers_four
validate_units
validate_v3_activation

for service in bp-postgres.service bp-dashboard-api.service bp-dashboard-web.service bp-paper-execution.service bp-live-predictor.service bp-prospective-outcomes.service; do
  systemctl is-active --quiet "$service" || fail "required_service_not_active:$service"
done
for unit in "$RECORDER_UNIT" "$V3_PREDICTOR" "$V3_EXECUTION"; do
  systemctl is-enabled --quiet "$unit" || fail "unit_not_enabled:$unit"
  systemctl is-active --quiet "$unit" && fail "unexpected_unit_already_active:$unit" || true
done
require_timer_active_enabled "$MAINTENANCE_TIMER"
require_timer_active_enabled "$DISK_HEALTH_TIMER"
require_timer_active_enabled "$V2_TIMER"
require_timer_active_enabled "$V4_TIMER"
wait_for_oneshot_idle_success "$MAINTENANCE_SERVICE" 3600
wait_for_oneshot_idle_success "$DISK_HEALTH_SERVICE" 30
require_timer_headroom "$MAINTENANCE_TIMER" 600

DISK_BEFORE=$(mktemp /var/tmp/bp-phase14-recorder-v3-recovery-disk-before.XXXXXX.json)
run_storage_health "$DISK_BEFORE"
HOLDOUT_BEFORE=$(gate_b_fingerprint)
require_timer_headroom "$MAINTENANCE_TIMER" 600

MUTATION_STARTED=1
systemctl stop "$MAINTENANCE_TIMER"
require_timer_enabled_inactive "$MAINTENANCE_TIMER"
wait_for_oneshot_idle_success "$MAINTENANCE_SERVICE" 3600

systemctl reset-failed "$RECORDER_UNIT" "$V3_PREDICTOR" "$V3_EXECUTION" >/dev/null 2>&1 || true
systemctl start "$RECORDER_UNIT"
for _ in $(seq 1 45); do systemctl is-active --quiet "$RECORDER_UNIT" && break; sleep 1; done
systemctl is-active --quiet "$RECORDER_UNIT" || fail "recorder_not_active_after_start"

systemctl start "$V3_PREDICTOR"
for _ in $(seq 1 30); do systemctl is-active --quiet "$V3_PREDICTOR" && break; sleep 1; done
systemctl is-active --quiet "$V3_PREDICTOR" || fail "v3_predictor_not_active_after_start"

systemctl start "$V3_EXECUTION"
for _ in $(seq 1 30); do systemctl is-active --quiet "$V3_EXECUTION" && break; sleep 1; done
systemctl is-active --quiet "$V3_EXECUTION" || fail "v3_execution_not_active_after_start"

RECORDER_PID=$(systemctl show -p MainPID --value "$RECORDER_UNIT")
PREDICTOR_PID=$(systemctl show -p MainPID --value "$V3_PREDICTOR")
EXECUTION_PID=$(systemctl show -p MainPID --value "$V3_EXECUTION")
RECORDER_RESTARTS=$(systemctl show -p NRestarts --value "$RECORDER_UNIT")
PREDICTOR_RESTARTS=$(systemctl show -p NRestarts --value "$V3_PREDICTOR")
EXECUTION_RESTARTS=$(systemctl show -p NRestarts --value "$V3_EXECUTION")
for pid in "$RECORDER_PID" "$PREDICTOR_PID" "$EXECUTION_PID"; do [[ "$pid" =~ ^[1-9][0-9]*$ ]] || fail "invalid_service_pid"; done

sleep 45
run_soak
verify_dashboard_safety
require_research_zero_money
require_workers_four

[[ "$(systemctl show -p MainPID --value "$RECORDER_UNIT")" == "$RECORDER_PID" ]] || fail "recorder_pid_changed"
[[ "$(systemctl show -p MainPID --value "$V3_PREDICTOR")" == "$PREDICTOR_PID" ]] || fail "v3_predictor_pid_changed"
[[ "$(systemctl show -p MainPID --value "$V3_EXECUTION")" == "$EXECUTION_PID" ]] || fail "v3_execution_pid_changed"
[[ "$(systemctl show -p NRestarts --value "$RECORDER_UNIT")" == "$RECORDER_RESTARTS" ]] || fail "recorder_restarted_during_recovery"
[[ "$(systemctl show -p NRestarts --value "$V3_PREDICTOR")" == "$PREDICTOR_RESTARTS" ]] || fail "v3_predictor_restarted_during_recovery"
[[ "$(systemctl show -p NRestarts --value "$V3_EXECUTION")" == "$EXECUTION_RESTARTS" ]] || fail "v3_execution_restarted_during_recovery"

DISK_AFTER=$(mktemp /var/tmp/bp-phase14-recorder-v3-recovery-disk-after.XXXXXX.json)
run_storage_health "$DISK_AFTER"
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]] || fail "deployed_head_changed"
validate_deployed_checkout
require_timer_enabled_inactive "$MAINTENANCE_TIMER"
require_timer_active_enabled "$DISK_HEALTH_TIMER"
require_timer_active_enabled "$V2_TIMER"
require_timer_active_enabled "$V4_TIMER"
HOLDOUT_AFTER=$(gate_b_fingerprint)
[[ "$HOLDOUT_AFTER" == "$HOLDOUT_BEFORE" ]] || fail "gate_b_artifacts_changed"

install -d -o bp -g bp -m 0750 "$EVIDENCE_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
EVIDENCE_PATH="$EVIDENCE_DIR/phase14-recorder-v3-recovery-$STAMP.json"
EVIDENCE_TMP=$(mktemp /var/tmp/bp-phase14-recorder-v3-recovery-evidence.XXXXXX.json)
"$REPO/.venv/bin/python" - "$DISK_BEFORE" "$DISK_AFTER" "$SOAK_FILE" "$EVIDENCE_TMP" "$DEPLOYED_HEAD" "$RECORDER_PID" "$PREDICTOR_PID" "$EXECUTION_PID" "$HOLDOUT_AFTER" <<'PY'
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
before, after, soak, output, deployed, recorder, predictor, execution, fingerprint = sys.argv[1:]
payload = {
    "verdict": "PASS",
    "recorded_at": datetime.now(UTC).isoformat(),
    "deployed_head": deployed,
    "services": {
        "bp-recorder.service": {"active": True, "main_pid": int(recorder)},
        "bp-v3-frozen-predictor.service": {"active": True, "main_pid": int(predictor)},
        "bp-v3-paper-execution.service": {"active": True, "main_pid": int(execution)},
    },
    "recorder_writer_workers": 4,
    "safety": {
        "mode": "research",
        "live_trading_enabled": False,
        "max_trade_size_usd": 0,
        "max_daily_loss_usd": 0,
        "automatic_promotion": False,
    },
    "storage_before": json.loads(Path(before).read_text()),
    "storage_after": json.loads(Path(after).read_text()),
    "soak": json.loads(Path(soak).read_text()),
    "gate_b_artifacts_fingerprint": fingerprint,
    "handoff": {
        "maintenance_timer_enabled": True,
        "maintenance_timer_active": False,
        "rollout_handoff_ready": True,
    },
}
Path(output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
install -o bp -g bp -m 0640 "$EVIDENCE_TMP" "$EVIDENCE_PATH"
sync -f "$EVIDENCE_PATH"
rm -f "$EVIDENCE_TMP"
MUTATION_STARTED=0

echo "PHASE14_RECORDER_V3_RECOVERY_GATE=PASS"
echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"
echo "RECORDER_PID=$RECORDER_PID"
echo "V3_PREDICTOR_PID=$PREDICTOR_PID"
echo "V3_EXECUTION_PID=$EXECUTION_PID"
echo "EVIDENCE_PATH=$EVIDENCE_PATH"
echo "MAINTENANCE_TIMER_ACTIVE=inactive"
echo "ROLLOUT_HANDOFF_READY=true"
echo "LIVE_TRADING_ENABLED=false"
echo "MAX_TRADE_SIZE_USD=0"
echo "MAX_DAILY_LOSS_USD=0"
REMOTE

REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 -w0)
echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "HELPER_HEAD=$HELPER_HEAD"
echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"
echo "This helper restores only the recorder and accepted frozen-V3 paper service chain."
echo "It preserves the existing checkout, 4-writer config, research mode, live-disabled state, and zero money."

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo env PHASE14_RECORDER_V3_RECOVERY_DEPLOYED_HEAD=$DEPLOYED_HEAD_Q bash"
