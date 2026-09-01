#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_RECORDER_RELIABILITY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_RECORDER_RELIABILITY_ZONE:-us-east1-c}"
VM="${PHASE14_RECORDER_RELIABILITY_VM:-bp-recorder}"
BRANCH="${PHASE14_RECORDER_RELIABILITY_BRANCH:-main}"
EXPECTED_HEAD="${PHASE14_RECORDER_RELIABILITY_HEAD:-}"
EXPECTED_FROM_HEAD="${PHASE14_RECORDER_RELIABILITY_FROM_HEAD:-be4d866b46cbe13a4f12f43e580486ab46c0ad28}"
ENV_FILE="${PHASE14_RECORDER_RELIABILITY_ENV_FILE:-/etc/bp/bp.env}"

if [[ ! "$EXPECTED_HEAD" =~ ^[0-9a-f]{40}$ ]]; then
  echo "PHASE14_RECORDER_RELIABILITY_HEAD must be the exact 40-character verified candidate SHA" >&2
  exit 2
fi
if [[ ! "$EXPECTED_FROM_HEAD" =~ ^[0-9a-f]{40}$ ]]; then
  echo "PHASE14_RECORDER_RELIABILITY_FROM_HEAD must be the exact expected deployed SHA" >&2
  exit 2
fi
if ! [[ "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  echo "PHASE14_RECORDER_RELIABILITY_BRANCH contains unsupported characters" >&2
  exit 2
fi
if [[ "$ENV_FILE" != /* ]]; then
  echo "PHASE14_RECORDER_RELIABILITY_ENV_FILE must be an absolute path" >&2
  exit 2
fi
if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud is required; run this helper from Google Cloud Shell" >&2
  exit 2
fi
if ! gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q .; then
  echo "no active gcloud account; authorize Cloud Shell and rerun" >&2
  exit 2
fi

gcloud config set project "$PROJECT" >/dev/null

printf -v HEAD_Q '%q' "$EXPECTED_HEAD"
printf -v FROM_HEAD_Q '%q' "$EXPECTED_FROM_HEAD"
printf -v BRANCH_Q '%q' "$BRANCH"
printf -v ENV_FILE_Q '%q' "$ENV_FILE"

REMOTE_SCRIPT=$(cat <<'REMOTE'
set -Eeuo pipefail

SHA="${PHASE14_RECORDER_RELIABILITY_HEAD:?}"
EXPECTED_FROM_HEAD="${PHASE14_RECORDER_RELIABILITY_FROM_HEAD:?}"
BRANCH="${PHASE14_RECORDER_RELIABILITY_BRANCH:?}"
ENV_FILE="${PHASE14_RECORDER_RELIABILITY_ENV_FILE:?}"
REPO=/opt/bp
SHORT="${SHA:0:12}"
WT="/var/tmp/bp-phase14-recorder-reliability-${SHORT}-$$"
ROLLBACK_WT="/var/tmp/bp-phase14-recorder-reliability-rollback-${SHORT}-$$"
RECORDER_UNIT=bp-recorder.service
PREDICTOR_UNIT=bp-live-predictor.service
OUTCOME_UNIT=bp-prospective-outcomes.service
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
OLD_HEAD=""
DISK_BEFORE=""
DISK_AFTER=""
SOAK_FILE=""
SNAPSHOT_FILE=""

fail() {
  echo "PHASE14_RECORDER_RELIABILITY_ROLLOUT=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

read_env() {
  local key=$1
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE"
}

validate_deployed_checkout() {
  local entry
  local code
  local path

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
  done < <(git -C /opt/bp status --porcelain --untracked-files=all)
}

validate_rollout_scope() {
  local path
  local reliability_seen=0
  local runner_seen=0

  while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    case "$path" in
      src/bp_engine/collectors/reliability.py)
        reliability_seen=1
        ;;
      src/bp_engine/collectors/websocket_runner.py)
        runner_seen=1
        ;;
      tests/*|docs/*|.github/workflows/ci.yml|scripts/deploy/phase14_recorder_reliability_rollout_cloudshell.sh)
        ;;
      *)
        fail "unexpected_rollout_path:$path"
        ;;
    esac
  done < <(git -C "$REPO" diff --name-only "$OLD_HEAD" "$SHA")

  (( reliability_seen )) || fail "collector_reliability_change_missing"
  (( runner_seen )) || fail "websocket_runner_change_missing"
}

require_research_zero_money() {
  local mode
  local live_trading_enabled
  local max_trade_size_usd
  local max_daily_loss_usd

  mode=$(read_env MODE)
  live_trading_enabled=$(read_env LIVE_TRADING_ENABLED)
  max_trade_size_usd=$(read_env MAX_TRADE_SIZE_USD)
  max_daily_loss_usd=$(read_env MAX_DAILY_LOSS_USD)
  if [[ "$mode" != "research" || \
        "$live_trading_enabled" != "false" || \
        "$max_trade_size_usd" != "0" || \
        "$max_daily_loss_usd" != "0" ]]; then
    fail "research_zero_money_boundary_not_satisfied"
  fi
  MODE=$mode
  LIVE_TRADING_ENABLED=$live_trading_enabled
  MAX_TRADE_SIZE_USD=$max_trade_size_usd
  MAX_DAILY_LOSS_USD=$max_daily_loss_usd
}

require_services_active() {
  local service
  for service in "${CORE_SERVICES[@]}"; do
    systemctl is-active --quiet "$service" || fail "service_not_active:$service"
  done
  systemctl is-enabled --quiet "$PREDICTOR_UNIT" || fail "service_not_enabled:$PREDICTOR_UNIT"
  systemctl is-enabled --quiet "$OUTCOME_UNIT" || fail "service_not_enabled:$OUTCOME_UNIT"
}

run_disk_health() {
  local destination=$1
  if ! sudo -u bp "$REPO/.venv/bin/python" "$REPO/scripts/storage_maintenance.py" disk-health \
      --env-file "$ENV_FILE" > "$destination"; then
    cat "$destination" >&2 || true
    fail "disk_health_command_failed"
  fi
  "$REPO/.venv/bin/python" - "$destination" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("status") != "ok":
    raise SystemExit(f"disk status is not ok: {payload.get('status')!r}")
PY
}

run_soak_gate() {
  SOAK_FILE=$(mktemp /var/tmp/bp-phase14-recorder-reliability-soak.XXXXXX.json)
  if ! sudo -u bp bash -c \
      'set -a; source "$1"; set +a; exec "$2" "$3" --hours 0.01 --minimum-hours 0.008' \
      _ "$ENV_FILE" "$REPO/.venv/bin/python" "$REPO/scripts/soak_report.py" > "$SOAK_FILE"; then
    cat "$SOAK_FILE" >&2 || true
    fail "post_restart_soak_failed"
  fi
  "$REPO/.venv/bin/python" - "$SOAK_FILE" <<'PY'
from __future__ import annotations

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
missing = sorted(label for label in required if int((feeds.get(label) or {}).get("event_count", 0)) <= 0)
if missing:
    raise SystemExit(f"required feeds missing post-restart events: {missing}")
PY
}

verify_dashboard_safety() {
  SNAPSHOT_FILE=$(mktemp /var/tmp/bp-phase14-recorder-reliability-snapshot.XXXXXX.json)
  if ! curl -fsS http://127.0.0.1:8787/api/v1/snapshot > "$SNAPSHOT_FILE"; then
    fail "dashboard_snapshot_unavailable"
  fi
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

rollback_recorder_reliability_rollout() {
  set +e
  echo "Rolling back Phase 14 recorder reliability rollout..." >&2
  git -C "$REPO" worktree add --detach "$ROLLBACK_WT" "$OLD_HEAD" >/dev/null 2>&1
  if [[ -d "$ROLLBACK_WT/.git" || -f "$ROLLBACK_WT/.git" ]]; then
    BP_CANDIDATE_ROOT="$ROLLBACK_WT" \
    BP_ENV_FILE="$ENV_FILE" \
    bash "$ROLLBACK_WT/scripts/deploy/phase14_prospective_runtime_install.sh" "$OLD_HEAD" >&2
  else
    git -C "$REPO" checkout --detach --force "$OLD_HEAD" >&2
  fi
  systemctl restart "$RECORDER_UNIT" >&2
  systemctl is-active --quiet "$RECORDER_UNIT" || true
  set -e
}

cleanup() {
  local rc=$?
  trap - EXIT
  set +e
  if (( rc != 0 && ROLLBACK_ARMED )); then
    rollback_recorder_reliability_rollout
  fi
  [[ -n "$DISK_BEFORE" ]] && rm -f "$DISK_BEFORE"
  [[ -n "$DISK_AFTER" ]] && rm -f "$DISK_AFTER"
  [[ -n "$SOAK_FILE" ]] && rm -f "$SOAK_FILE"
  [[ -n "$SNAPSHOT_FILE" ]] && rm -f "$SNAPSHOT_FILE"
  git -C "$REPO" worktree remove --force "$WT" >/dev/null 2>&1 || true
  git -C "$REPO" worktree remove --force "$ROLLBACK_WT" >/dev/null 2>&1 || true
  set -e
  exit "$rc"
}
trap cleanup EXIT

[[ -d "$REPO/.git" ]] || fail "missing_opt_bp_repo"
[[ -r "$ENV_FILE" ]] || fail "missing_environment_file"
[[ -x "$REPO/.venv/bin/python" ]] || fail "missing_python_runtime"
id -u bp >/dev/null 2>&1 || fail "missing_bp_user"
validate_deployed_checkout

OLD_HEAD=$(git -C "$REPO" rev-parse HEAD)
[[ "$OLD_HEAD" == "$EXPECTED_FROM_HEAD" ]] || fail "unexpected_deployed_head:$OLD_HEAD"
[[ "$SHA" != "$OLD_HEAD" ]] || fail "candidate_already_deployed"
require_services_active
require_research_zero_money

DISK_BEFORE=$(mktemp /var/tmp/bp-phase14-recorder-reliability-disk-before.XXXXXX.json)
run_disk_health "$DISK_BEFORE"

git -C /opt/bp fetch --no-tags origin \
  "refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
FETCHED=$(git -C /opt/bp rev-parse "refs/remotes/origin/$BRANCH")
if [[ "$FETCHED" != "$SHA" ]]; then
  fail "candidate_head_changed"
fi
git -C "$REPO" merge-base --is-ancestor "$OLD_HEAD" "$SHA" || fail "candidate_not_descendant_of_deployed_head"
validate_rollout_scope

git -C "$REPO" worktree add --detach "$WT" "$SHA"
[[ "$(git -C "$WT" rev-parse HEAD)" == "$SHA" ]] || fail "candidate_worktree_head_mismatch"

BP_CANDIDATE_ROOT="$WT" \
BP_ENV_FILE="$ENV_FILE" \
bash "$WT/scripts/deploy/phase14_prospective_runtime_install.sh" "$SHA"

[[ "$(git -C "$REPO" rev-parse HEAD)" == "$SHA" ]] || fail "deployed_head_mismatch_after_runtime_install"
validate_deployed_checkout
ROLLBACK_ARMED=1

systemctl restart "$RECORDER_UNIT"
for _ in $(seq 1 30); do
  systemctl is-active --quiet "$RECORDER_UNIT" && break
  sleep 1
done
systemctl is-active --quiet "$RECORDER_UNIT" || fail "recorder_not_active_after_restart"

sleep 45
require_services_active
require_research_zero_money
run_soak_gate
verify_dashboard_safety

DISK_AFTER=$(mktemp /var/tmp/bp-phase14-recorder-reliability-disk-after.XXXXXX.json)
run_disk_health "$DISK_AFTER"
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$SHA" ]] || fail "deployed_head_changed_after_rollout"
validate_deployed_checkout

ROLLBACK_ARMED=0
install -d -o bp -g bp /var/lib/bp/evidence
stamp=$(date -u +%Y%m%dT%H%M%SZ)
evidence_file="/var/lib/bp/evidence/phase14-recorder-reliability-rollout-$stamp.txt"
{
  echo "PHASE14_RECORDER_RELIABILITY_ROLLOUT=PASS"
  echo "OLD_HEAD=$OLD_HEAD"
  echo "NEW_HEAD=$(git -C "$REPO" rev-parse HEAD)"
  echo "RECORDER_ACTIVE=$(systemctl is-active "$RECORDER_UNIT")"
  echo "PREDICTOR_ACTIVE=$(systemctl is-active "$PREDICTOR_UNIT")"
  echo "PREDICTOR_ENABLED=$(systemctl is-enabled "$PREDICTOR_UNIT")"
  echo "OUTCOME_ACTIVE=$(systemctl is-active "$OUTCOME_UNIT")"
  echo "OUTCOME_ENABLED=$(systemctl is-enabled "$OUTCOME_UNIT")"
  for service in "${CORE_SERVICES[@]}"; do
    echo "SERVICE=${service}:$(systemctl is-active "$service")"
  done
  echo "MODE=$MODE"
  echo "LIVE_TRADING_ENABLED=$LIVE_TRADING_ENABLED"
  echo "MAX_TRADE_SIZE_USD=$MAX_TRADE_SIZE_USD"
  echo "MAX_DAILY_LOSS_USD=$MAX_DAILY_LOSS_USD"
  echo "DISK_BEFORE=$(tr -d '\n' < "$DISK_BEFORE")"
  echo "DISK_AFTER=$(tr -d '\n' < "$DISK_AFTER")"
  echo "SOAK_REPORT=$(tr -d '\n' < "$SOAK_FILE")"
} | tee "$evidence_file"
chown bp:bp "$evidence_file"
chmod 0640 "$evidence_file"

echo "EVIDENCE_FILE=$evidence_file"
echo "PHASE14_RECORDER_RELIABILITY_ROLLOUT=PASS"
REMOTE
)
REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 -w0)

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "PHASE14_RECORDER_RELIABILITY_FROM_HEAD=$EXPECTED_FROM_HEAD"
echo "PHASE14_RECORDER_RELIABILITY_HEAD=$EXPECTED_HEAD"
echo "Running exact-head research-only recorder reliability rollout..."

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo env PHASE14_RECORDER_RELIABILITY_HEAD=$HEAD_Q PHASE14_RECORDER_RELIABILITY_FROM_HEAD=$FROM_HEAD_Q PHASE14_RECORDER_RELIABILITY_BRANCH=$BRANCH_Q PHASE14_RECORDER_RELIABILITY_ENV_FILE=$ENV_FILE_Q bash"
