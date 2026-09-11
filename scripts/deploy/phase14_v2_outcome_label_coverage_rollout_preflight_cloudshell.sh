#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V2_OUTCOME_LABEL_ROLLOUT_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_V2_OUTCOME_LABEL_ROLLOUT_ZONE:-us-east1-c}"
VM="${PHASE14_V2_OUTCOME_LABEL_ROLLOUT_VM:-bp-recorder}"
ENV_FILE="${PHASE14_V2_OUTCOME_LABEL_ROLLOUT_ENV_FILE:-/etc/bp/bp.env}"
HELPER_HEAD="${PHASE14_V2_OUTCOME_LABEL_ROLLOUT_HELPER_HEAD:?set exact merged helper/main SHA}"

FROM_HEAD="71b33d3beaba4a11ef93e7c5bde1c517323f3440"
CANDIDATE_BRANCH="ops/phase14-v2-outcome-label-coverage-rollout-candidate"
CANDIDATE_HEAD="7c3af78da1922a0e5187c24b799951130cc98887"
RUNTIME_PATH="src/bp_engine/prospective_outcomes/service.py"
TEST_PATH="tests/prospective_outcomes/test_prospective_outcome_sync_service.py"
STORAGE_EVIDENCE="/mnt/bp-data/evidence/phase14-partitioned-storage-rollout-20260909T070219Z.json"
STORAGE_EVIDENCE_SHA256="f33a28f5306e46c509b0000a176d226c079aa2d160d595095ca228118542ce19"

fail() {
  echo "PHASE14_V2_OUTCOME_LABEL_ROLLOUT_PREFLIGHT=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$HELPER_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "helper_head_invalid"
[[ "$ENV_FILE" == /* ]] || fail "env_file_not_absolute"

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(git -C "$SCRIPT_DIR/../.." rev-parse --show-toplevel)
LOCAL_HEAD=$(git -C "$REPO_ROOT" rev-parse HEAD)
[[ "$LOCAL_HEAD" == "$HELPER_HEAD" ]] || fail "helper_head_mismatch"
[[ -z "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all)" ]] || fail "helper_worktree_dirty"

git -C "$REPO_ROOT" fetch --quiet --no-tags origin \
  "refs/heads/main:refs/remotes/origin/main" \
  "refs/heads/$CANDIDATE_BRANCH:refs/remotes/origin/$CANDIDATE_BRANCH"
REMOTE_MAIN=$(git -C "$REPO_ROOT" rev-parse refs/remotes/origin/main)
[[ "$REMOTE_MAIN" == "$HELPER_HEAD" ]] || fail "remote_main_changed"
REMOTE_CANDIDATE=$(git -C "$REPO_ROOT" rev-parse "refs/remotes/origin/$CANDIDATE_BRANCH")
[[ "$REMOTE_CANDIDATE" == "$CANDIDATE_HEAD" ]] || fail "candidate_branch_changed"

mapfile -t CHANGED_PATHS < <(git -C "$REPO_ROOT" diff --name-only "$FROM_HEAD" "$CANDIDATE_HEAD" | sort)
if [[ "${#CHANGED_PATHS[@]}" -ne 2 || \
      "${CHANGED_PATHS[0]:-}" != "$RUNTIME_PATH" || \
      "${CHANGED_PATHS[1]:-}" != "$TEST_PATH" ]]; then
  fail "candidate_scope_mismatch"
fi

MAIN_RUNTIME_BLOB=$(git -C "$REPO_ROOT" rev-parse "$HELPER_HEAD:$RUNTIME_PATH")
CANDIDATE_RUNTIME_BLOB=$(git -C "$REPO_ROOT" rev-parse "$CANDIDATE_HEAD:$RUNTIME_PATH")
[[ "$CANDIDATE_RUNTIME_BLOB" == "$MAIN_RUNTIME_BLOB" ]] || fail "candidate_runtime_blob_not_exact_main"
MAIN_TEST_BLOB=$(git -C "$REPO_ROOT" rev-parse "$HELPER_HEAD:$TEST_PATH")
CANDIDATE_TEST_BLOB=$(git -C "$REPO_ROOT" rev-parse "$CANDIDATE_HEAD:$TEST_PATH")
[[ "$CANDIDATE_TEST_BLOB" == "$MAIN_TEST_BLOB" ]] || fail "candidate_test_blob_not_exact_main"
FROM_RUNTIME_BLOB=$(git -C "$REPO_ROOT" rev-parse "$FROM_HEAD:$RUNTIME_PATH")

EXPECTED_APPROVAL="I_APPROVE_PHASE14_V2_OUTCOME_LABEL_ROLLOUT:${HELPER_HEAD}:${FROM_HEAD}:${CANDIDATE_HEAD}:${STORAGE_EVIDENCE_SHA256}"

command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . || fail "gcloud_no_active_account"

printf -v HELPER_HEAD_Q '%q' "$HELPER_HEAD"
printf -v FROM_HEAD_Q '%q' "$FROM_HEAD"
printf -v FROM_RUNTIME_BLOB_Q '%q' "$FROM_RUNTIME_BLOB"
printf -v STORAGE_EVIDENCE_Q '%q' "$STORAGE_EVIDENCE"
printf -v STORAGE_EVIDENCE_SHA256_Q '%q' "$STORAGE_EVIDENCE_SHA256"
printf -v ENV_FILE_Q '%q' "$ENV_FILE"

REMOTE_SCRIPT=$(cat <<'REMOTE'
set -Eeuo pipefail

REPO=/opt/bp
HELPER_HEAD="${PHASE14_V2_OUTCOME_LABEL_ROLLOUT_HELPER_HEAD:?}"
FROM_HEAD="${PHASE14_V2_OUTCOME_LABEL_ROLLOUT_FROM_HEAD:?}"
FROM_RUNTIME_BLOB="${PHASE14_V2_OUTCOME_LABEL_ROLLOUT_FROM_RUNTIME_BLOB:?}"
STORAGE_EVIDENCE="${PHASE14_V2_OUTCOME_LABEL_ROLLOUT_STORAGE_EVIDENCE:?}"
STORAGE_EVIDENCE_SHA256="${PHASE14_V2_OUTCOME_LABEL_ROLLOUT_STORAGE_EVIDENCE_SHA256:?}"
ENV_FILE="${PHASE14_V2_OUTCOME_LABEL_ROLLOUT_ENV_FILE:?}"
RUNTIME_PATH=src/bp_engine/prospective_outcomes/service.py
OUTCOME_UNIT=bp-prospective-outcomes.service
SERVICES=(
  bp-recorder.service
  bp-live-predictor.service
  bp-paper-execution.service
  "$OUTCOME_UNIT"
)

fail() {
  echo "PHASE14_V2_OUTCOME_LABEL_ROLLOUT_PREFLIGHT=FAIL" >&2
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
        .node/*|apps/dashboard/.next/*|apps/dashboard/node_modules/*|apps/dashboard/tsconfig.tsbuildinfo)
          ;;
        *)
          fail "unexpected_deployed_checkout_change:$path"
          ;;
      esac
    else
      case "$path" in
        apps/dashboard/next-env.d.ts|apps/dashboard/tsconfig.json)
          ;;
        *)
          fail "unexpected_deployed_checkout_change:$path"
          ;;
      esac
    fi
  done < <(git -C "$REPO" status --porcelain --untracked-files=all)
}

require_research_zero_money() {
  local mode live max_trade max_loss
  mode=$(read_env MODE)
  live=$(read_env LIVE_TRADING_ENABLED)
  max_trade=$(read_env MAX_TRADE_SIZE_USD)
  max_loss=$(read_env MAX_DAILY_LOSS_USD)
  [[ "$mode" == "research" ]] || fail "mode_not_research"
  [[ "$live" == "false" ]] || fail "live_trading_enabled"
  [[ "$max_trade" == "0" ]] || fail "max_trade_size_nonzero"
  [[ "$max_loss" == "0" ]] || fail "max_daily_loss_nonzero"
}

require_automatic_promotion_false() {
  "$REPO/.venv/bin/python" - "$REPO/PROJECT_STATE.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    root = json.load(handle)
values = []

def walk(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "automatic_promotion":
                values.append(nested)
            walk(nested)
    elif isinstance(value, list):
        for nested in value:
            walk(nested)

walk(root)
if not values or any(value is not False for value in values):
    raise SystemExit("automatic_promotion must remain false")
PY
}

validate_storage_health() {
  local storage_json=$1
  "$REPO/.venv/bin/python" - "$storage_json" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if payload.get("status") != "ok":
    raise SystemExit("storage_status_not_ok")
guards = payload.get("guards") or {}
for key in ("maintenance_fresh", "current_partition_present", "retention_current"):
    if guards.get(key) is not True:
        raise SystemExit(f"storage_guard_not_true:{key}")
PY
}

[[ -d "$REPO/.git" ]] || fail "missing_opt_bp_repo"
[[ -r "$ENV_FILE" ]] || fail "missing_environment_file"
[[ -x "$REPO/.venv/bin/python" ]] || fail "missing_python_runtime"
[[ -r "$STORAGE_EVIDENCE" ]] || fail "missing_storage_evidence"
validate_deployed_checkout
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$FROM_HEAD" ]] || fail "deployed_head_mismatch"
[[ "$(git -C "$REPO" rev-parse "HEAD:$RUNTIME_PATH")" == "$FROM_RUNTIME_BLOB" ]] || fail "deployed_runtime_blob_mismatch"

ACTUAL_STORAGE_SHA256=$(sha256sum "$STORAGE_EVIDENCE" | awk '{print $1}')
[[ "$ACTUAL_STORAGE_SHA256" == "$STORAGE_EVIDENCE_SHA256" ]] || fail "storage_evidence_sha256_mismatch"
require_research_zero_money
require_automatic_promotion_false

STORAGE_JSON=$(sudo -u bp "$REPO/.venv/bin/python" "$REPO/scripts/storage_maintenance.py" disk-health --env-file "$ENV_FILE")
validate_storage_health "$STORAGE_JSON"

PID_PAIRS=""
for service in "${SERVICES[@]}"; do
  systemctl is-active --quiet "$service" || fail "service_not_active:$service"
  pid=$(systemctl show "$service" -p MainPID --value)
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || fail "service_pid_invalid:$service"
  if [[ "$service" == "$OUTCOME_UNIT" ]]; then
    echo "OUTCOME_SERVICE_PID=$pid"
  else
    PID_PAIRS+="${service}=${pid};"
  fi
done

echo "UNRELATED_SERVICE_PIDS=$PID_PAIRS"
echo "DEPLOYED_HEAD=$FROM_HEAD"
echo "STORAGE_STATUS=ok"
echo "PRODUCTION_MUTATIONS_PERFORMED=false"
echo "HOLDOUT_ACCESS_PERFORMED=false"
echo "GATE_B_ACTIONS_PERFORMED=false"
REMOTE
)
REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 -w0)

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet \
  --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo env PHASE14_V2_OUTCOME_LABEL_ROLLOUT_HELPER_HEAD=$HELPER_HEAD_Q PHASE14_V2_OUTCOME_LABEL_ROLLOUT_FROM_HEAD=$FROM_HEAD_Q PHASE14_V2_OUTCOME_LABEL_ROLLOUT_FROM_RUNTIME_BLOB=$FROM_RUNTIME_BLOB_Q PHASE14_V2_OUTCOME_LABEL_ROLLOUT_STORAGE_EVIDENCE=$STORAGE_EVIDENCE_Q PHASE14_V2_OUTCOME_LABEL_ROLLOUT_STORAGE_EVIDENCE_SHA256=$STORAGE_EVIDENCE_SHA256_Q PHASE14_V2_OUTCOME_LABEL_ROLLOUT_ENV_FILE=$ENV_FILE_Q bash"

echo "PHASE14_V2_OUTCOME_LABEL_ROLLOUT_PREFLIGHT=PASS"
echo "HELPER_HEAD=$HELPER_HEAD"
echo "FROM_HEAD=$FROM_HEAD"
echo "CANDIDATE_HEAD=$CANDIDATE_HEAD"
echo "CANDIDATE_BRANCH=$CANDIDATE_BRANCH"
echo "RUNTIME_BLOB=$MAIN_RUNTIME_BLOB"
echo "TEST_BLOB=$MAIN_TEST_BLOB"
echo "STORAGE_EVIDENCE_SHA256=$STORAGE_EVIDENCE_SHA256"
echo "EXPECTED_APPROVAL=$EXPECTED_APPROVAL"
echo "PRODUCTION_MUTATIONS_PERFORMED=false"
echo "HOLDOUT_ACCESS_PERFORMED=false"
echo "GATE_B_ACTIONS_PERFORMED=false"
