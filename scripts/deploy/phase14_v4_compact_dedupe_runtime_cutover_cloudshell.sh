#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V4_COMPACT_DEDUPE_RUNTIME_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_V4_COMPACT_DEDUPE_RUNTIME_ZONE:-us-east1-c}"
VM="${PHASE14_V4_COMPACT_DEDUPE_RUNTIME_VM:-bp-recorder}"
HELPER_HEAD="${PHASE14_V4_COMPACT_DEDUPE_RUNTIME_HELPER_HEAD:-}"
STAGE="${PHASE14_V4_COMPACT_DEDUPE_RUNTIME_STAGE:-}"
FROM_HEAD="${PHASE14_V4_COMPACT_DEDUPE_RUNTIME_FROM_HEAD:-}"
TARGET_HEAD="${PHASE14_V4_COMPACT_DEDUPE_RUNTIME_TARGET_HEAD:-}"
TARGET_BRANCH="${PHASE14_V4_COMPACT_DEDUPE_RUNTIME_TARGET_BRANCH:-}"
APPROVAL="${PHASE14_V4_COMPACT_DEDUPE_RUNTIME_APPROVAL:-}"
ENV_FILE="${PHASE14_V4_COMPACT_DEDUPE_RUNTIME_ENV_FILE:-/etc/bp/bp.env}"

fail() {
  echo "PHASE14_V4_COMPACT_DEDUPE_RUNTIME_CUTOVER=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$HELPER_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "helper_head_invalid"
[[ "$FROM_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "from_head_invalid"
[[ "$TARGET_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "target_head_invalid"
[[ "$STAGE" == "writer" || "$STAGE" == "steady" ]] || fail "stage_invalid"
[[ "$TARGET_BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]] || fail "target_branch_invalid"
[[ "$ENV_FILE" == /* ]] || fail "env_file_not_absolute"

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$ROOT" ]] || fail "local_repository_missing"
cd "$ROOT"
[[ "$(git rev-parse HEAD)" == "$HELPER_HEAD" ]] || fail "local_helper_head_mismatch"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || fail "local_working_tree_dirty"

REMOTE_MAIN="$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')"
[[ "$REMOTE_MAIN" == "$HELPER_HEAD" ]] || fail "remote_main_changed"

REMOTE_TARGET="$(
  git ls-remote --heads origin "refs/heads/$TARGET_BRANCH" |
    awk 'NR==1 {print $1}'
)"
[[ "$REMOTE_TARGET" == "$TARGET_HEAD" ]] || fail "target_branch_head_changed"

EXPECTED_APPROVAL="I_APPROVE_PHASE14_V4_COMPACT_DEDUPE_RUNTIME_CUTOVER:$STAGE:$HELPER_HEAD:$FROM_HEAD:$TARGET_HEAD"
[[ "$APPROVAL" == "$EXPECTED_APPROVAL" ]] || fail "production_approval_mismatch"

command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . ||
  fail "gcloud_auth_missing"

printf -v STAGE_Q '%q' "$STAGE"
printf -v FROM_Q '%q' "$FROM_HEAD"
printf -v TARGET_Q '%q' "$TARGET_HEAD"
printf -v BRANCH_Q '%q' "$TARGET_BRANCH"
printf -v ENV_Q '%q' "$ENV_FILE"

read -r -d '' REMOTE <<'REMOTE_SCRIPT' || true
set -Eeuo pipefail

STAGE=__STAGE__
FROM_HEAD=__FROM_HEAD__
TARGET_HEAD=__TARGET_HEAD__
TARGET_BRANCH=__TARGET_BRANCH__
ENV_FILE=__ENV_FILE__
REPO=/opt/bp
RECORDER_UNIT=bp-recorder.service
SHORT="${TARGET_HEAD:0:12}"
WT="/var/tmp/bp-v4-compact-runtime-${SHORT}-$$"
ROLLBACK_WT="/var/tmp/bp-v4-compact-runtime-rollback-${SHORT}-$$"
ROLLBACK_ARMED=0
SOAK_FILE=""
SNAPSHOT_FILE=""

CORE_SERVICES=(
  bp-postgres.service
  bp-recorder.service
  bp-v3-frozen-predictor.service
  bp-v3-paper-execution.service
  bp-dashboard-api.service
  bp-dashboard-web.service
  bp-paper-execution.service
  bp-live-predictor.service
  bp-prospective-outcomes.service
)
REQUIRED_TIMERS=(
  bp-storage-maintenance.timer
  bp-storage-disk-health.timer
  bp-v2-forward-coverage.timer
  bp-v4-forward-coverage.timer
)

fail() {
  echo "PHASE14_V4_COMPACT_DEDUPE_RUNTIME_CUTOVER=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

read_env() {
  awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE"
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
  [[ "$(read_env MODE)" == "research" ]] || fail "mode_not_research"
  [[ "$(read_env LIVE_TRADING_ENABLED)" == "false" ]] || fail "live_trading_enabled"
  [[ "$(read_env MAX_TRADE_SIZE_USD)" == "0" ]] || fail "max_trade_size_nonzero"
  [[ "$(read_env MAX_DAILY_LOSS_USD)" == "0" ]] || fail "max_daily_loss_nonzero"
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

require_services_and_timers() {
  local unit
  for unit in "${CORE_SERVICES[@]}"; do
    systemctl is-active --quiet "$unit" || fail "service_not_active:$unit"
  done
  for unit in "${REQUIRED_TIMERS[@]}"; do
    systemctl is-enabled --quiet "$unit" || fail "timer_not_enabled:$unit"
    systemctl is-active --quiet "$unit" || fail "timer_not_active:$unit"
  done
}

validate_scope() {
  local actual expected
  actual="$(git -C "$REPO" diff --name-only "$FROM_HEAD" "$TARGET_HEAD" | sort)"
  if [[ "$STAGE" == "writer" ]]; then
    expected="src/bp_engine/storage/recorder.py"
    [[ "$actual" == "$expected" ]] || fail "writer_stage_scope_changed"
    git -C "$REPO" diff "$FROM_HEAD" "$TARGET_HEAD" -- src/bp_engine/storage/recorder.py |
      grep -Fq -- '-                ON CONFLICT (dedupe_key) DO NOTHING' ||
      fail "writer_old_conflict_target_missing"
    git -C "$REPO" diff "$FROM_HEAD" "$TARGET_HEAD" -- src/bp_engine/storage/recorder.py |
      grep -Fq -- '+                ON CONFLICT DO NOTHING' ||
      fail "writer_target_free_conflict_missing"
  else
    expected="$(printf '%s\n'       src/bp_engine/storage/partitioned_raw.py       tests/storage/test_partitioned_raw_postgres.py | sort)"
    [[ "$actual" == "$expected" ]] || fail "steady_stage_scope_changed"
    git -C "$REPO" show "$TARGET_HEAD:src/bp_engine/storage/partitioned_raw.py" |
      grep -Fq '_compact_dedupe_indexes_healthy' ||
      fail "steady_compact_validator_missing"
    git -C "$REPO" show "$TARGET_HEAD:src/bp_engine/storage/partitioned_raw.py" |
      grep -Fq 'partitioned dedupe uniqueness contract is missing or unhealthy' ||
      fail "steady_fail_closed_contract_missing"
  fi
}

run_soak_gate() {
  SOAK_FILE="$(mktemp /var/tmp/bp-v4-compact-runtime-soak.XXXXXX.json)"
  if ! sudo -u bp bash -c       'set -a; source "$1"; set +a; exec "$2" "$3" --hours 0.01 --minimum-hours 0.008'       _ "$ENV_FILE" "$REPO/.venv/bin/python" "$REPO/scripts/soak_report.py" > "$SOAK_FILE"; then
    cat "$SOAK_FILE" >&2 || true
    fail "post_restart_soak_failed"
  fi
  "$REPO/.venv/bin/python" - "$SOAK_FILE" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
required = {
    "polymarket/market",
    "bybit/spot",
    "bybit/linear",
    "coinbase/spot",
}
if payload.get("passed") is not True:
    raise SystemExit("soak report did not pass")
feeds = payload.get("feeds") or {}
missing = sorted(
    label
    for label in required
    if int((feeds.get(label) or {}).get("event_count", 0)) <= 0
)
if missing:
    raise SystemExit(f"required feeds missing post-restart events: {missing}")
PY
}

verify_dashboard_safety() {
  SNAPSHOT_FILE="$(mktemp /var/tmp/bp-v4-compact-runtime-snapshot.XXXXXX.json)"
  curl -fsS http://127.0.0.1:8787/api/v1/snapshot > "$SNAPSHOT_FILE" ||
    fail "dashboard_snapshot_unavailable"
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
if mode.get("paper_execution_available") is not True:
    raise SystemExit("dashboard paper execution unavailable")
PY
}

rollback() {
  set +e
  echo "PHASE14_V4_COMPACT_DEDUPE_RUNTIME_ROLLBACK=START" >&2
  git -C "$REPO" worktree add --detach "$ROLLBACK_WT" "$FROM_HEAD" >/dev/null 2>&1
  if [[ -d "$ROLLBACK_WT/.git" || -f "$ROLLBACK_WT/.git" ]]; then
    BP_CANDIDATE_ROOT="$ROLLBACK_WT"     BP_ENV_FILE="$ENV_FILE"       bash "$ROLLBACK_WT/scripts/deploy/phase14_prospective_runtime_install.sh" "$FROM_HEAD" >&2
  else
    git -C "$REPO" checkout --detach --force "$FROM_HEAD" >&2
  fi
  systemctl restart "$RECORDER_UNIT" >&2
  systemctl is-active --quiet "$RECORDER_UNIT" || true
  echo "PHASE14_V4_COMPACT_DEDUPE_RUNTIME_ROLLBACK=COMPLETE" >&2
  set -e
}

cleanup() {
  local rc=$?
  trap - EXIT
  set +e
  if (( rc != 0 && ROLLBACK_ARMED == 1 )); then
    rollback
  fi
  [[ -n "$SOAK_FILE" ]] && rm -f "$SOAK_FILE"
  [[ -n "$SNAPSHOT_FILE" ]] && rm -f "$SNAPSHOT_FILE"
  git -C "$REPO" worktree remove --force "$WT" >/dev/null 2>&1 || true
  git -C "$REPO" worktree remove --force "$ROLLBACK_WT" >/dev/null 2>&1 || true
  set -e
  exit "$rc"
}
trap cleanup EXIT

[[ -d "$REPO/.git" ]] || fail "deployed_repo_missing"
[[ -r "$ENV_FILE" ]] || fail "env_file_missing"
[[ -x "$REPO/.venv/bin/python" ]] || fail "python_runtime_missing"
validate_deployed_checkout
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$FROM_HEAD" ]] || fail "unexpected_deployed_head"
require_research_zero_money
require_automatic_promotion_false
require_services_and_timers

git -C "$REPO" fetch --no-tags origin   "refs/heads/$TARGET_BRANCH:refs/remotes/origin/$TARGET_BRANCH"
[[ "$(git -C "$REPO" rev-parse "refs/remotes/origin/$TARGET_BRANCH")" == "$TARGET_HEAD" ]] ||
  fail "target_head_changed"
git -C "$REPO" merge-base --is-ancestor "$FROM_HEAD" "$TARGET_HEAD" ||
  fail "target_not_descendant_of_from_head"
validate_scope

git -C "$REPO" worktree add --detach "$WT" "$TARGET_HEAD"
[[ "$(git -C "$WT" rev-parse HEAD)" == "$TARGET_HEAD" ]] ||
  fail "target_worktree_head_mismatch"

BP_CANDIDATE_ROOT="$WT" BP_ENV_FILE="$ENV_FILE"   bash "$WT/scripts/deploy/phase14_prospective_runtime_install.sh" "$TARGET_HEAD"

[[ "$(git -C "$REPO" rev-parse HEAD)" == "$TARGET_HEAD" ]] ||
  fail "deployed_head_mismatch_after_install"
validate_deployed_checkout
ROLLBACK_ARMED=1

systemctl restart "$RECORDER_UNIT"
for _ in $(seq 1 30); do
  systemctl is-active --quiet "$RECORDER_UNIT" && break
  sleep 1
done
systemctl is-active --quiet "$RECORDER_UNIT" ||
  fail "recorder_not_active_after_restart"

sleep 45
require_services_and_timers
require_research_zero_money
require_automatic_promotion_false
run_soak_gate
verify_dashboard_safety
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$TARGET_HEAD" ]] ||
  fail "deployed_head_changed_after_validation"

ROLLBACK_ARMED=0

install -d -o bp -g bp /var/lib/bp/evidence
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
evidence="/var/lib/bp/evidence/phase14-v4-compact-dedupe-runtime-${STAGE}-${stamp}-${TARGET_HEAD}.txt"
{
  echo "PHASE14_V4_COMPACT_DEDUPE_RUNTIME_CUTOVER=PASS"
  echo "STAGE=$STAGE"
  echo "FROM_HEAD=$FROM_HEAD"
  echo "TARGET_HEAD=$TARGET_HEAD"
  echo "TARGET_BRANCH=$TARGET_BRANCH"
  echo "RECORDER_ACTIVE=$(systemctl is-active "$RECORDER_UNIT")"
  echo "MODE=$(read_env MODE)"
  echo "LIVE_TRADING_ENABLED=$(read_env LIVE_TRADING_ENABLED)"
  echo "MAX_TRADE_SIZE_USD=$(read_env MAX_TRADE_SIZE_USD)"
  echo "MAX_DAILY_LOSS_USD=$(read_env MAX_DAILY_LOSS_USD)"
  echo "SOAK_REPORT=$(tr -d '\n' < "$SOAK_FILE")"
} | tee "$evidence"
chown bp:bp "$evidence"
chmod 0640 "$evidence"
echo "EVIDENCE_FILE=$evidence"
REMOTE_SCRIPT

REMOTE="${REMOTE/__STAGE__/$STAGE_Q}"
REMOTE="${REMOTE/__FROM_HEAD__/$FROM_Q}"
REMOTE="${REMOTE/__TARGET_HEAD__/$TARGET_Q}"
REMOTE="${REMOTE/__TARGET_BRANCH__/$BRANCH_Q}"
REMOTE="${REMOTE/__ENV_FILE__/$ENV_Q}"

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "HELPER_HEAD=$HELPER_HEAD"
echo "STAGE=$STAGE"
echo "FROM_HEAD=$FROM_HEAD"
echo "TARGET_HEAD=$TARGET_HEAD"
echo "TARGET_BRANCH=$TARGET_BRANCH"
echo "OPERATION=RECORDER_RUNTIME_CUTOVER"
echo "PRODUCTION_MUTATION=true"
echo "DATABASE_SCHEMA_MUTATION=false"
echo "This helper changes the deployed runtime and restarts bp-recorder.service."

REMOTE_OUTPUT="$(mktemp)"
set +e
printf '%s' "$REMOTE" |
  gcloud compute ssh "$VM"     --project="$PROJECT"     --zone="$ZONE"     --quiet     --command='REMOTE_SCRIPT_PATH="$(mktemp /tmp/bp-v4-compact-runtime.XXXXXX.sh)" && cat > "$REMOTE_SCRIPT_PATH" && sudo bash "$REMOTE_SCRIPT_PATH"; rc=$?; rm -f "$REMOTE_SCRIPT_PATH"; exit "$rc"'     2>&1 | tee "$REMOTE_OUTPUT"
PIPE_RC=("${PIPESTATUS[@]}")
set -e

STREAM_RC="${PIPE_RC[0]}"
GCLOUD_RC="${PIPE_RC[1]}"
TEE_RC="${PIPE_RC[2]}"

if ! grep -Eq '^PHASE14_V4_COMPACT_DEDUPE_RUNTIME_CUTOVER=(PASS|FAIL)$' "$REMOTE_OUTPUT"; then
  rm -f "$REMOTE_OUTPUT"
  fail "remote_terminal_marker_missing:stream_rc=$STREAM_RC:gcloud_rc=$GCLOUD_RC:tee_rc=$TEE_RC"
fi
rm -f "$REMOTE_OUTPUT"

(( GCLOUD_RC == 0 )) || exit "$GCLOUD_RC"
(( STREAM_RC == 0 )) || fail "remote_script_stream_failed:rc=$STREAM_RC"
(( TEE_RC == 0 )) || fail "remote_output_capture_failed:rc=$TEE_RC"
