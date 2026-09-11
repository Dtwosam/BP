#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V2_GATE_B_READINESS_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_V2_GATE_B_READINESS_ZONE:-us-east1-c}"
VM="${PHASE14_V2_GATE_B_READINESS_VM:-bp-recorder}"
HELPER_HEAD="${PHASE14_V2_GATE_B_READINESS_HELPER_HEAD:-}"
DEPLOYED_HEAD="${PHASE14_V2_GATE_B_READINESS_DEPLOYED_HEAD:-}"
ENV_FILE="${PHASE14_V2_GATE_B_READINESS_ENV_FILE:-/etc/bp/bp.env}"
STORAGE_EVIDENCE="${PHASE14_V2_GATE_B_READINESS_STORAGE_EVIDENCE:-}"
STORAGE_EVIDENCE_SHA256="${PHASE14_V2_GATE_B_READINESS_STORAGE_EVIDENCE_SHA256:-}"
PLANNING_EPOCH_START="${PHASE14_V2_GATE_B_READINESS_PLANNING_EPOCH_START:-}"

fail_local() {
  echo "PHASE14_V2_GATE_B_READINESS=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$HELPER_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail_local "helper_head_invalid"
[[ "$DEPLOYED_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail_local "deployed_head_invalid"
[[ "$ENV_FILE" == /* ]] || fail_local "env_file_must_be_absolute"
[[ "$STORAGE_EVIDENCE" == /* ]] || fail_local "storage_evidence_path_must_be_absolute"
[[ "$STORAGE_EVIDENCE_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail_local "storage_evidence_sha256_invalid"
if ! PLANNING_EPOCH_START_CANONICAL=$(python3 - "$PLANNING_EPOCH_START" <<'PY'
from __future__ import annotations

import sys
from datetime import UTC, datetime

try:
    value = datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
except ValueError:
    raise SystemExit(1)
if value.tzinfo is None or value.utcoffset() is None:
    raise SystemExit(1)
print(value.astimezone(UTC).isoformat())
PY
); then
  fail_local "planning_epoch_start_invalid"
fi

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

ARCHIVE=$(mktemp /tmp/bp-v2-gate-b-readiness.XXXXXX.tar.gz)
cleanup_local() {
  rm -f "$ARCHIVE"
}
trap cleanup_local EXIT

git archive --format=tar.gz --output="$ARCHIVE" "$HELPER_HEAD"
ARCHIVE_SHA256=$(sha256sum "$ARCHIVE" | awk '{print $1}')
REMOTE_ARCHIVE="/tmp/bp-v2-gate-b-readiness-${HELPER_HEAD}.tar.gz"

gcloud compute scp "$ARCHIVE" "$VM:$REMOTE_ARCHIVE" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet

printf -v HELPER_HEAD_Q '%q' "$HELPER_HEAD"
printf -v DEPLOYED_HEAD_Q '%q' "$DEPLOYED_HEAD"
printf -v ENV_FILE_Q '%q' "$ENV_FILE"
printf -v STORAGE_EVIDENCE_Q '%q' "$STORAGE_EVIDENCE"
printf -v STORAGE_EVIDENCE_SHA256_Q '%q' "$STORAGE_EVIDENCE_SHA256"
printf -v PLANNING_EPOCH_START_Q '%q' "$PLANNING_EPOCH_START"
printf -v REMOTE_ARCHIVE_Q '%q' "$REMOTE_ARCHIVE"
printf -v ARCHIVE_SHA256_Q '%q' "$ARCHIVE_SHA256"

REMOTE_SCRIPT=$(cat <<'REMOTE'
set -Eeuo pipefail

HELPER_HEAD="${PHASE14_V2_GATE_B_READINESS_HELPER_HEAD:?}"
DEPLOYED_HEAD="${PHASE14_V2_GATE_B_READINESS_DEPLOYED_HEAD:?}"
ENV_FILE="${PHASE14_V2_GATE_B_READINESS_ENV_FILE:?}"
STORAGE_EVIDENCE="${PHASE14_V2_GATE_B_READINESS_STORAGE_EVIDENCE:?}"
STORAGE_EVIDENCE_SHA256="${PHASE14_V2_GATE_B_READINESS_STORAGE_EVIDENCE_SHA256:?}"
PLANNING_EPOCH_START="${PHASE14_V2_GATE_B_READINESS_PLANNING_EPOCH_START:?}"
ARCHIVE="${PHASE14_V2_GATE_B_READINESS_ARCHIVE:?}"
ARCHIVE_SHA256="${PHASE14_V2_GATE_B_READINESS_ARCHIVE_SHA256:?}"

REPO=/opt/bp
SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env
V2_TIMER=bp-v2-forward-coverage.timer
MAINTENANCE_TIMER=bp-storage-maintenance.timer
DISK_HEALTH_TIMER=bp-storage-disk-health.timer
CORE_SERVICES=(
  bp-recorder.service
  bp-postgres.service
  bp-dashboard-api.service
  bp-dashboard-web.service
  bp-paper-execution.service
  bp-live-predictor.service
  bp-prospective-outcomes.service
)
RUNTIME_ROOT=""
DISK_BEFORE=""
DISK_AFTER=""
REPORT_FILE=""

fail() {
  echo "PHASE14_V2_GATE_B_READINESS=FAIL" >&2
  echo "REASON=$1" >&2
  echo "HOLDOUT_TOUCHED=false" >&2
  exit 1
}

cleanup() {
  local rc=$?
  trap - EXIT
  set +e
  [[ -n "$RUNTIME_ROOT" ]] && rm -rf "$RUNTIME_ROOT"
  rm -f "$ARCHIVE"
  [[ -n "$DISK_BEFORE" ]] && rm -f "$DISK_BEFORE"
  [[ -n "$DISK_AFTER" ]] && rm -f "$DISK_AFTER"
  [[ -n "$REPORT_FILE" ]] && rm -f "$REPORT_FILE"
  set -e
  exit "$rc"
}
trap cleanup EXIT

read_env() {
  local path=$1
  local key=$2
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
  done < <(git -c safe.directory="$REPO" -C "$REPO" status --porcelain --untracked-files=all)
}

read_recorder_config_workers() {
  sudo -u bp env -u RECORDER_WRITER_WORKERS "$REPO/.venv/bin/python" - "$ENV_FILE" <<'PY'
from __future__ import annotations

import sys

from bp_engine.config import Settings

print(Settings(_env_file=sys.argv[1]).recorder_writer_workers)
PY
}

run_storage_health() {
  local destination=$1
  if ! sudo -u bp "$REPO/.venv/bin/python" "$REPO/scripts/storage_maintenance.py" \
      disk-health --env-file "$ENV_FILE" > "$destination"; then
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
    raise SystemExit("storage health status is not ok")
if payload.get("storage_mode") != "partitioned":
    raise SystemExit("storage mode is not partitioned")
guards = payload.get("guards") or {}
for name in ("maintenance_fresh", "current_partition_present", "retention_current"):
    if guards.get(name) is not True:
        raise SystemExit(f"storage guard not satisfied: {name}")
PY
}

require_services() {
  local service
  for service in "${CORE_SERVICES[@]}"; do
    systemctl is-active --quiet "$service" || fail "service_not_active:$service"
  done
  systemctl is-active --quiet "$MAINTENANCE_TIMER" || fail "maintenance_timer_not_active"
  systemctl is-active --quiet "$DISK_HEALTH_TIMER" || fail "disk_health_timer_not_active"
  systemctl is-active --quiet "$V2_TIMER" || fail "v2_timer_not_active"
  systemctl is-enabled --quiet "$V2_TIMER" || fail "v2_timer_not_enabled"
}

[[ -d "$REPO/.git" ]] || fail "deployed_repo_missing"
[[ -x "$REPO/.venv/bin/python" ]] || fail "production_python_missing"
[[ -r "$STORAGE_EVIDENCE" ]] || fail "storage_evidence_missing"
[[ -r "$ARCHIVE" ]] || fail "candidate_archive_missing"
[[ "$(sha256sum "$ARCHIVE" | awk '{print $1}')" == "$ARCHIVE_SHA256" ]] || fail "candidate_archive_sha256_mismatch"
chmod 0644 "$ARCHIVE"
[[ "$(sha256sum "$STORAGE_EVIDENCE" | awk '{print $1}')" == "$STORAGE_EVIDENCE_SHA256" ]] || fail "storage_evidence_sha256_mismatch"
[[ "$(git -c safe.directory="$REPO" -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]] || fail "unexpected_deployed_head"

validate_deployed_checkout
require_research_zero_money
require_services
[[ "$(read_recorder_config_workers)" == "4" ]] || fail "recorder_config_worker_count_not_4"

DISK_BEFORE=$(mktemp /var/tmp/bp-v2-gate-b-readiness-disk-before.XXXXXX.json)
run_storage_health "$DISK_BEFORE"

RUNTIME_ROOT="/var/tmp/bp-v2-gate-b-readiness-${HELPER_HEAD:0:12}-$$"
install -d -o bp -g bp -m 0750 "$RUNTIME_ROOT"
sudo -u bp tar -xzf "$ARCHIVE" -C "$RUNTIME_ROOT"

[[ -f "$RUNTIME_ROOT/scripts/run_v2_gate_b_research.py" ]] || fail "candidate_gate_b_runner_missing"
[[ -f "$RUNTIME_ROOT/src/bp_engine/v2_research/plan.py" ]] || fail "candidate_gate_b_plan_missing"

REPORT_FILE=$(mktemp /var/tmp/bp-v2-gate-b-readiness-report.XXXXXX.json)
if ! sudo -u bp env \
    MODE=research \
    LIVE_TRADING_ENABLED=false \
    MAX_TRADE_SIZE_USD=0 \
    MAX_DAILY_LOSS_USD=0 \
    PYTHONPATH="$RUNTIME_ROOT/src" \
    "$REPO/.venv/bin/python" "$RUNTIME_ROOT/scripts/run_v2_gate_b_research.py" \
    --env-file "$ENV_FILE" readiness \
    --planning-epoch-start "$PLANNING_EPOCH_START" > "$REPORT_FILE"; then
  fail "readiness_command_failed"
fi

"$REPO/.venv/bin/python" - "$REPORT_FILE" "$PLANNING_EPOCH_START" <<'PY' || fail "readiness_contract_failed"
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected_epoch = datetime.fromisoformat(sys.argv[2].replace("Z", "+00:00")).astimezone(UTC).isoformat()
if payload.get("planning_epoch_start_at") != expected_epoch:
    raise SystemExit("planning epoch mismatch")
if payload.get("labels_read") is not False:
    raise SystemExit("readiness read labels")
if payload.get("plan_artifact_written") is not False:
    raise SystemExit("readiness wrote a plan artifact")
if payload.get("selection_artifact_written") is not False:
    raise SystemExit("readiness wrote a selection artifact")
if payload.get("holdout_touched") is not False:
    raise SystemExit("readiness touched holdout")
if payload.get("minimum_contiguous_epoch_seconds") != 64800.0:
    raise SystemExit("unexpected Gate B minimum contiguous epoch")
if payload.get("required_ordinary_folds") != 3:
    raise SystemExit("unexpected ordinary fold requirement")
if payload.get("coverage_input_sha256") != "aab75574aa7faf18e65358353403e5ec1a2b89dd42424eb7b0e3329bf683b099":
    raise SystemExit("coverage preregistration hash changed")
if payload.get("freshness_candidates_seconds") != [1, 2, 5, 10]:
    raise SystemExit("freshness candidate grid changed")
PY

require_research_zero_money
require_services
[[ "$(read_recorder_config_workers)" == "4" ]] || fail "recorder_config_worker_count_changed"
[[ "$(git -c safe.directory="$REPO" -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]] || fail "deployed_head_changed"

DISK_AFTER=$(mktemp /var/tmp/bp-v2-gate-b-readiness-disk-after.XXXXXX.json)
run_storage_health "$DISK_AFTER"

echo "PHASE14_V2_GATE_B_READINESS=PASS"
echo "HELPER_HEAD=$HELPER_HEAD"
echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"
echo "PLANNING_EPOCH_START=$PLANNING_EPOCH_START"
echo "HOLDOUT_TOUCHED=false"
"$REPO/.venv/bin/python" - "$REPORT_FILE" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(f"READY={str(bool(payload['ready'])).lower()}")
print(f"MARKET_COUNT={payload['market_count']}")
print(f"MARKET_START_AT={payload['market_start_at']}")
print(f"MARKET_END_AT={payload['market_end_at']}")
print(f"AVAILABLE_SPAN_HOURS={payload['available_span_seconds'] / 3600:.6f}")
print(
    "MINIMUM_CONTIGUOUS_EPOCH_HOURS="
    f"{payload['minimum_contiguous_epoch_seconds'] / 3600:.6f}"
)
print(f"REQUIRED_ORDINARY_FOLDS={payload['required_ordinary_folds']}")
print(f"ANALYSIS_START_AT={payload['analysis_start_at']}")
print(f"ELIGIBLE_FOLD_COUNT={payload['eligible_fold_count']}")
print(f"FINAL_HOLDOUT_MARKET_COUNT={payload['final_holdout_market_count']}")
print(f"ANALYSIS_START_ATTEMPT_COUNT={payload['analysis_start_attempt_count']}")
print(f"CANDIDATE_REJECTION_COUNT={len(payload['candidate_rejections'])}")
print(f"BLOCKING_REASON={payload['blocking_reason']}")
print(f"WOULD_PLAN_SHA256={payload['would_plan_sha256']}")
print(
    "READINESS_REPORT="
    + json.dumps(payload, sort_keys=True, separators=(",", ":"))
)
PY
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
echo "PLANNING_EPOCH_START=$PLANNING_EPOCH_START_CANONICAL"
echo "CANDIDATE_ARCHIVE_SHA256=$ARCHIVE_SHA256"
echo "Running feature-only Phase 14 V2 Gate B readiness check."

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo env PHASE14_V2_GATE_B_READINESS_HELPER_HEAD=$HELPER_HEAD_Q PHASE14_V2_GATE_B_READINESS_DEPLOYED_HEAD=$DEPLOYED_HEAD_Q PHASE14_V2_GATE_B_READINESS_ENV_FILE=$ENV_FILE_Q PHASE14_V2_GATE_B_READINESS_STORAGE_EVIDENCE=$STORAGE_EVIDENCE_Q PHASE14_V2_GATE_B_READINESS_STORAGE_EVIDENCE_SHA256=$STORAGE_EVIDENCE_SHA256_Q PHASE14_V2_GATE_B_READINESS_PLANNING_EPOCH_START=$PLANNING_EPOCH_START_Q PHASE14_V2_GATE_B_READINESS_ARCHIVE=$REMOTE_ARCHIVE_Q PHASE14_V2_GATE_B_READINESS_ARCHIVE_SHA256=$ARCHIVE_SHA256_Q bash"
