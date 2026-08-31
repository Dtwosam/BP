#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PROSPECTIVE_OUTCOME_SYNC_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PROSPECTIVE_OUTCOME_SYNC_ZONE:-us-east1-c}"
VM="${PROSPECTIVE_OUTCOME_SYNC_VM:-bp-recorder}"
BRANCH="${PROSPECTIVE_OUTCOME_SYNC_BRANCH:-phase14-prospective-outcome-sync}"
EXPECTED_HEAD="${PROSPECTIVE_OUTCOME_SYNC_HEAD:-}"
ENV_FILE="${PROSPECTIVE_OUTCOME_SYNC_ENV_FILE:-/etc/bp/bp.env}"

if [[ ! "$EXPECTED_HEAD" =~ ^[0-9a-f]{40}$ ]]; then
  echo "PROSPECTIVE_OUTCOME_SYNC_HEAD must be the exact 40-character verified candidate SHA" >&2
  exit 2
fi
if ! [[ "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  echo "PROSPECTIVE_OUTCOME_SYNC_BRANCH contains unsupported characters" >&2
  exit 2
fi
if [[ "$ENV_FILE" != /* ]]; then
  echo "PROSPECTIVE_OUTCOME_SYNC_ENV_FILE must be an absolute path" >&2
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
printf -v BRANCH_Q '%q' "$BRANCH"
printf -v ENV_FILE_Q '%q' "$ENV_FILE"

REMOTE_SCRIPT=$(cat <<'REMOTE'
set -Eeuo pipefail

HEAD="${PROSPECTIVE_OUTCOME_SYNC_HEAD:?}"
BRANCH="${PROSPECTIVE_OUTCOME_SYNC_BRANCH:?}"
ENV_FILE="${PROSPECTIVE_OUTCOME_SYNC_ENV_FILE:?}"
REPO=/opt/bp
PYTHON="$REPO/.venv/bin/python"
PAPER_UNIT=bp-paper-execution.service
PREDICTOR_UNIT=bp-live-predictor.service
SHORT="${HEAD:0:12}"
WT="/var/tmp/bp-prospective-outcome-sync-${SHORT}-$$"

fail() {
  echo "PROSPECTIVE_OUTCOME_SYNC_HOST_ACCEPTANCE=FAIL"
  echo "REASON=$1"
  exit 1
}

cleanup() {
  git -C "$REPO" worktree remove --force "$WT" >/dev/null 2>&1 || true
}
trap cleanup EXIT

read_env() {
  local key=$1
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE"
}

[[ -d "$REPO/.git" ]] || fail "missing_opt_bp_repo"
id -u bp >/dev/null 2>&1 || fail "missing_bp_user"
[[ -x "$PYTHON" ]] || fail "missing_python_runtime"
[[ -r "$ENV_FILE" ]] || fail "missing_environment_file"
systemctl is-active --quiet "$PAPER_UNIT" || fail "paper_service_not_active_before"
PREDICTOR_SERVICE_BEFORE=$(systemctl is-active "$PREDICTOR_UNIT" 2>/dev/null || true)
[[ -n "$PREDICTOR_SERVICE_BEFORE" ]] || PREDICTOR_SERVICE_BEFORE=unknown

DEPLOYED_HEAD_BEFORE=$(git -C "$REPO" rev-parse HEAD)
MODE=$(read_env MODE)
LIVE_TRADING_ENABLED=$(read_env LIVE_TRADING_ENABLED)
MAX_TRADE_SIZE_USD=$(read_env MAX_TRADE_SIZE_USD)
MAX_DAILY_LOSS_USD=$(read_env MAX_DAILY_LOSS_USD)
if [[ "$MODE" != "research" || \
      "$LIVE_TRADING_ENABLED" != "false" || \
      "$MAX_TRADE_SIZE_USD" != "0" || \
      "$MAX_DAILY_LOSS_USD" != "0" ]]; then
  fail "research_money_disabled_interlocks_not_satisfied"
fi

git -C /opt/bp fetch --no-tags origin \
  "refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
FETCHED_HEAD=$(git -C "$REPO" rev-parse "refs/remotes/origin/$BRANCH")
[[ "$FETCHED_HEAD" == "$HEAD" ]] || fail "candidate_head_changed"

git -C /opt/bp worktree add --detach "$WT" "$HEAD"
WORKTREE_HEAD=$(git -C "$WT" rev-parse HEAD)
[[ "$WORKTREE_HEAD" == "$HEAD" ]] || fail "worktree_head_mismatch"

OUTCOME_REPORT=$(sudo -u bp env \
  PYTHONPATH="$WT/src" \
  MODE=research \
  LIVE_TRADING_ENABLED=false \
  MAX_TRADE_SIZE_USD=0 \
  MAX_DAILY_LOSS_USD=0 \
  timeout --signal=TERM --kill-after=5s 90s \
  "$PYTHON" -m bp_engine.prospective_outcomes once \
  --env-file "$ENV_FILE")

printf '%s\n' "$OUTCOME_REPORT" | "$PYTHON" -c '
import json
import sys

report = json.load(sys.stdin)
required = (
    "candidates",
    "pending_markets",
    "resolved_markets",
    "created_snapshots",
    "existing_snapshots",
    "created_labels",
    "existing_labels",
    "created_evaluations",
    "existing_evaluations",
)
if not isinstance(report, dict):
    raise SystemExit("outcome sync report must be a JSON object")
for name in required:
    value = report.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SystemExit(f"invalid outcome sync count: {name}")
if report["candidates"] < 1:
    raise SystemExit("outcome sync acceptance requires at least one candidate")
if report["pending_markets"] + report["resolved_markets"] != report["candidates"]:
    raise SystemExit("outcome sync candidate accounting mismatch")
if report["resolved_markets"] < 1:
    raise SystemExit("outcome sync acceptance requires at least one resolved market")
if report["created_snapshots"] + report["existing_snapshots"] != report["resolved_markets"]:
    raise SystemExit("outcome sync snapshot accounting mismatch")
if report["created_labels"] + report["existing_labels"] < report["resolved_markets"]:
    raise SystemExit("outcome sync label evidence does not cover resolved markets")
if report["created_evaluations"] < report["resolved_markets"]:
    raise SystemExit("outcome sync did not create an evaluation for every resolved candidate")
'

PAPER_REPORT=$(sudo -u bp env \
  PYTHONPATH="$WT/src" \
  BP_ENV_FILE="$ENV_FILE" \
  MODE=research \
  LIVE_TRADING_ENABLED=false \
  MAX_TRADE_SIZE_USD=0 \
  MAX_DAILY_LOSS_USD=0 \
  timeout --signal=TERM --kill-after=5s 120s \
  "$PYTHON" -m bp_engine.execution --once)

printf '%s\n' "$PAPER_REPORT" | "$PYTHON" -c '
import json
import sys

report = json.load(sys.stdin)
if not isinstance(report, dict):
    raise SystemExit("paper execution report must be a JSON object")
'

systemctl is-active --quiet "$PAPER_UNIT" || fail "paper_service_not_active_after"
PREDICTOR_SERVICE_AFTER=$(systemctl is-active "$PREDICTOR_UNIT" 2>/dev/null || true)
[[ -n "$PREDICTOR_SERVICE_AFTER" ]] || PREDICTOR_SERVICE_AFTER=unknown
[[ "$PREDICTOR_SERVICE_AFTER" == "$PREDICTOR_SERVICE_BEFORE" ]] || fail "predictor_service_state_changed"
DEPLOYED_HEAD_AFTER=$(git -C "$REPO" rev-parse HEAD)
[[ "$DEPLOYED_HEAD_AFTER" == "$DEPLOYED_HEAD_BEFORE" ]] || fail "deployed_checkout_changed"

printf '%s\n' "$OUTCOME_REPORT"
printf '%s\n' "$PAPER_REPORT"
echo "PROSPECTIVE_OUTCOME_SYNC_HEAD=$HEAD"
echo "DEPLOYED_HEAD_UNCHANGED=$DEPLOYED_HEAD_AFTER"
echo "PAPER_SERVICE=active"
echo "PREDICTOR_SERVICE_BEFORE=$PREDICTOR_SERVICE_BEFORE"
echo "PREDICTOR_SERVICE_AFTER=$PREDICTOR_SERVICE_AFTER"
echo "PREDICTOR_SERVICE_UNCHANGED=true"
echo "MODE=$MODE"
echo "LIVE_TRADING_ENABLED=$LIVE_TRADING_ENABLED"
echo "MAX_TRADE_SIZE_USD=$MAX_TRADE_SIZE_USD"
echo "MAX_DAILY_LOSS_USD=$MAX_DAILY_LOSS_USD"
echo "PROSPECTIVE_OUTCOME_SYNC_HOST_ACCEPTANCE=PASS"
REMOTE
)
REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 -w0)

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "PROSPECTIVE_OUTCOME_SYNC_HEAD=$EXPECTED_HEAD"
echo "Running exact-head money-disabled prospective outcome acceptance..."

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo env PROSPECTIVE_OUTCOME_SYNC_HEAD=$HEAD_Q PROSPECTIVE_OUTCOME_SYNC_BRANCH=$BRANCH_Q PROSPECTIVE_OUTCOME_SYNC_ENV_FILE=$ENV_FILE_Q bash"
