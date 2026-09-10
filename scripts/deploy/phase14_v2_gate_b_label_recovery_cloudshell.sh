#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V2_GATE_B_LABEL_RECOVERY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
VM="${PHASE14_V2_GATE_B_LABEL_RECOVERY_VM:-bp-recorder}"
ZONE="${PHASE14_V2_GATE_B_LABEL_RECOVERY_ZONE:-us-east1-c}"
DEPLOYED_HEAD="${PHASE14_V2_GATE_B_LABEL_RECOVERY_DEPLOYED_HEAD:-e9c7afc1536880e4612cb6e3d1a7282fa37c69f5}"
ENV_FILE="${PHASE14_V2_GATE_B_LABEL_RECOVERY_ENV_FILE:-/etc/bp/bp.env}"
STORAGE_EVIDENCE="${PHASE14_V2_GATE_B_LABEL_RECOVERY_STORAGE_EVIDENCE:-}"
STORAGE_EVIDENCE_SHA256="${PHASE14_V2_GATE_B_LABEL_RECOVERY_STORAGE_EVIDENCE_SHA256:-}"
PARTIAL_DIR="${PHASE14_V2_GATE_B_LABEL_RECOVERY_PARTIAL_DIR:?set exact failed Gate B evidence directory}"
PLAN_SHA256="${PHASE14_V2_GATE_B_LABEL_RECOVERY_PLAN_SHA256:?set exact frozen plan.json SHA-256}"
ACTION="${PHASE14_V2_GATE_B_LABEL_RECOVERY_ACTION:-audit}"
APPROVAL="${PHASE14_V2_GATE_B_LABEL_RECOVERY_APPROVAL:-}"

case "$ACTION" in
  "audit"|"recover") ;;
  *) echo "unsupported recovery action: $ACTION" >&2; exit 2 ;;
esac

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"
git fetch --quiet origin main
LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_MAIN=$(git rev-parse origin/main)
test "$LOCAL_HEAD" = "$REMOTE_MAIN"
test -z "$(git status --porcelain)"

if [[ ! "$PLAN_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  echo "invalid frozen plan SHA-256" >&2
  exit 2
fi
[[ "$STORAGE_EVIDENCE" == /* ]] || { echo "storage evidence path must be absolute" >&2; exit 2; }
[[ "$STORAGE_EVIDENCE_SHA256" =~ ^[0-9a-f]{64}$ ]] || { echo "invalid storage evidence SHA-256" >&2; exit 2; }
case "$PARTIAL_DIR" in
  /var/lib/bp/evidence/phase14-v2-gate-b-*) ;;
  *) echo "partial directory is outside the canonical Gate B evidence root" >&2; exit 2 ;;
esac

if [[ "$ACTION" == "recover" ]]; then
  EXPECTED_APPROVAL="I_APPROVE_PHASE14_V2_GATE_B_LABEL_RECOVERY:${LOCAL_HEAD}:${PLAN_SHA256}"
  test "$APPROVAL" = "$EXPECTED_APPROVAL"
fi

gcloud config set project "$PROJECT" >/dev/null
gcloud auth print-access-token >/dev/null

ARCHIVE=$(mktemp "$HOME/bp-v2-label-recovery-${LOCAL_HEAD:0:12}.XXXXXX.tar.gz")
trap 'rm -f "$ARCHIVE"' EXIT
git archive --format=tar.gz -o "$ARCHIVE" "$LOCAL_HEAD"
ARCHIVE_SHA256=$(sha256sum "$ARCHIVE" | awk '{print $1}')
REMOTE_ARCHIVE="/tmp/bp-v2-label-recovery-${LOCAL_HEAD}.tar.gz"

gcloud compute scp "$ARCHIVE" "$VM:$REMOTE_ARCHIVE" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet

printf -v HELPER_HEAD_Q '%q' "$LOCAL_HEAD"
printf -v DEPLOYED_HEAD_Q '%q' "$DEPLOYED_HEAD"
printf -v ENV_FILE_Q '%q' "$ENV_FILE"
printf -v STORAGE_EVIDENCE_Q '%q' "$STORAGE_EVIDENCE"
printf -v STORAGE_EVIDENCE_SHA256_Q '%q' "$STORAGE_EVIDENCE_SHA256"
printf -v PARTIAL_DIR_Q '%q' "$PARTIAL_DIR"
printf -v PLAN_SHA256_Q '%q' "$PLAN_SHA256"
printf -v ACTION_Q '%q' "$ACTION"
printf -v ARCHIVE_Q '%q' "$REMOTE_ARCHIVE"
printf -v ARCHIVE_SHA256_Q '%q' "$ARCHIVE_SHA256"

read -r -d '' REMOTE_SCRIPT <<'REMOTE' || true
set -Eeuo pipefail

fail() {
  echo "PHASE14_V2_GATE_B_LABEL_RECOVERY=FAIL" >&2
  echo "REASON=$1" >&2
  echo "HOLDOUT_TOUCHED=false" >&2
  exit 1
}

HELPER_HEAD=${PHASE14_V2_GATE_B_LABEL_RECOVERY_HELPER_HEAD:?}
DEPLOYED_HEAD=${PHASE14_V2_GATE_B_LABEL_RECOVERY_DEPLOYED_HEAD:?}
ENV_FILE=${PHASE14_V2_GATE_B_LABEL_RECOVERY_ENV_FILE:?}
STORAGE_EVIDENCE=${PHASE14_V2_GATE_B_LABEL_RECOVERY_STORAGE_EVIDENCE:?}
STORAGE_EVIDENCE_SHA256=${PHASE14_V2_GATE_B_LABEL_RECOVERY_STORAGE_EVIDENCE_SHA256:?}
PARTIAL_DIR=${PHASE14_V2_GATE_B_LABEL_RECOVERY_PARTIAL_DIR:?}
PLAN_SHA256=${PHASE14_V2_GATE_B_LABEL_RECOVERY_PLAN_SHA256:?}
ACTION=${PHASE14_V2_GATE_B_LABEL_RECOVERY_ACTION:?}
ARCHIVE=${PHASE14_V2_GATE_B_LABEL_RECOVERY_ARCHIVE:?}
ARCHIVE_SHA256=${PHASE14_V2_GATE_B_LABEL_RECOVERY_ARCHIVE_SHA256:?}
REPO=/opt/bp
SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env
CORE_SERVICES=(
  bp-recorder.service
  bp-postgres.service
  bp-dashboard-api.service
  bp-dashboard-web.service
  bp-paper-execution.service
  bp-live-predictor.service
  bp-prospective-outcomes.service
)
DISK_BEFORE=""
DISK_AFTER=""

case "$ACTION" in
  "audit"|"recover") ;;
  *) fail "unsupported_action" ;;
esac
case "$PARTIAL_DIR" in
  /var/lib/bp/evidence/phase14-v2-gate-b-*) ;;
  *) fail "invalid_partial_dir" ;;
esac

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

require_services() {
  local service
  for service in "${CORE_SERVICES[@]}"; do
    systemctl is-active --quiet "$service" || fail "service_not_active:$service"
  done
  systemctl is-active --quiet bp-storage-maintenance.timer || fail "maintenance_timer_not_active"
  systemctl is-active --quiet bp-storage-disk-health.timer || fail "disk_health_timer_not_active"
  systemctl is-active --quiet bp-v2-forward-coverage.timer || fail "v2_timer_not_active"
  systemctl is-enabled --quiet bp-v2-forward-coverage.timer || fail "v2_timer_not_enabled"
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
  sudo -u bp env -u RECORDER_WRITER_WORKERS "$REPO/.venv/bin/python" - "$ENV_FILE" <<'PYWORKERS'
from __future__ import annotations
import sys
from bp_engine.config import Settings
print(Settings(_env_file=sys.argv[1]).recorder_writer_workers)
PYWORKERS
}

run_storage_health() {
  local destination=$1
  if ! sudo -u bp "$REPO/.venv/bin/python" "$REPO/scripts/storage_maintenance.py" \
      disk-health --env-file "$ENV_FILE" > "$destination"; then
    cat "$destination" >&2 || true
    fail "storage_health_command_failed"
  fi
  "$REPO/.venv/bin/python" - "$destination" <<'PYHEALTH'
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
PYHEALTH
}

[[ -d "$REPO/.git" ]] || fail "deployed_repo_missing"
[[ -x "$REPO/.venv/bin/python" ]] || fail "production_python_missing"
[[ -r "$ENV_FILE" ]] || fail "environment_file_missing"
[[ -r "$STORAGE_EVIDENCE" ]] || fail "storage_evidence_missing"
[[ -r "$ARCHIVE" ]] || fail "candidate_archive_missing"
[[ "$(sha256sum "$ARCHIVE" | awk '{print $1}')" == "$ARCHIVE_SHA256" ]] || fail "candidate_archive_sha256_mismatch"
[[ "$(sha256sum "$STORAGE_EVIDENCE" | awk '{print $1}')" == "$STORAGE_EVIDENCE_SHA256" ]] || fail "storage_evidence_sha256_mismatch"
[[ "$(git -c safe.directory="$REPO" -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]] || fail "unexpected_deployed_head"
[[ -d "$PARTIAL_DIR" ]] || fail "partial_dir_missing"
[[ -f "$PARTIAL_DIR/plan.json" ]] || fail "frozen_plan_missing"
test ! -e "$PARTIAL_DIR/selection.json" || fail "selection_already_present"
test ! -e "$PARTIAL_DIR/holdout.json" || fail "holdout_already_present"
test ! -e "$PARTIAL_DIR/summary.json" || fail "summary_already_present"
[[ "$(sha256sum "$PARTIAL_DIR/plan.json" | awk '{print $1}')" == "$PLAN_SHA256" ]] || fail "frozen_plan_sha256_mismatch"

RUNTIME_ROOT=$(mktemp -d /var/tmp/bp-v2-label-recovery.XXXXXX)
BEFORE=$(mktemp /var/tmp/bp-v2-label-audit-before.XXXXXX.json)
AFTER=$(mktemp /var/tmp/bp-v2-label-audit-after.XXXXXX.json)
RECOVERY=$(mktemp /var/tmp/bp-v2-label-recovery.XXXXXX.json)
cleanup() {
  rm -rf "$RUNTIME_ROOT" "$BEFORE" "$AFTER" "$RECOVERY" "$ARCHIVE"
  [[ -z "$DISK_BEFORE" ]] || rm -f "$DISK_BEFORE"
  [[ -z "$DISK_AFTER" ]] || rm -f "$DISK_AFTER"
}
trap cleanup EXIT
validate_deployed_checkout
require_research_zero_money
require_services
[[ "$(read_recorder_config_workers)" == "4" ]] || fail "recorder_config_worker_count_not_4"
DISK_BEFORE=$(mktemp /var/tmp/bp-v2-label-recovery-disk-before.XXXXXX.json)
run_storage_health "$DISK_BEFORE"
chmod 0755 "$RUNTIME_ROOT"
tar -xzf "$ARCHIVE" -C "$RUNTIME_ROOT"
[[ -f "$RUNTIME_ROOT/scripts/run_v2_gate_b_label_recovery.py" ]] || fail "candidate_label_recovery_runner_missing"

run_recovery() {
  sudo -u bp env \
    MODE=research \
    LIVE_TRADING_ENABLED=false \
    MAX_TRADE_SIZE_USD=0 \
    MAX_DAILY_LOSS_USD=0 \
    PYTHONPATH="$RUNTIME_ROOT/src" \
    "$REPO/.venv/bin/python" "$RUNTIME_ROOT/scripts/run_v2_gate_b_label_recovery.py" \
    "$1" --env-file "$ENV_FILE" --plan "$PARTIAL_DIR/plan.json"
}

validate_audit() {
  "$REPO/.venv/bin/python" - "$1" <<'PY'
import json
import sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("holdout_touched") is not False:
    raise SystemExit("holdout_touched must remain false")
if not isinstance(payload.get("missing_label_count"), int):
    raise SystemExit("missing_label_count missing")
PY
}

run_recovery audit > "$BEFORE" || fail "non_holdout_label_audit_failed"
validate_audit "$BEFORE" || fail "non_holdout_label_audit_invalid"

if [[ "$ACTION" == "audit" ]]; then
  require_research_zero_money
  require_services
  [[ "$(read_recorder_config_workers)" == "4" ]] || fail "recorder_config_worker_count_changed"
  validate_deployed_checkout
  [[ "$(git -c safe.directory="$REPO" -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]] || fail "deployed_head_changed"
  DISK_AFTER=$(mktemp /var/tmp/bp-v2-label-recovery-disk-after.XXXXXX.json)
  run_storage_health "$DISK_AFTER"
  cat "$BEFORE"
  echo "PHASE14_V2_GATE_B_LABEL_RECOVERY=PASS"
  echo "ACTION=audit"
  echo "HELPER_HEAD=$HELPER_HEAD"
  echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"
  echo "PARTIAL_EVIDENCE_DIR=$PARTIAL_DIR"
  echo "PLAN_SHA256=$PLAN_SHA256"
  echo "HOLDOUT_TOUCHED=false"
  exit 0
fi

run_recovery recover > "$RECOVERY" || fail "non_holdout_label_recovery_failed"
run_recovery audit > "$AFTER" || fail "post_recovery_audit_failed"
validate_audit "$AFTER" || fail "post_recovery_audit_invalid"

"$REPO/.venv/bin/python" - "$RECOVERY" "$AFTER" <<'PY' || fail "label_recovery_incomplete"
import json
import sys
from pathlib import Path
recovery = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
after = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
if recovery.get("holdout_touched") is not False or after.get("holdout_touched") is not False:
    raise SystemExit("holdout was touched")
if after.get("missing_label_count") != 0:
    raise SystemExit("canonical non-holdout labels are still missing")
PY

require_research_zero_money
require_services
[[ "$(read_recorder_config_workers)" == "4" ]] || fail "recorder_config_worker_count_changed"
validate_deployed_checkout
[[ "$(git -c safe.directory="$REPO" -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]] || fail "deployed_head_changed"
DISK_AFTER=$(mktemp /var/tmp/bp-v2-label-recovery-disk-after.XXXXXX.json)
run_storage_health "$DISK_AFTER"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
EVIDENCE="/var/lib/bp/evidence/phase14-v2-gate-b-label-recovery-${STAMP}.json"
"$REPO/.venv/bin/python" - "$BEFORE" "$RECOVERY" "$AFTER" "$EVIDENCE" "$HELPER_HEAD" "$DEPLOYED_HEAD" "$PARTIAL_DIR" "$PLAN_SHA256" <<'PY'
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
before_path, recovery_path, after_path, output_path, helper_head, deployed_head, partial_dir, plan_sha = sys.argv[1:]
def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
payload = {
    "verdict": "PASS",
    "recorded_at": datetime.now(UTC).isoformat(),
    "helper_head": helper_head,
    "deployed_head": deployed_head,
    "partial_gate_b_evidence_dir": partial_dir,
    "frozen_plan_sha256": plan_sha,
    "scope": "canonical_non_holdout_labels_only",
    "before": load(before_path),
    "recovery": load(recovery_path),
    "after": load(after_path),
    "holdout_touched": False,
    "gate_b_resumed": False,
    "automatic_promotion": False,
}
Path(output_path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
chown bp:bp "$EVIDENCE"
chmod 0640 "$EVIDENCE"

cat "$RECOVERY"
echo "PHASE14_V2_GATE_B_LABEL_RECOVERY=PASS"
echo "ACTION=recover"
echo "HELPER_HEAD=$HELPER_HEAD"
echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"
echo "STORAGE_EVIDENCE=$STORAGE_EVIDENCE"
echo "STORAGE_EVIDENCE_SHA256=$STORAGE_EVIDENCE_SHA256"
echo "PARTIAL_EVIDENCE_DIR=$PARTIAL_DIR"
echo "PLAN_SHA256=$PLAN_SHA256"
echo "RECOVERY_EVIDENCE=$EVIDENCE"
echo "HOLDOUT_TOUCHED=false"
REMOTE

REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 -w0)

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "ACTION=$ACTION"
echo "HELPER_HEAD=$LOCAL_HEAD"
echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"
echo "PARTIAL_EVIDENCE_DIR=$PARTIAL_DIR"
echo "PLAN_SHA256=$PLAN_SHA256"
echo "CANDIDATE_ARCHIVE_SHA256=$ARCHIVE_SHA256"

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo env PHASE14_V2_GATE_B_LABEL_RECOVERY_HELPER_HEAD=$HELPER_HEAD_Q PHASE14_V2_GATE_B_LABEL_RECOVERY_DEPLOYED_HEAD=$DEPLOYED_HEAD_Q PHASE14_V2_GATE_B_LABEL_RECOVERY_ENV_FILE=$ENV_FILE_Q PHASE14_V2_GATE_B_LABEL_RECOVERY_STORAGE_EVIDENCE=$STORAGE_EVIDENCE_Q PHASE14_V2_GATE_B_LABEL_RECOVERY_STORAGE_EVIDENCE_SHA256=$STORAGE_EVIDENCE_SHA256_Q PHASE14_V2_GATE_B_LABEL_RECOVERY_PARTIAL_DIR=$PARTIAL_DIR_Q PHASE14_V2_GATE_B_LABEL_RECOVERY_PLAN_SHA256=$PLAN_SHA256_Q PHASE14_V2_GATE_B_LABEL_RECOVERY_ACTION=$ACTION_Q PHASE14_V2_GATE_B_LABEL_RECOVERY_ARCHIVE=$ARCHIVE_Q PHASE14_V2_GATE_B_LABEL_RECOVERY_ARCHIVE_SHA256=$ARCHIVE_SHA256_Q bash"
