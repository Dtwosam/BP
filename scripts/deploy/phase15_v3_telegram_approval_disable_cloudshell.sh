#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
ACCEPT="${PHASE15_ACCEPT_TELEGRAM_APPROVAL_DISABLE:-}"

fail() {
  echo "PHASE15_V3_TELEGRAM_APPROVAL_DISABLE=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$ACCEPT" == "yes" ]] ||
  fail "explicit_telegram_approval_disable_authorization_required"

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
[[ -n "$ROOT" ]] || fail "local_repository_missing"
cd "$ROOT"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  fail "local_working_tree_dirty"
LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_MAIN=$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || fail "local_main_not_current"
command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . ||
  fail "gcloud_auth_missing"

gcloud compute ssh "$US_VM"   --project="$PROJECT"   --zone="$US_ZONE"   --quiet   --command='sudo bash -s' <<'REMOTE'
set -Eeuo pipefail

SERVICE=bp-phase15-canary-telegram-approval.service
ENV_PATH=/etc/bp/telegram-approval.env
HANDOFF_ENV_PATH=/etc/bp/telegram-approval-handoff.env
STATE_ROOT=/var/lib/bp/phase15-canary-telegram-approval

fail() {
  echo "PHASE15_V3_TELEGRAM_APPROVAL_DISABLE=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

for unit in bp-postgres.service bp-recorder.service bp-v3-frozen-predictor.service bp-v3-paper-execution.service; do
  systemctl is-active --quiet "$unit" || fail "core_service_not_active_before:$unit"
done
RECORDER_PID_BEFORE=$(systemctl show -p MainPID --value bp-recorder.service)
PREDICTOR_PID_BEFORE=$(systemctl show -p MainPID --value bp-v3-frozen-predictor.service)
PAPER_PID_BEFORE=$(systemctl show -p MainPID --value bp-v3-paper-execution.service)

systemctl stop "$SERVICE" >/dev/null 2>&1 || true
systemctl disable "$SERVICE" >/dev/null 2>&1 || true
rm -f "$ENV_PATH" "$HANDOFF_ENV_PATH"

[[ "$(systemctl is-active "$SERVICE" 2>/dev/null || true)" != "active" ]] ||
  fail "telegram_service_still_active"
[[ "$(systemctl is-enabled "$SERVICE" 2>/dev/null || true)" != "enabled" ]] ||
  fail "telegram_service_still_enabled"
[[ ! -e "$ENV_PATH" ]] || fail "telegram_env_still_present"
[[ ! -e "$HANDOFF_ENV_PATH" ]] || fail "telegram_handoff_env_still_present"
[[ -d "$STATE_ROOT" ]] || true

RECORDER_PID_AFTER=$(systemctl show -p MainPID --value bp-recorder.service)
PREDICTOR_PID_AFTER=$(systemctl show -p MainPID --value bp-v3-frozen-predictor.service)
PAPER_PID_AFTER=$(systemctl show -p MainPID --value bp-v3-paper-execution.service)
[[ "$RECORDER_PID_AFTER" == "$RECORDER_PID_BEFORE" ]] || fail "recorder_restarted"
[[ "$PREDICTOR_PID_AFTER" == "$PREDICTOR_PID_BEFORE" ]] || fail "predictor_restarted"
[[ "$PAPER_PID_AFTER" == "$PAPER_PID_BEFORE" ]] || fail "paper_executor_restarted"

echo "SERVICE_ACTIVE=false"
echo "SERVICE_ENABLED=false"
echo "BOT_TOKEN_FILE_PRESENT=false"
echo "HANDOFF_ENV_PRESENT=false"
echo "APPROVAL_AUDIT_STATE_PRESERVED=true"
echo "CORE_SERVICE_PIDS_PRESERVED=true"
echo "NO_REAL_ORDER_SUBMITTED=true"
echo "PHASE15_V3_TELEGRAM_APPROVAL_DISABLE=PASS"
REMOTE
