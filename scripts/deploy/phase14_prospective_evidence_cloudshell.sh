#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PROSPECTIVE_EVIDENCE_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PROSPECTIVE_EVIDENCE_ZONE:-us-east1-c}"
VM="${PROSPECTIVE_EVIDENCE_VM:-bp-recorder}"
BRANCH="${PROSPECTIVE_EVIDENCE_BRANCH:-phase14-prospective-evidence}"
EXPECTED_HEAD="${PROSPECTIVE_EVIDENCE_HEAD:-}"
ENV_FILE="${PROSPECTIVE_EVIDENCE_ENV_FILE:-/etc/bp/bp.env}"

if [[ ! "$EXPECTED_HEAD" =~ ^[0-9a-f]{40}$ ]]; then
  echo "PROSPECTIVE_EVIDENCE_HEAD must be the exact 40-character verified candidate SHA" >&2
  exit 2
fi
if ! [[ "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  echo "PROSPECTIVE_EVIDENCE_BRANCH contains unsupported characters" >&2
  exit 2
fi
if [[ "$ENV_FILE" != /* ]]; then
  echo "PROSPECTIVE_EVIDENCE_ENV_FILE must be an absolute path" >&2
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

HEAD="${PROSPECTIVE_EVIDENCE_HEAD:?}"
BRANCH="${PROSPECTIVE_EVIDENCE_BRANCH:?}"
ENV_FILE="${PROSPECTIVE_EVIDENCE_ENV_FILE:?}"
REPO=/opt/bp
PYTHON="$REPO/.venv/bin/python"
PAPER_UNIT=bp-paper-execution.service
SHORT="${HEAD:0:12}"
WT="/var/tmp/bp-prospective-evidence-${SHORT}-$$"

fail() {
  echo "PROSPECTIVE_EVIDENCE_HOST_REPORT=FAIL"
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

DEPLOYED_HEAD_BEFORE=$(git -C "$REPO" rev-parse HEAD)
LIVE_TRADING_ENABLED=$(read_env LIVE_TRADING_ENABLED)
MAX_TRADE_SIZE_USD=$(read_env MAX_TRADE_SIZE_USD)
MAX_DAILY_LOSS_USD=$(read_env MAX_DAILY_LOSS_USD)
if [[ "$LIVE_TRADING_ENABLED" != "false" || \
      "$MAX_TRADE_SIZE_USD" != "0" || \
      "$MAX_DAILY_LOSS_USD" != "0" ]]; then
  fail "money_disabled_interlocks_not_zero"
fi

git -C /opt/bp fetch --no-tags origin \
  "refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
FETCHED_HEAD=$(git -C "$REPO" rev-parse "refs/remotes/origin/$BRANCH")
[[ "$FETCHED_HEAD" == "$HEAD" ]] || fail "candidate_head_changed"

git -C /opt/bp worktree add --detach "$WT" "$HEAD"
WORKTREE_HEAD=$(git -C "$WT" rev-parse HEAD)
[[ "$WORKTREE_HEAD" == "$HEAD" ]] || fail "worktree_head_mismatch"

REPORT=$(sudo -u bp env \
  PYTHONPATH="$WT/src" \
  "$PYTHON" -m bp_engine.prospective_evidence.cli report \
  --env-file "$ENV_FILE" \
  --project-state "$WT/PROJECT_STATE.json")

printf '%s\n' "$REPORT" | "$PYTHON" -c '
import json
import sys

report = json.load(sys.stdin)
if report.get("automatic_promotion") is not False:
    raise SystemExit("automatic_promotion must remain false")
master = report.get("master_live_gate")
if not isinstance(master, dict) or master.get("overall_live_gate") != "fail":
    raise SystemExit("Master live gate must remain fail for this Phase 14 report")
gates = report.get("evidence_gates")
if not isinstance(gates, dict):
    raise SystemExit("evidence_gates missing")
allowed = {"pass", "fail", "insufficient_evidence"}
for name, payload in gates.items():
    if not isinstance(payload, dict) or payload.get("status") not in allowed:
        raise SystemExit(f"invalid evidence gate status: {name}")
'

systemctl is-active --quiet "$PAPER_UNIT" || fail "paper_service_not_active_after"
DEPLOYED_HEAD_AFTER=$(git -C "$REPO" rev-parse HEAD)
[[ "$DEPLOYED_HEAD_AFTER" == "$DEPLOYED_HEAD_BEFORE" ]] || fail "deployed_checkout_changed"

printf '%s\n' "$REPORT"
echo "PROSPECTIVE_EVIDENCE_HEAD=$HEAD"
echo "DEPLOYED_HEAD_UNCHANGED=$DEPLOYED_HEAD_AFTER"
echo "PAPER_SERVICE=active"
echo "LIVE_TRADING_ENABLED=$LIVE_TRADING_ENABLED"
echo "MAX_TRADE_SIZE_USD=$MAX_TRADE_SIZE_USD"
echo "MAX_DAILY_LOSS_USD=$MAX_DAILY_LOSS_USD"
echo "PROSPECTIVE_EVIDENCE_HOST_REPORT=PASS"
REMOTE
)
REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 -w0)

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "PROSPECTIVE_EVIDENCE_HEAD=$EXPECTED_HEAD"
echo "Connecting for read-only prospective evidence reporting..."

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo env PROSPECTIVE_EVIDENCE_HEAD=$HEAD_Q PROSPECTIVE_EVIDENCE_BRANCH=$BRANCH_Q PROSPECTIVE_EVIDENCE_ENV_FILE=$ENV_FILE_Q bash"
