#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V2_FORWARD_RESTORE_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_V2_FORWARD_RESTORE_ZONE:-us-east1-c}"
VM="${PHASE14_V2_FORWARD_RESTORE_VM:-bp-recorder}"
HELPER_HEAD="${PHASE14_V2_FORWARD_RESTORE_HELPER_HEAD:-}"
DEPLOYED_HEAD="${PHASE14_V2_FORWARD_RESTORE_DEPLOYED_HEAD:-}"
ENV_FILE="${PHASE14_V2_FORWARD_RESTORE_ENV_FILE:-/etc/bp/bp.env}"
STORAGE_EVIDENCE="${PHASE14_V2_FORWARD_RESTORE_STORAGE_EVIDENCE:-}"
STORAGE_EVIDENCE_SHA256="${PHASE14_V2_FORWARD_RESTORE_STORAGE_EVIDENCE_SHA256:-}"

fail_local() {
  echo "PHASE14_V2_FORWARD_RESTORE_GATE=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$HELPER_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail_local "helper_head_invalid"
[[ "$DEPLOYED_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail_local "deployed_head_invalid"
[[ "$ENV_FILE" == /* ]] || fail_local "env_file_must_be_absolute"
[[ "$STORAGE_EVIDENCE" == /* ]] || fail_local "storage_evidence_path_must_be_absolute"
[[ "$STORAGE_EVIDENCE_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail_local "storage_evidence_sha256_invalid"

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

printf -v HELPER_HEAD_Q '%q' "$HELPER_HEAD"
printf -v DEPLOYED_HEAD_Q '%q' "$DEPLOYED_HEAD"
printf -v ENV_FILE_Q '%q' "$ENV_FILE"
printf -v STORAGE_EVIDENCE_Q '%q' "$STORAGE_EVIDENCE"
printf -v STORAGE_EVIDENCE_SHA256_Q '%q' "$STORAGE_EVIDENCE_SHA256"

REMOTE_SCRIPT=$(cat <<'REMOTE'
set -Eeuo pipefail

HELPER_HEAD="${PHASE14_V2_FORWARD_RESTORE_HELPER_HEAD:?}"
DEPLOYED_HEAD="${PHASE14_V2_FORWARD_RESTORE_DEPLOYED_HEAD:?}"
ENV_FILE="${PHASE14_V2_FORWARD_RESTORE_ENV_FILE:?}"
STORAGE_EVIDENCE="${PHASE14_V2_FORWARD_RESTORE_STORAGE_EVIDENCE:?}"
STORAGE_EVIDENCE_SHA256="${PHASE14_V2_FORWARD_RESTORE_STORAGE_EVIDENCE_SHA256:?}"

REPO=/opt/bp
SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env
RECORDER_UNIT=bp-recorder.service
SERVICE_UNIT=bp-v2-forward-coverage.service
TIMER_UNIT=bp-v2-forward-coverage.timer
MAINTENANCE_TIMER=bp-storage-maintenance.timer
DISK_HEALTH_TIMER=bp-storage-disk-health.timer
EVIDENCE_DIR=/var/lib/bp/evidence
CORE_SERVICES=(
  bp-recorder.service
  bp-postgres.service
  bp-dashboard-api.service
  bp-dashboard-web.service
  bp-paper-execution.service
  bp-live-predictor.service
  bp-prospective-outcomes.service
)

ROLLBACK_ARMED=0
TIMER_WAS_ENABLED=0
TIMER_WAS_ACTIVE=0
DISK_BEFORE=""
DISK_AFTER=""
COVERAGE_FILE=""
EVIDENCE_TMP=""
EVIDENCE_PATH=""

fail() {
  echo "PHASE14_V2_FORWARD_RESTORE_GATE=FAIL" >&2
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

  if [[ "$mode" != "research" || "$live" != "false" || "$trade" != "0" || "$loss" != "0" ||         "$safe_mode" != "research" || "$safe_live" != "false" || "$safe_trade" != "0" || "$safe_loss" != "0" ]]; then
    fail "research_zero_money_boundary_not_satisfied"
  fi
}

require_core_services_active() {
  local service
  for service in "${CORE_SERVICES[@]}"; do
    systemctl is-active --quiet "$service" || fail "service_not_active:$service"
  done
}

read_recorder_config_workers() {
  env -u RECORDER_WRITER_WORKERS "$REPO/.venv/bin/python" - "$ENV_FILE" <<'PY'
from __future__ import annotations

import sys

from bp_engine.config import Settings

print(Settings(_env_file=sys.argv[1]).recorder_writer_workers)
PY
}

validate_collector_units() {
  local service_fragment service_dropins timer_fragment timer_dropins

  service_fragment=$(systemctl show -p FragmentPath --value "$SERVICE_UNIT")
  [[ -r "$service_fragment" ]] || fail "collector_service_fragment_missing"
  cmp -s "$service_fragment" "$REPO/deploy/$SERVICE_UNIT" || fail "collector_service_fragment_mismatch"

  service_dropins=$(systemctl show -p DropInPaths --value "$SERVICE_UNIT")
  [[ -z "$service_dropins" ]] || fail "collector_service_dropins_present"

  timer_fragment=$(systemctl show -p FragmentPath --value "$TIMER_UNIT")
  [[ -r "$timer_fragment" ]] || fail "collector_timer_fragment_missing"
  cmp -s "$timer_fragment" "$REPO/deploy/$TIMER_UNIT" || fail "collector_timer_fragment_mismatch"

  timer_dropins=$(systemctl show -p DropInPaths --value "$TIMER_UNIT")
  [[ -z "$timer_dropins" ]] || fail "collector_timer_dropins_present"
}

run_storage_health() {
  local destination=$1
  if ! sudo -u bp "$REPO/.venv/bin/python" "$REPO/scripts/storage_maintenance.py"       disk-health --env-file "$ENV_FILE" > "$destination"; then
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

capture_timer_state() {
  if systemctl is-enabled --quiet "$TIMER_UNIT" 2>/dev/null; then
    TIMER_WAS_ENABLED=1
  fi
  if systemctl is-active --quiet "$TIMER_UNIT" 2>/dev/null; then
    TIMER_WAS_ACTIVE=1
  fi
}

rollback() {
  set +e
  echo "PHASE14_V2_FORWARD_RESTORE_ROLLBACK=START" >&2

  systemctl stop "$TIMER_UNIT" >/dev/null 2>&1 || true
  systemctl stop "$SERVICE_UNIT" >/dev/null 2>&1 || true

  if (( TIMER_WAS_ENABLED )); then
    systemctl enable "$TIMER_UNIT" >/dev/null 2>&1 || true
  else
    systemctl disable "$TIMER_UNIT" >/dev/null 2>&1 || true
  fi
  if (( TIMER_WAS_ACTIVE )); then
    systemctl start "$TIMER_UNIT" >/dev/null 2>&1 || true
  fi

  echo "TIMER_ACTIVE=$(systemctl is-active "$TIMER_UNIT" 2>/dev/null || true)" >&2
  echo "TIMER_ENABLED=$(systemctl is-enabled "$TIMER_UNIT" 2>/dev/null || true)" >&2
  echo "PHASE14_V2_FORWARD_RESTORE_ROLLBACK=COMPLETE" >&2
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
  [[ -n "$COVERAGE_FILE" ]] && rm -f "$COVERAGE_FILE"
  [[ -n "$EVIDENCE_TMP" ]] && rm -f "$EVIDENCE_TMP"
  set -e
  exit "$rc"
}
trap cleanup EXIT

[[ -d "$REPO/.git" ]] || fail "deployed_repo_missing"
[[ -x "$REPO/.venv/bin/python" ]] || fail "python_runtime_missing"
[[ -r "$STORAGE_EVIDENCE" ]] || fail "storage_evidence_missing"
[[ "$(sha256sum "$STORAGE_EVIDENCE" | awk '{print $1}')" == "$STORAGE_EVIDENCE_SHA256" ]] || fail "storage_evidence_sha256_mismatch"
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]] || fail "unexpected_deployed_head"
validate_deployed_checkout

systemctl is-active --quiet "$RECORDER_UNIT" || fail "recorder_unit_not_active"
require_core_services_active
require_research_zero_money
[[ "$(read_recorder_config_workers)" == "4" ]] || fail "recorder_config_worker_count_not_4"

systemctl is-active --quiet "$MAINTENANCE_TIMER" || fail "maintenance_timer_not_active"
systemctl is-active --quiet "$DISK_HEALTH_TIMER" || fail "disk_health_timer_not_active"
validate_collector_units

capture_timer_state
(( TIMER_WAS_ENABLED == 1 )) || fail "collector_timer_not_enabled_before_restore"
(( TIMER_WAS_ACTIVE == 0 )) || fail "collector_timer_already_active"
if systemctl is-active --quiet "$SERVICE_UNIT"; then
  fail "collector_service_already_active"
fi

DISK_BEFORE=$(mktemp /var/tmp/bp-phase14-v2-forward-restore-disk-before.XXXXXX.json)
run_storage_health "$DISK_BEFORE"

ROLLBACK_ARMED=1

# Prove the already-installed oneshot unit executes successfully without changing /opt/bp.
systemctl start "$SERVICE_UNIT"
[[ "$(systemctl show -p Result --value "$SERVICE_UNIT")" == "success" ]] || fail "collector_service_cycle_failed"

# Run one explicit bounded cycle to capture deterministic acceptance JSON.
COVERAGE_FILE=$(mktemp /var/tmp/bp-phase14-v2-forward-restore-coverage.XXXXXX.json)
if ! sudo -u bp env     MODE=research     LIVE_TRADING_ENABLED=false     MAX_TRADE_SIZE_USD=0     MAX_DAILY_LOSS_USD=0     PYTHONPATH="$REPO/src"     "$REPO/.venv/bin/python" "$REPO/scripts/run_v2_forward_coverage.py"     once --env-file "$ENV_FILE" > "$COVERAGE_FILE"; then
  cat "$COVERAGE_FILE" >&2 || true
  fail "bounded_manual_coverage_cycle_failed"
fi

"$REPO/.venv/bin/python" - "$COVERAGE_FILE" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if int(payload.get("future_cutoff_violation_count", -1)) != 0:
    raise SystemExit("future_cutoff_violation_count must remain zero")
if payload.get("policy_selected") is not False:
    raise SystemExit("policy_selected must remain false")
if payload.get("automatic_promotion") is not False:
    raise SystemExit("automatic_promotion must remain false")
if int(payload.get("coverage_row_count", 0)) < 4:
    raise SystemExit("coverage_row_count must preserve at least the Gate A baseline")
if int(payload.get("coverage_market_count", 0)) < 1:
    raise SystemExit("coverage_market_count must preserve at least the Gate A baseline")
PY

systemctl enable --now "$TIMER_UNIT"
systemctl is-enabled --quiet "$TIMER_UNIT" || fail "collector_timer_not_enabled"
systemctl is-active --quiet "$TIMER_UNIT" || fail "collector_timer_not_active"

require_core_services_active
require_research_zero_money
[[ "$(read_recorder_config_workers)" == "4" ]] || fail "recorder_config_worker_count_changed"
validate_collector_units
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]] || fail "deployed_head_changed"

DISK_AFTER=$(mktemp /var/tmp/bp-phase14-v2-forward-restore-disk-after.XXXXXX.json)
run_storage_health "$DISK_AFTER"

install -d -o bp -g bp -m 0750 "$EVIDENCE_DIR"
EVIDENCE_TMP=$(mktemp /var/tmp/bp-phase14-v2-forward-restore-evidence.XXXXXX.json)
"$REPO/.venv/bin/python" -   "$COVERAGE_FILE" "$DISK_BEFORE" "$DISK_AFTER" "$EVIDENCE_TMP"   "$HELPER_HEAD" "$DEPLOYED_HEAD" "$STORAGE_EVIDENCE" "$STORAGE_EVIDENCE_SHA256" <<'PY'
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

(
    coverage_path,
    before_path,
    after_path,
    output_path,
    helper_head,
    deployed_head,
    storage_evidence,
    storage_evidence_sha256,
) = sys.argv[1:]

coverage = json.loads(Path(coverage_path).read_text(encoding="utf-8"))
before = json.loads(Path(before_path).read_text(encoding="utf-8"))
after = json.loads(Path(after_path).read_text(encoding="utf-8"))

payload = {
    "verdict": "PASS",
    "recorded_at": datetime.now(UTC).isoformat(),
    "helper_head": helper_head,
    "deployed_head": deployed_head,
    "storage_evidence": storage_evidence,
    "storage_evidence_sha256": storage_evidence_sha256,
    "recorder": {
        "active": True,
        "config_workers": 4,
    },
    "collector": {
        "service_unit": "bp-v2-forward-coverage.service",
        "service_cycle_result": "success",
        "timer_unit": "bp-v2-forward-coverage.timer",
        "timer_enabled": True,
        "timer_active": True,
    },
    "safety": {
        "mode": "research",
        "live_trading_enabled": False,
        "max_trade_size_usd": 0,
        "max_daily_loss_usd": 0,
        "automatic_promotion": False,
        "policy_selected": False,
    },
    "coverage": coverage,
    "storage_before": before,
    "storage_after": after,
}
Path(output_path).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
EVIDENCE_PATH="$EVIDENCE_DIR/phase14-v2-forward-coverage-restore-$STAMP.json"
[[ ! -e "$EVIDENCE_PATH" ]] || fail "evidence_path_exists"
install -o bp -g bp -m 0640 "$EVIDENCE_TMP" "$EVIDENCE_PATH"

ROLLBACK_ARMED=0

echo "PHASE14_V2_FORWARD_RESTORE_GATE=PASS"
echo "HELPER_HEAD=$HELPER_HEAD"
echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"
echo "RECORDER_CONFIG_WORKERS=4"
echo "SERVICE_RESULT=$(systemctl show -p Result --value "$SERVICE_UNIT")"
echo "TIMER_ENABLED=$(systemctl is-enabled "$TIMER_UNIT")"
echo "TIMER_ACTIVE=$(systemctl is-active "$TIMER_UNIT")"
echo "COVERAGE_REPORT=$(tr -d '\n' < "$COVERAGE_FILE")"
echo "EVIDENCE_PATH=$EVIDENCE_PATH"
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
echo "Running controlled Phase 14 V2 forward-coverage restore gate."

gcloud compute ssh "$VM"   --project="$PROJECT"   --zone="$ZONE"   --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo env PHASE14_V2_FORWARD_RESTORE_HELPER_HEAD=$HELPER_HEAD_Q PHASE14_V2_FORWARD_RESTORE_DEPLOYED_HEAD=$DEPLOYED_HEAD_Q PHASE14_V2_FORWARD_RESTORE_ENV_FILE=$ENV_FILE_Q PHASE14_V2_FORWARD_RESTORE_STORAGE_EVIDENCE=$STORAGE_EVIDENCE_Q PHASE14_V2_FORWARD_RESTORE_STORAGE_EVIDENCE_SHA256=$STORAGE_EVIDENCE_SHA256_Q bash"
