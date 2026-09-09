#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_RECORDER_RESTART_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_RECORDER_RESTART_ZONE:-us-east1-c}"
VM="${PHASE14_RECORDER_RESTART_VM:-bp-recorder}"
HELPER_HEAD="${PHASE14_RECORDER_RESTART_HELPER_HEAD:-}"
DEPLOYED_HEAD="${PHASE14_RECORDER_RESTART_DEPLOYED_HEAD:-}"
ENV_FILE="${PHASE14_RECORDER_RESTART_ENV_FILE:-/etc/bp/bp.env}"
STORAGE_EVIDENCE="${PHASE14_RECORDER_RESTART_STORAGE_EVIDENCE:-}"
STORAGE_EVIDENCE_SHA256="${PHASE14_RECORDER_RESTART_STORAGE_EVIDENCE_SHA256:-}"
WRITER_WORKERS="${PHASE14_RECORDER_RESTART_WRITER_WORKERS:-4}"

fail_local() {
  echo "PHASE14_RECORDER_RESTART_GATE=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$HELPER_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail_local "helper_head_invalid"
[[ "$DEPLOYED_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail_local "deployed_head_invalid"
[[ "$STORAGE_EVIDENCE_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail_local "storage_evidence_sha256_invalid"
[[ "$STORAGE_EVIDENCE" == /* ]] || fail_local "storage_evidence_path_must_be_absolute"
[[ "$ENV_FILE" == /* ]] || fail_local "env_file_must_be_absolute"
[[ "$WRITER_WORKERS" == "4" ]] || fail_local "writer_workers_must_equal_4"

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
[[ -n "$ROOT" ]] || fail_local "local_repository_missing"
cd "$ROOT"
[[ "$(git rev-parse HEAD)" == "$HELPER_HEAD" ]] || fail_local "local_helper_head_mismatch"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || fail_local "local_working_tree_dirty"

REMOTE_MAIN=$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')
[[ "$REMOTE_MAIN" == "$HELPER_HEAD" ]] || fail_local "remote_main_changed"

command -v gcloud >/dev/null 2>&1 || fail_local "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . || fail_local "gcloud_auth_missing"

gcloud config set project "$PROJECT" >/dev/null

printf -v DEPLOYED_HEAD_Q '%q' "$DEPLOYED_HEAD"
printf -v ENV_FILE_Q '%q' "$ENV_FILE"
printf -v STORAGE_EVIDENCE_Q '%q' "$STORAGE_EVIDENCE"
printf -v STORAGE_EVIDENCE_SHA256_Q '%q' "$STORAGE_EVIDENCE_SHA256"
printf -v WRITER_WORKERS_Q '%q' "$WRITER_WORKERS"

REMOTE_SCRIPT=$(cat <<'REMOTE'
set -Eeuo pipefail

DEPLOYED_HEAD="${PHASE14_RECORDER_RESTART_DEPLOYED_HEAD:?}"
ENV_FILE="${PHASE14_RECORDER_RESTART_ENV_FILE:?}"
STORAGE_EVIDENCE="${PHASE14_RECORDER_RESTART_STORAGE_EVIDENCE:?}"
STORAGE_EVIDENCE_SHA256="${PHASE14_RECORDER_RESTART_STORAGE_EVIDENCE_SHA256:?}"
WRITER_WORKERS="${PHASE14_RECORDER_RESTART_WRITER_WORKERS:?}"
REPO=/opt/bp
RECORDER_UNIT=bp-recorder.service
MAINTENANCE_TIMER=bp-storage-maintenance.timer
DISK_HEALTH_TIMER=bp-storage-disk-health.timer
REQUIRED_ACTIVE_SERVICES=(
  bp-postgres.service
  bp-dashboard-api.service
  bp-dashboard-web.service
  bp-paper-execution.service
  bp-live-predictor.service
  bp-prospective-outcomes.service
)
ENV_BACKUP=""
ENV_STAGE=""
DISK_BEFORE=""
DISK_AFTER=""
SOAK_FILE=""
SNAPSHOT_FILE=""
ROLLBACK_ARMED=0

fail() {
  echo "PHASE14_RECORDER_RESTART_GATE=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

read_env() {
  local key=$1
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE"
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
  [[ "$(read_env MODE)" == "research" ]] || fail "mode_not_research"
  [[ "$(read_env LIVE_TRADING_ENABLED)" == "false" ]] || fail "live_trading_enabled"
  [[ "$(read_env MAX_TRADE_SIZE_USD)" == "0" ]] || fail "max_trade_size_nonzero"
  [[ "$(read_env MAX_DAILY_LOSS_USD)" == "0" ]] || fail "max_daily_loss_nonzero"
}

run_storage_health() {
  local destination=$1
  if ! sudo -u bp "$REPO/.venv/bin/python" "$REPO/scripts/storage_maintenance.py" disk-health       --env-file "$ENV_FILE" > "$destination"; then
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

run_soak_gate() {
  SOAK_FILE=$(mktemp /var/tmp/bp-phase14-recorder-restart-soak.XXXXXX.json)
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
missing = sorted(label for label in required if int((feeds.get(label) or {}).get("event_count", 0)) <= 0)
if missing:
    raise SystemExit(f"required feeds missing post-restart events: {missing}")
for label in required:
    incidents = (payload.get("incidents") or {}).get(label) or {}
    if int(incidents.get("backpressure", 0)) != 0:
        raise SystemExit(f"required feed recorded backpressure: {label}")
PY
}

verify_dashboard_safety() {
  SNAPSHOT_FILE=$(mktemp /var/tmp/bp-phase14-recorder-restart-snapshot.XXXXXX.json)
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

restore_pre_restart_state() {
  set +e
  echo "PHASE14_RECORDER_RESTART_ROLLBACK=START" >&2
  systemctl stop "$RECORDER_UNIT" >/dev/null 2>&1 || true
  if [[ -n "$ENV_BACKUP" && -r "$ENV_BACKUP" ]]; then
    cp -a "$ENV_BACKUP" "$ENV_FILE"
    sync "$ENV_FILE" >/dev/null 2>&1 || true
  fi
  echo "RECORDER_ACTIVE=$(systemctl is-active "$RECORDER_UNIT" 2>/dev/null || true)" >&2
  echo "PHASE14_RECORDER_RESTART_ROLLBACK=COMPLETE" >&2
  set -e
}

cleanup() {
  local rc=$?
  trap - EXIT
  set +e
  if (( rc != 0 && ROLLBACK_ARMED )); then
    restore_pre_restart_state
  fi
  [[ -n "$ENV_BACKUP" ]] && rm -f "$ENV_BACKUP"
  [[ -n "$ENV_STAGE" ]] && rm -f "$ENV_STAGE"
  [[ -n "$DISK_BEFORE" ]] && rm -f "$DISK_BEFORE"
  [[ -n "$DISK_AFTER" ]] && rm -f "$DISK_AFTER"
  [[ -n "$SOAK_FILE" ]] && rm -f "$SOAK_FILE"
  [[ -n "$SNAPSHOT_FILE" ]] && rm -f "$SNAPSHOT_FILE"
  set -e
  exit "$rc"
}
trap cleanup EXIT

[[ "$WRITER_WORKERS" == "4" ]] || fail "writer_workers_must_equal_4"
[[ -d "$REPO/.git" ]] || fail "missing_deployed_repository"
[[ -r "$ENV_FILE" ]] || fail "missing_environment_file"
[[ -x "$REPO/.venv/bin/python" ]] || fail "missing_python_runtime"
[[ -r "$STORAGE_EVIDENCE" ]] || fail "storage_evidence_missing"
[[ "$(sha256sum "$STORAGE_EVIDENCE" | awk '{print $1}')" == "$STORAGE_EVIDENCE_SHA256" ]] || fail "storage_evidence_sha256_mismatch"
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]] || fail "unexpected_deployed_head"
validate_deployed_checkout

systemctl is-active --quiet "$RECORDER_UNIT" && fail "recorder_already_active" || true
systemctl is-active --quiet "$MAINTENANCE_TIMER" || fail "maintenance_timer_not_active"
systemctl is-active --quiet "$DISK_HEALTH_TIMER" || fail "disk_health_timer_not_active"
for service in "${REQUIRED_ACTIVE_SERVICES[@]}"; do
  systemctl is-active --quiet "$service" || fail "required_service_not_active:$service"
done
require_research_zero_money

CURRENT_WORKERS=$(read_env RECORDER_WRITER_WORKERS)
if [[ -n "$CURRENT_WORKERS" && "$CURRENT_WORKERS" != "1" ]]; then
  fail "unexpected_existing_writer_workers:$CURRENT_WORKERS"
fi
if [[ $(grep -c '^RECORDER_WRITER_WORKERS=' "$ENV_FILE" || true) -gt 1 ]]; then
  fail "duplicate_writer_worker_setting"
fi

DISK_BEFORE=$(mktemp /var/tmp/bp-phase14-recorder-restart-disk-before.XXXXXX.json)
run_storage_health "$DISK_BEFORE"

ENV_BACKUP=$(mktemp /var/tmp/bp-phase14-recorder-restart-env-backup.XXXXXX)
cp -a "$ENV_FILE" "$ENV_BACKUP"
ENV_STAGE=$(mktemp /var/tmp/bp-phase14-recorder-restart-env-stage.XXXXXX)
"$REPO/.venv/bin/python" - "$ENV_FILE" "$ENV_STAGE" "$WRITER_WORKERS" <<'PY'
from __future__ import annotations
import sys
from pathlib import Path

source, destination, workers = sys.argv[1:]
lines = Path(source).read_text(encoding="utf-8").splitlines()
indices = [i for i, line in enumerate(lines) if line.startswith("RECORDER_WRITER_WORKERS=")]
if len(indices) > 1:
    raise SystemExit("duplicate RECORDER_WRITER_WORKERS")
replacement = f"RECORDER_WRITER_WORKERS={workers}"
if indices:
    lines[indices[0]] = replacement
else:
    lines.append(replacement)
Path(destination).write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
ENV_UID=$(stat -c '%u' "$ENV_FILE")
ENV_GID=$(stat -c '%g' "$ENV_FILE")
ENV_MODE=$(stat -c '%a' "$ENV_FILE")
install -o "$ENV_UID" -g "$ENV_GID" -m "$ENV_MODE" "$ENV_STAGE" "$ENV_FILE"
sync "$ENV_FILE"
[[ "$(read_env RECORDER_WRITER_WORKERS)" == "4" ]] || fail "writer_worker_setting_not_applied"
ROLLBACK_ARMED=1

systemctl reset-failed "$RECORDER_UNIT" >/dev/null 2>&1 || true
systemctl restart "$RECORDER_UNIT"
for _ in $(seq 1 45); do
  systemctl is-active --quiet "$RECORDER_UNIT" && break
  sleep 1
done
systemctl is-active --quiet "$RECORDER_UNIT" || fail "recorder_not_active_after_restart"

MAIN_PID=$(systemctl show -p MainPID --value "$RECORDER_UNIT")
[[ "$MAIN_PID" =~ ^[1-9][0-9]*$ ]] || fail "recorder_main_pid_invalid"
tr '\0' '\n' < "/proc/$MAIN_PID/environ" | grep -qx 'RECORDER_WRITER_WORKERS=4' || fail "recorder_effective_worker_count_not_4"

sleep 45
for service in "${REQUIRED_ACTIVE_SERVICES[@]}"; do
  systemctl is-active --quiet "$service" || fail "required_service_not_active_after_restart:$service"
done
systemctl is-active --quiet "$RECORDER_UNIT" || fail "recorder_not_active_after_soak"
require_research_zero_money
run_soak_gate
verify_dashboard_safety

DISK_AFTER=$(mktemp /var/tmp/bp-phase14-recorder-restart-disk-after.XXXXXX.json)
run_storage_health "$DISK_AFTER"
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]] || fail "deployed_head_changed"
validate_deployed_checkout

ROLLBACK_ARMED=0
install -d -o bp -g bp /var/lib/bp/evidence
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
EVIDENCE_FILE="/var/lib/bp/evidence/phase14-recorder-restart-gate-$STAMP.txt"
{
  echo "PHASE14_RECORDER_RESTART_GATE=PASS"
  echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"
  echo "STORAGE_EVIDENCE=$STORAGE_EVIDENCE"
  echo "STORAGE_EVIDENCE_SHA256=$STORAGE_EVIDENCE_SHA256"
  echo "RECORDER_WRITER_WORKERS=4"
  echo "RECORDER_ACTIVE=$(systemctl is-active "$RECORDER_UNIT")"
  echo "MAINTENANCE_TIMER_ACTIVE=$(systemctl is-active "$MAINTENANCE_TIMER")"
  echo "DISK_HEALTH_TIMER_ACTIVE=$(systemctl is-active "$DISK_HEALTH_TIMER")"
  echo "DISK_BEFORE=$(tr -d '\n' < "$DISK_BEFORE")"
  echo "DISK_AFTER=$(tr -d '\n' < "$DISK_AFTER")"
  echo "SOAK_REPORT=$(tr -d '\n' < "$SOAK_FILE")"
} | tee "$EVIDENCE_FILE"
chown bp:bp "$EVIDENCE_FILE"
chmod 0640 "$EVIDENCE_FILE"

echo "EVIDENCE_FILE=$EVIDENCE_FILE"
echo "PHASE14_RECORDER_RESTART_GATE=PASS"
REMOTE
)
REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 -w0)

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "HELPER_HEAD=$HELPER_HEAD"
echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"
echo "STORAGE_EVIDENCE=$STORAGE_EVIDENCE"
echo "STORAGE_EVIDENCE_SHA256=$STORAGE_EVIDENCE_SHA256"
echo "RECORDER_WRITER_WORKERS=$WRITER_WORKERS"
echo "Running controlled Phase 14 recorder restart gate."

gcloud compute ssh "$VM"   --project="$PROJECT"   --zone="$ZONE"   --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo env PHASE14_RECORDER_RESTART_DEPLOYED_HEAD=$DEPLOYED_HEAD_Q PHASE14_RECORDER_RESTART_ENV_FILE=$ENV_FILE_Q PHASE14_RECORDER_RESTART_STORAGE_EVIDENCE=$STORAGE_EVIDENCE_Q PHASE14_RECORDER_RESTART_STORAGE_EVIDENCE_SHA256=$STORAGE_EVIDENCE_SHA256_Q PHASE14_RECORDER_RESTART_WRITER_WORKERS=$WRITER_WORKERS_Q bash"
