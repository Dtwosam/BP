from pathlib import Path

RECOVERY = Path("scripts/deploy/phase14_v2_gate_b_label_recovery_cloudshell.sh")
RESUME = Path("scripts/deploy/phase14_v2_gate_b_resume_cloudshell.sh")
CONTRACT = Path("tests/v2_research/test_gate_b_contract.py")


def rep(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


r = RECOVERY.read_text(encoding="utf-8")
r = rep(
    r,
    'ENV_FILE="${PHASE14_V2_GATE_B_LABEL_RECOVERY_ENV_FILE:-/etc/bp/bp.env}"\nPARTIAL_DIR=',
    'ENV_FILE="${PHASE14_V2_GATE_B_LABEL_RECOVERY_ENV_FILE:-/etc/bp/bp.env}"\nSTORAGE_EVIDENCE="${PHASE14_V2_GATE_B_LABEL_RECOVERY_STORAGE_EVIDENCE:-}"\nSTORAGE_EVIDENCE_SHA256="${PHASE14_V2_GATE_B_LABEL_RECOVERY_STORAGE_EVIDENCE_SHA256:-}"\nPARTIAL_DIR=',
    "recovery local storage vars",
)
r = rep(
    r,
    'fi\ncase "$PARTIAL_DIR" in\n',
    'fi\n[[ "$STORAGE_EVIDENCE" == /* ]] || { echo "storage evidence path must be absolute" >&2; exit 2; }\n[[ "$STORAGE_EVIDENCE_SHA256" =~ ^[0-9a-f]{64}$ ]] || { echo "invalid storage evidence SHA-256" >&2; exit 2; }\ncase "$PARTIAL_DIR" in\n',
    "recovery local storage validation",
)
r = rep(
    r,
    "printf -v ENV_FILE_Q '%q' \"$ENV_FILE\"\nprintf -v PARTIAL_DIR_Q",
    "printf -v ENV_FILE_Q '%q' \"$ENV_FILE\"\nprintf -v STORAGE_EVIDENCE_Q '%q' \"$STORAGE_EVIDENCE\"\nprintf -v STORAGE_EVIDENCE_SHA256_Q '%q' \"$STORAGE_EVIDENCE_SHA256\"\nprintf -v PARTIAL_DIR_Q",
    "recovery quoted storage vars",
)
r = rep(
    r,
    'ENV_FILE=${PHASE14_V2_GATE_B_LABEL_RECOVERY_ENV_FILE:?}\nPARTIAL_DIR=',
    'ENV_FILE=${PHASE14_V2_GATE_B_LABEL_RECOVERY_ENV_FILE:?}\nSTORAGE_EVIDENCE=${PHASE14_V2_GATE_B_LABEL_RECOVERY_STORAGE_EVIDENCE:?}\nSTORAGE_EVIDENCE_SHA256=${PHASE14_V2_GATE_B_LABEL_RECOVERY_STORAGE_EVIDENCE_SHA256:?}\nPARTIAL_DIR=',
    "recovery remote storage vars",
)
r = rep(
    r,
    'REPO=/opt/bp\n\ncase "$ACTION" in',
    '''REPO=/opt/bp
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

case "$ACTION" in''',
    "recovery runtime constants",
)

recovery_funcs = r'''
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
'''
r = rep(
    r,
    'esac\n\n[[ -d "$REPO/.git" ]] || fail "deployed_repo_missing"',
    'esac\n' + recovery_funcs + '\n[[ -d "$REPO/.git" ]] || fail "deployed_repo_missing"',
    "recovery helper functions",
)
r = rep(
    r,
    '[[ -r "$ENV_FILE" ]] || fail "environment_file_missing"\n[[ -r "$ARCHIVE" ]] || fail "candidate_archive_missing"',
    '[[ -r "$ENV_FILE" ]] || fail "environment_file_missing"\n[[ -r "$STORAGE_EVIDENCE" ]] || fail "storage_evidence_missing"\n[[ -r "$ARCHIVE" ]] || fail "candidate_archive_missing"',
    "recovery storage evidence readable",
)
r = rep(
    r,
    '''[[ "$(sha256sum "$ARCHIVE" | awk '{print $1}')" == "$ARCHIVE_SHA256" ]] || fail "candidate_archive_sha256_mismatch"
[[ "$(git -c safe.directory="$REPO" -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]]''',
    '''[[ "$(sha256sum "$ARCHIVE" | awk '{print $1}')" == "$ARCHIVE_SHA256" ]] || fail "candidate_archive_sha256_mismatch"
[[ "$(sha256sum "$STORAGE_EVIDENCE" | awk '{print $1}')" == "$STORAGE_EVIDENCE_SHA256" ]] || fail "storage_evidence_sha256_mismatch"
[[ "$(git -c safe.directory="$REPO" -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]]''',
    "recovery storage digest",
)
r = rep(
    r,
    '''cleanup() {
  rm -rf "$RUNTIME_ROOT" "$BEFORE" "$AFTER" "$RECOVERY" "$ARCHIVE"
}''',
    '''cleanup() {
  rm -rf "$RUNTIME_ROOT" "$BEFORE" "$AFTER" "$RECOVERY" "$ARCHIVE"
  [[ -z "$DISK_BEFORE" ]] || rm -f "$DISK_BEFORE"
  [[ -z "$DISK_AFTER" ]] || rm -f "$DISK_AFTER"
}''',
    "recovery cleanup",
)
r = rep(
    r,
    'trap cleanup EXIT\nchmod 0755 "$RUNTIME_ROOT"',
    '''trap cleanup EXIT
validate_deployed_checkout
require_research_zero_money
require_services
[[ "$(read_recorder_config_workers)" == "4" ]] || fail "recorder_config_worker_count_not_4"
DISK_BEFORE=$(mktemp /var/tmp/bp-v2-label-recovery-disk-before.XXXXXX.json)
run_storage_health "$DISK_BEFORE"
chmod 0755 "$RUNTIME_ROOT"''',
    "recovery precheck",
)
r = rep(
    r,
    'if [[ "$ACTION" == "audit" ]]; then\n  cat "$BEFORE"',
    '''if [[ "$ACTION" == "audit" ]]; then
  require_research_zero_money
  require_services
  [[ "$(read_recorder_config_workers)" == "4" ]] || fail "recorder_config_worker_count_changed"
  validate_deployed_checkout
  [[ "$(git -c safe.directory="$REPO" -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]] || fail "deployed_head_changed"
  DISK_AFTER=$(mktemp /var/tmp/bp-v2-label-recovery-disk-after.XXXXXX.json)
  run_storage_health "$DISK_AFTER"
  cat "$BEFORE"''',
    "recovery audit postcheck",
)
r = rep(
    r,
    'PY\n\nSTAMP=$(date -u +%Y%m%dT%H%M%SZ)',
    '''PY

require_research_zero_money
require_services
[[ "$(read_recorder_config_workers)" == "4" ]] || fail "recorder_config_worker_count_changed"
validate_deployed_checkout
[[ "$(git -c safe.directory="$REPO" -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]] || fail "deployed_head_changed"
DISK_AFTER=$(mktemp /var/tmp/bp-v2-label-recovery-disk-after.XXXXXX.json)
run_storage_health "$DISK_AFTER"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)''',
    "recovery mutation postcheck",
)
r = rep(
    r,
    'echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"\necho "PARTIAL_EVIDENCE_DIR=$PARTIAL_DIR"',
    'echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"\necho "STORAGE_EVIDENCE=$STORAGE_EVIDENCE"\necho "STORAGE_EVIDENCE_SHA256=$STORAGE_EVIDENCE_SHA256"\necho "PARTIAL_EVIDENCE_DIR=$PARTIAL_DIR"',
    "recovery local evidence echo",
)
r = rep(
    r,
    'PHASE14_V2_GATE_B_LABEL_RECOVERY_ENV_FILE=$ENV_FILE_Q PHASE14_V2_GATE_B_LABEL_RECOVERY_PARTIAL_DIR=',
    'PHASE14_V2_GATE_B_LABEL_RECOVERY_ENV_FILE=$ENV_FILE_Q PHASE14_V2_GATE_B_LABEL_RECOVERY_STORAGE_EVIDENCE=$STORAGE_EVIDENCE_Q PHASE14_V2_GATE_B_LABEL_RECOVERY_STORAGE_EVIDENCE_SHA256=$STORAGE_EVIDENCE_SHA256_Q PHASE14_V2_GATE_B_LABEL_RECOVERY_PARTIAL_DIR=',
    "recovery remote command storage binding",
)
RECOVERY.write_text(r, encoding="utf-8")

s = RESUME.read_text(encoding="utf-8")
s = rep(
    s,
    'ENV_FILE="${PHASE14_V2_GATE_B_RESUME_ENV_FILE:-/etc/bp/bp.env}"\nPARTIAL_DIR=',
    'ENV_FILE="${PHASE14_V2_GATE_B_RESUME_ENV_FILE:-/etc/bp/bp.env}"\nSTORAGE_EVIDENCE="${PHASE14_V2_GATE_B_RESUME_STORAGE_EVIDENCE:-}"\nSTORAGE_EVIDENCE_SHA256="${PHASE14_V2_GATE_B_RESUME_STORAGE_EVIDENCE_SHA256:-}"\nPARTIAL_DIR=',
    "resume local storage vars",
)
s = rep(
    s,
    'fi\ncase "$PARTIAL_DIR" in\n',
    'fi\n[[ "$STORAGE_EVIDENCE" == /* ]] || { echo "storage evidence path must be absolute" >&2; exit 2; }\n[[ "$STORAGE_EVIDENCE_SHA256" =~ ^[0-9a-f]{64}$ ]] || { echo "invalid storage evidence SHA-256" >&2; exit 2; }\ncase "$PARTIAL_DIR" in\n',
    "resume local storage validation",
)
s = rep(
    s,
    "printf -v ENV_FILE_Q '%q' \"$ENV_FILE\"\nprintf -v PARTIAL_DIR_Q",
    "printf -v ENV_FILE_Q '%q' \"$ENV_FILE\"\nprintf -v STORAGE_EVIDENCE_Q '%q' \"$STORAGE_EVIDENCE\"\nprintf -v STORAGE_EVIDENCE_SHA256_Q '%q' \"$STORAGE_EVIDENCE_SHA256\"\nprintf -v PARTIAL_DIR_Q",
    "resume quoted storage vars",
)
s = rep(
    s,
    'ENV_FILE=${PHASE14_V2_GATE_B_RESUME_ENV_FILE:?}\nPARTIAL_DIR=',
    'ENV_FILE=${PHASE14_V2_GATE_B_RESUME_ENV_FILE:?}\nSTORAGE_EVIDENCE=${PHASE14_V2_GATE_B_RESUME_STORAGE_EVIDENCE:?}\nSTORAGE_EVIDENCE_SHA256=${PHASE14_V2_GATE_B_RESUME_STORAGE_EVIDENCE_SHA256:?}\nPARTIAL_DIR=',
    "resume remote storage vars",
)
s = rep(s, ')\n\nfail() {', ')\nDISK_BEFORE=""\nDISK_AFTER=""\n\nfail() {', "resume disk vars")
s = rep(
    s,
    '[[ -r "$SAFETY_FILE" ]] || fail "runtime_safety_file_missing"\n[[ -r "$ARCHIVE" ]]',
    '[[ -r "$SAFETY_FILE" ]] || fail "runtime_safety_file_missing"\n[[ -r "$STORAGE_EVIDENCE" ]] || fail "storage_evidence_missing"\n[[ -r "$ARCHIVE" ]]',
    "resume storage evidence readable",
)
s = rep(
    s,
    '''[[ "$(sha256sum "$ARCHIVE" | awk '{print $1}')" == "$ARCHIVE_SHA256" ]] || fail "candidate_archive_sha256_mismatch"
[[ "$(git -c safe.directory="$REPO" -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]]''',
    '''[[ "$(sha256sum "$ARCHIVE" | awk '{print $1}')" == "$ARCHIVE_SHA256" ]] || fail "candidate_archive_sha256_mismatch"
[[ "$(sha256sum "$STORAGE_EVIDENCE" | awk '{print $1}')" == "$STORAGE_EVIDENCE_SHA256" ]] || fail "storage_evidence_sha256_mismatch"
[[ "$(git -c safe.directory="$REPO" -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]]''',
    "resume storage digest",
)

resume_funcs = r'''
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
'''
s = rep(
    s,
    '  systemctl is-active --quiet bp-v2-forward-coverage.timer || fail "v2_timer_not_active"\n}\nrequire_research_zero_money',
    '  systemctl is-active --quiet bp-v2-forward-coverage.timer || fail "v2_timer_not_active"\n  systemctl is-enabled --quiet bp-v2-forward-coverage.timer || fail "v2_timer_not_enabled"\n}\n' + resume_funcs + '\nrequire_research_zero_money',
    "resume helper functions",
)
s = rep(
    s,
    'cleanup() {\n  rm -rf "$RUNTIME_ROOT" "$AUDIT" "$ARCHIVE"\n}',
    'cleanup() {\n  rm -rf "$RUNTIME_ROOT" "$AUDIT" "$ARCHIVE"\n  [[ -z "$DISK_BEFORE" ]] || rm -f "$DISK_BEFORE"\n  [[ -z "$DISK_AFTER" ]] || rm -f "$DISK_AFTER"\n}',
    "resume cleanup",
)
s = rep(
    s,
    'trap cleanup EXIT\nchmod 0755 "$RUNTIME_ROOT"',
    'trap cleanup EXIT\nvalidate_deployed_checkout\n[[ "$(read_recorder_config_workers)" == "4" ]] || fail "recorder_config_worker_count_not_4"\nDISK_BEFORE=$(mktemp /var/tmp/bp-v2-gate-b-resume-disk-before.XXXXXX.json)\nrun_storage_health "$DISK_BEFORE"\nchmod 0755 "$RUNTIME_ROOT"',
    "resume precheck",
)
s = rep(
    s,
    'require_research_zero_money\nrequire_services\n[[ "$(git -c safe.directory="$REPO" -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]] || fail "deployed_head_changed"',
    'require_research_zero_money\nrequire_services\n[[ "$(read_recorder_config_workers)" == "4" ]] || fail "recorder_config_worker_count_changed"\nvalidate_deployed_checkout\n[[ "$(git -c safe.directory="$REPO" -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]] || fail "deployed_head_changed"\nDISK_AFTER=$(mktemp /var/tmp/bp-v2-gate-b-resume-disk-after.XXXXXX.json)\nrun_storage_health "$DISK_AFTER"',
    "resume postcheck",
)
s = rep(
    s,
    'echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"\necho "PARTIAL_EVIDENCE_DIR=$PARTIAL_DIR"',
    'echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"\necho "STORAGE_EVIDENCE=$STORAGE_EVIDENCE"\necho "STORAGE_EVIDENCE_SHA256=$STORAGE_EVIDENCE_SHA256"\necho "PARTIAL_EVIDENCE_DIR=$PARTIAL_DIR"',
    "resume local evidence echo",
)
s = rep(
    s,
    'PHASE14_V2_GATE_B_RESUME_ENV_FILE=$ENV_FILE_Q PHASE14_V2_GATE_B_RESUME_PARTIAL_DIR=',
    'PHASE14_V2_GATE_B_RESUME_ENV_FILE=$ENV_FILE_Q PHASE14_V2_GATE_B_RESUME_STORAGE_EVIDENCE=$STORAGE_EVIDENCE_Q PHASE14_V2_GATE_B_RESUME_STORAGE_EVIDENCE_SHA256=$STORAGE_EVIDENCE_SHA256_Q PHASE14_V2_GATE_B_RESUME_PARTIAL_DIR=',
    "resume remote command storage binding",
)
RESUME.write_text(s, encoding="utf-8")

c = CONTRACT.read_text(encoding="utf-8")
c = rep(
    c,
    'assert checkpoint["v2_gate_b_research_gate_b_execution_status"] == "AUTHORIZED_NOT_RUN"',
    'assert checkpoint["v2_gate_b_research_gate_b_execution_status"] == (\n        "FAILED_PRE_HOLDOUT_NON_HOLDOUT_LABEL_GAP_RECOVERY_ENGINEERING_READY"\n    )',
    "execution status assertion",
)
CONTRACT.write_text(c, encoding="utf-8")
