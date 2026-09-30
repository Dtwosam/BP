#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf 'PHASE15_FAST_LIVE_CLEANUP=FAIL:%s\n' "$1" >&2
  exit 1
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXPIRED_ACCEPT="${PHASE15_ACCEPT_FAST_LIVE_EXPIRED_CLEANUP:-}"
ABORT_ACCEPT="${PHASE15_ACCEPT_FAST_LIVE_ZERO_ACTIVITY_ABORT:-}"
RESTART_ACCEPT="${PHASE15_ACCEPT_FAST_LIVE_ZERO_ACTIVITY_RESTART:-}"
CLEANUP_MODE=""
if [[ "$EXPIRED_ACCEPT" == "I_ACCEPT_CLEAN_EXPIRED_CONTINUOUS_LIVE_SESSION" ]]; then
  CLEANUP_MODE="expired"
elif [[ "$ABORT_ACCEPT" == "I_ACCEPT_ABORT_ZERO_ACTIVITY_FAST_LIVE_SESSION_AFTER_VALIDATION_DEFECT" ]]; then
  CLEANUP_MODE="zero_activity_abort"
elif [[ "$RESTART_ACCEPT" == "I_ACCEPT_DEPLOY_FAST_LIVE_ZERO_FILL_FIX_AND_RESTART_SESSION" ]]; then
  CLEANUP_MODE="zero_activity_restart"
else
  fail "explicit_session_cleanup_acceptance_required"
fi

: "${BP_FAST_LIVE_GCP_PROJECT:?BP_FAST_LIVE_GCP_PROJECT is required}"
: "${BP_FAST_LIVE_RECORDER_VM:=bp-recorder}"
: "${BP_FAST_LIVE_RECORDER_ZONE:=us-east1-c}"
: "${BP_FAST_LIVE_EXEC_VM:=bp-v3-canary-exec}"
: "${BP_FAST_LIVE_EXEC_ZONE:=africa-south1-a}"

PROJECT="$BP_FAST_LIVE_GCP_PROJECT"
US_VM="$BP_FAST_LIVE_RECORDER_VM"
US_ZONE="$BP_FAST_LIVE_RECORDER_ZONE"
EXEC_VM="$BP_FAST_LIVE_EXEC_VM"
EXEC_ZONE="$BP_FAST_LIVE_EXEC_ZONE"

command -v git >/dev/null 2>&1 || fail "git_missing"
command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
command -v python3 >/dev/null 2>&1 || fail "python3_missing"
[[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=all)" ]] ||
  fail "working_tree_not_clean"

git -C "$ROOT" fetch origin main --quiet || fail "fetch_main_failed"
HELPER_HEAD="$(git -C "$ROOT" rev-parse HEAD)"
REMOTE_MAIN="$(git -C "$ROOT" rev-parse origin/main)"
[[ "$HELPER_HEAD" == "$REMOTE_MAIN" ]] || fail "checkout_is_not_current_main"

TMP_DIR="$(mktemp -d)"
cleanup_local() {
  rm -rf "$TMP_DIR"
}
trap cleanup_local EXIT
SESSION_AUTH="$TMP_DIR/session-authorization.json"
AUTH_SOURCE_HOST=""
if gcloud compute ssh "$US_VM" \
  --project="$PROJECT" --zone="$US_ZONE" --quiet \
  --command="sudo cat /etc/bp-fast-live/authorization.json" \
  >"$SESSION_AUTH" 2>/dev/null; then
  AUTH_SOURCE_HOST="recorder"
elif gcloud compute ssh "$EXEC_VM" \
  --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
  --command="sudo cat /etc/bp-fast-live/authorization.json" \
  >"$SESSION_AUTH" 2>/dev/null; then
  AUTH_SOURCE_HOST="executor"
else
  fail "expired_runtime_authorization_missing_on_both_hosts"
fi

read -r AUTH_ID RELEASE_MAIN RUNTIME_EXPIRES AUTH_SUFFIX AUTH_MODE RUNTIME_EXPIRED < <(
  python3 - "$SESSION_AUTH" <<'PY'
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("schema_version") != 1:
    raise SystemExit("schema_invalid")
if payload.get("purpose") != "phase15-v3-fast-live-v1":
    raise SystemExit("purpose_invalid")
if payload.get("authorized") is not True:
    raise SystemExit("not_authorized")
if payload.get("continuous_session") is not True:
    raise SystemExit("not_continuous")
if payload.get("requires_telegram_approval") is not True:
    raise SystemExit("telegram_not_required")
mode = str(payload.get("authorization_mode") or "")
if mode not in {
    "manual-telegram-continuous-v1",
    "auto-telegram-continuous-v1",
}:
    raise SystemExit("authorization_mode_invalid")
if payload.get("max_network_submission_attempts_per_intent") != 1:
    raise SystemExit("per_intent_attempt_limit_invalid")
auth_id = str(payload.get("authorization_id") or "")
release_main = str(payload.get("release_main") or "")
expires = datetime.fromisoformat(str(payload.get("expires_at") or "")).astimezone(UTC)
if not auth_id:
    raise SystemExit("authorization_id_missing")
if len(release_main) != 40 or any(ch not in "0123456789abcdef" for ch in release_main):
    raise SystemExit("release_main_invalid")
suffix = hashlib.sha256(auth_id.encode("utf-8")).hexdigest()[:12]
expired = datetime.now(UTC) >= expires
print(
    auth_id,
    release_main,
    expires.isoformat(),
    suffix,
    mode,
    "true" if expired else "false",
)
PY
) || fail "runtime_authorization_invalid"

if [[ "$CLEANUP_MODE" == "expired" ]]; then
  [[ "$RUNTIME_EXPIRED" == "true" ]] || fail "runtime_authorization_not_expired"
elif [[ "$CLEANUP_MODE" == "zero_activity_abort" ]]; then
  [[ "$RUNTIME_EXPIRED" == "false" ]] || fail "zero_activity_abort_runtime_already_expired"
  [[ "$AUTH_ID" == "phase15-v3-fast-live-auto-continuous-5d305254b06ef0cbce33065e" ]] ||
    fail "zero_activity_abort_authorization_id_mismatch"
  [[ "$RELEASE_MAIN" == "bbb20f8f5f3b533ecad3c0798c944c61f31bcdfe" ]] ||
    fail "zero_activity_abort_release_main_mismatch"
  [[ "$AUTH_MODE" == "auto-telegram-continuous-v1" ]] ||
    fail "zero_activity_abort_authorization_mode_mismatch"
elif [[ "$CLEANUP_MODE" == "zero_activity_restart" ]]; then
  [[ "$RUNTIME_EXPIRED" == "false" ]] || fail "zero_activity_restart_runtime_already_expired"
  [[ "$AUTH_ID" == "phase15-v3-fast-live-auto-continuous-12h-a6525318-20260930" ]] ||
    fail "zero_activity_restart_authorization_id_mismatch"
  [[ "$RELEASE_MAIN" == "afbf078a1be8bb29bb26ad7b99b2a35f10501473" ]] ||
    fail "zero_activity_restart_release_main_mismatch"
  [[ "$AUTH_MODE" == "auto-telegram-continuous-v1" ]] ||
    fail "zero_activity_restart_authorization_mode_mismatch"
  [[ "$RUNTIME_EXPIRES" == "2026-09-30T11:58:23.648915+00:00" ]] ||
    fail "zero_activity_restart_runtime_expiry_mismatch"
else
  fail "cleanup_mode_invalid"
fi

ORDER_TOPIC="bp-phase15-fast-live-orders-$AUTH_SUFFIX"
ORDER_SUB="bp-phase15-fast-live-orders-$AUTH_SUFFIX-jhb"
RESULT_TOPIC="bp-phase15-fast-live-results-$AUTH_SUFFIX"
RESULT_SUB="bp-phase15-fast-live-results-$AUTH_SUFFIX-us"

# Cleanup is allowed only after both session services have already stopped.
gcloud compute ssh "$US_VM" \
  --project="$PROJECT" --zone="$US_ZONE" --quiet \
  --command="sudo systemctl is-active --quiet bp-phase15-fast-live-source.service && exit 20 || true;
             sudo test ! -e /var/lib/bp/phase15-fast-live/telegram-prepare/current-run" ||
  fail "recorder_session_not_quiescent"

gcloud compute ssh "$US_VM" \
  --project="$PROJECT" --zone="$US_ZONE" --quiet \
  --command="sudo test ! -e /var/lib/bp/phase15-fast-live/RESULT_INTEGRITY_FAULT.json" ||
  fail "recorder_result_integrity_fault_latched"

gcloud compute ssh "$EXEC_VM" \
  --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
  --command="sudo systemctl is-active --quiet bp-phase15-fast-live-receiver.service && exit 21 || true;
             sudo test -f /var/lib/bp-canary/fast-live/KILL" ||
  fail "executor_session_not_quiescent"

# Source-side recovery must be fully durable before transport/session material is removed.
gcloud compute ssh "$US_VM" \
  --project="$PROJECT" --zone="$US_ZONE" --quiet \
  --command="sudo sh -c 'root=/var/lib/bp/phase15-fast-live/published;
    if [ -d \"\$root\" ]; then
      for receipt in \"\$root\"/*.json; do
        [ -e \"\$receipt\" ] || continue;
        base=\${receipt##*/};
        [ -f \"\$root/results/\$base\" ] || exit 24;
      done;
      if [ -d \"\$root/results\" ]; then
        for result in \"\$root/results\"/*.json; do
          [ -e \"\$result\" ] || continue;
          if grep -F -q \"\\\"settlement_reconciliation_required\\\":true\" \"\$result\"; then
            base=\${result##*/};
            [ -f \"\$root/settlements/\$base\" ] || exit 25;
          fi;
        done;
      fi;
    fi'" ||
  fail "recorder_recovery_not_complete"

# Executor-side cancellation/result publication recovery must also be complete.
gcloud compute ssh "$EXEC_VM" \
  --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
  --command="sudo sh -c '! grep -R -F -q cancellation_pending\":true /var/lib/bp-canary/fast-live/attempts 2>/dev/null &&
                          ! grep -R -F -q recovery_result_publish_pending\":true /var/lib/bp-canary/fast-live/attempts 2>/dev/null'" ||
  fail "executor_recovery_not_complete"

if [[ "$CLEANUP_MODE" == "zero_activity_abort" ]]; then
  read -r LIVE_PUBLICATIONS LIVE_RESULTS LIVE_SETTLEMENTS < <(
    gcloud compute ssh "$US_VM" \
      --project="$PROJECT" --zone="$US_ZONE" --quiet \
      --command="sudo sh -c 'root=/var/lib/bp/phase15-fast-live/published;
        publications=0; results=0; settlements=0;
        if [ -d \"\$root\" ]; then
          publications=\$(find \"\$root\" -maxdepth 1 -type f -name \"*.json\" | wc -l | tr -d \" \");
          results=\$(find \"\$root/results\" -maxdepth 1 -type f -name \"*.json\" 2>/dev/null | wc -l | tr -d \" \");
          settlements=\$(find \"\$root/settlements\" -maxdepth 1 -type f -name \"*.json\" 2>/dev/null | wc -l | tr -d \" \");
        fi;
        printf \"%s %s %s\\n\" \"\$publications\" \"\$results\" \"\$settlements\"'"
  ) || fail "zero_activity_abort_recorder_count_failed"

  [[ "$LIVE_PUBLICATIONS" == "0" && "$LIVE_RESULTS" == "0" && "$LIVE_SETTLEMENTS" == "0" ]] ||
    fail "zero_activity_abort_recorder_activity_present"

  read -r LIVE_ATTEMPTS EXEC_RESULTS APPROVAL_CLAIMS APPROVAL_RESULTS < <(
    gcloud compute ssh "$EXEC_VM" \
      --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
      --command="sudo sh -c 'root=/var/lib/bp-canary/fast-live;
        attempts=\$(find \"\$root/attempts\" -type f -name attempt.json 2>/dev/null | wc -l | tr -d \" \");
        results=\$(find \"\$root/attempts\" -type f -name result.json 2>/dev/null | wc -l | tr -d \" \");
        claims=\$(find \"\$root/approval-decisions\" -type f -name claim.json 2>/dev/null | wc -l | tr -d \" \");
        approvals=\$(find \"\$root/approval-decisions\" -type f -name result.json 2>/dev/null | wc -l | tr -d \" \");
        printf \"%s %s %s %s\\n\" \"\$attempts\" \"\$results\" \"\$claims\" \"\$approvals\"'"
  ) || fail "zero_activity_abort_executor_count_failed"

  [[ "$LIVE_ATTEMPTS" == "0" && "$EXEC_RESULTS" == "0" &&
     "$APPROVAL_CLAIMS" == "0" && "$APPROVAL_RESULTS" == "0" ]] ||
    fail "zero_activity_abort_executor_activity_present"
fi

# If both runtime copies still exist, they must identify the exact same session.
# A retry after partial host cleanup may legitimately have only one copy left.
US_RUNTIME_PRESENT="$(
  gcloud compute ssh "$US_VM" \
    --project="$PROJECT" --zone="$US_ZONE" --quiet \
    --command="if sudo test -f /etc/bp-fast-live/authorization.json &&
                  sudo test -f /etc/bp-fast-live/transport.key; then
                 printf yes;
               else
                 printf no;
               fi"
)" || fail "recorder_runtime_presence_check_failed"
EXEC_RUNTIME_PRESENT="$(
  gcloud compute ssh "$EXEC_VM" \
    --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
    --command="if sudo test -f /etc/bp-fast-live/authorization.json &&
                  sudo test -f /etc/bp-fast-live/transport.key; then
                 printf yes;
               else
                 printf no;
               fi"
)" || fail "executor_runtime_presence_check_failed"
[[ "$US_RUNTIME_PRESENT" == "yes" || "$EXEC_RUNTIME_PRESENT" == "yes" ]] ||
  fail "expired_runtime_material_missing_on_both_hosts"

if [[ "$US_RUNTIME_PRESENT" == "yes" && "$EXEC_RUNTIME_PRESENT" == "yes" ]]; then
  read -r US_AUTH_SHA US_KEY_SHA < <(
    gcloud compute ssh "$US_VM" \
      --project="$PROJECT" --zone="$US_ZONE" --quiet \
      --command="sudo sha256sum /etc/bp-fast-live/authorization.json /etc/bp-fast-live/transport.key | awk '{print \$1}' | xargs"
  ) || fail "recorder_session_hash_read_failed"
  read -r EXEC_AUTH_SHA EXEC_KEY_SHA < <(
    gcloud compute ssh "$EXEC_VM" \
      --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
      --command="sudo sha256sum /etc/bp-fast-live/authorization.json /etc/bp-fast-live/transport.key | awk '{print \$1}' | xargs"
  ) || fail "executor_session_hash_read_failed"
  [[ "$US_AUTH_SHA" == "$EXEC_AUTH_SHA" ]] ||
    fail "runtime_authorization_hash_mismatch"
  [[ "$US_KEY_SHA" == "$EXEC_KEY_SHA" ]] ||
    fail "transport_key_hash_mismatch"
fi

HEALTH="$TMP_DIR/health.json"
gcloud compute ssh "$EXEC_VM" \
  --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
  --command="printf '%s' '{\"action\":\"health\"}' | sudo /opt/bp-canary/executor.sh" \
  >"$HEALTH" || fail "executor_health_failed"
python3 - "$HEALTH" <<'PY' || fail "executor_account_not_clean"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("status") != "ok":
    raise SystemExit("health_not_ok")
account = payload.get("account") or {}
if int(account.get("open_order_count", -1)) != 0:
    raise SystemExit("open_orders_present")
if account.get("clean_for_canary") is not True:
    raise SystemExit("account_not_clean")
PY

delete_subscription_if_present() {
  local sub="$1"
  if gcloud pubsub subscriptions describe "$sub" --project="$PROJECT" >/dev/null 2>&1; then
    gcloud pubsub subscriptions delete "$sub" --project="$PROJECT" --quiet >/dev/null ||
      fail "subscription_delete_failed:$sub"
  fi
}

delete_topic_if_present() {
  local topic="$1"
  if gcloud pubsub topics describe "$topic" --project="$PROJECT" >/dev/null 2>&1; then
    gcloud pubsub topics delete "$topic" --project="$PROJECT" --quiet >/dev/null ||
      fail "topic_delete_failed:$topic"
  fi
}

delete_subscription_if_present "$ORDER_SUB"
delete_subscription_if_present "$RESULT_SUB"
delete_topic_if_present "$ORDER_TOPIC"
delete_topic_if_present "$RESULT_TOPIC"

# Remove session-only runtime material only after its transport is gone.
# Historical receipts, approvals, attempts, reconciliations, settlement
# markers, and logs are deliberately preserved.
gcloud compute ssh "$US_VM" \
  --project="$PROJECT" --zone="$US_ZONE" --quiet \
  --command="sudo rm -f /etc/bp-fast-live/authorization.json /etc/bp-fast-live/PROJECT_STATE.json /etc/bp-fast-live/transport.key /etc/bp/phase15-fast-live-source.env" ||
  fail "recorder_runtime_cleanup_failed"

gcloud compute ssh "$EXEC_VM" \
  --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
  --command="sudo rm -f /etc/bp-fast-live/authorization.json /etc/bp-fast-live/PROJECT_STATE.json /etc/bp-fast-live/transport.key /etc/bp-fast-live/receiver.env;
             sudo test -f /var/lib/bp-canary/fast-live/KILL" ||
  fail "executor_runtime_cleanup_failed"

printf 'PHASE15_FAST_LIVE_CLEANUP=PASS\n'
printf 'CLEANUP_MODE=%s\n' "$CLEANUP_MODE"
printf 'AUTHORIZATION_ID=%s\n' "$AUTH_ID"
printf 'AUTHORIZATION_MODE=%s\n' "$AUTH_MODE"
printf 'AUTHORIZATION_SOURCE_HOST=%s\n' "$AUTH_SOURCE_HOST"
printf 'SESSION_RELEASE_MAIN=%s\n' "$RELEASE_MAIN"
printf 'RUNTIME_EXPIRES_AT=%s\n' "$RUNTIME_EXPIRES"
if [[ "$CLEANUP_MODE" == "zero_activity_abort" || "$CLEANUP_MODE" == "zero_activity_restart" ]]; then
  printf 'ZERO_ACTIVITY_VERIFIED=true\n'
fi
printf 'KILL_SWITCH_ENGAGED=true\n'
printf 'SESSION_RUNTIME_FILES_PRESENT=false\n'
printf 'SESSION_PUBSUB_RESOURCES_PRESENT=false\n'
printf 'HISTORICAL_STATE_PRESERVED=true\n'
printf 'SERVICES_STARTED=false\n'
printf 'REAL_ORDER_SUBMITTED=false\n'
