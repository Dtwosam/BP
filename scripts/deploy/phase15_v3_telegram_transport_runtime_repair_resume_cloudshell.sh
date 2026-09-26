#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
EXEC_ZONE="${PHASE15_CANARY_EXEC_ZONE:-africa-south1-a}"
EXEC_VM="${PHASE15_CANARY_EXEC_VM:-bp-v3-canary-exec}"
ACCEPT="${PHASE15_ACCEPT_TELEGRAM_TRANSPORT_RUNTIME_REPAIR_RESUME:-}"

fail() {
  echo "PHASE15_V3_TELEGRAM_TRANSPORT_RUNTIME_REPAIR_RESUME=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$ACCEPT" == "yes" ]] ||
  fail "explicit_transport_runtime_repair_resume_authorization_required"

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
[[ -n "$ROOT" ]] || fail "local_repository_missing"
cd "$ROOT"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  fail "local_working_tree_dirty"
LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_MAIN=$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || fail "local_main_not_current"

command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
command -v python3 >/dev/null 2>&1 || fail "python3_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . ||
  fail "gcloud_auth_missing"

readarray -t AUTH < <(
  python3 - "$ROOT/PROJECT_STATE.json" <<'PY'
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gate = state["phase_15_v3_live_canary"]
repair = gate.get("telegram_transport_runtime_repair") or {}
second = gate.get("second_live_canary_authorization") or {}

assert state["live_trading_enabled"] is False
assert gate["live_trading_enabled"] is False
assert second.get("status") == "AUTHORIZED_NOT_SUBMITTED"
assert repair.get("status") == "AUTHORIZED_RESUME_PENDING"
assert repair.get("authorized") is True
assert repair.get("authorization_consumed") is False
assert repair.get("transport_reactivation_authorized") is True
assert repair.get("does_not_authorize_telegram_approve") is True
assert repair.get("does_not_authorize_executor_arm_or_invoke") is True
assert repair.get("does_not_authorize_order_submission") is True
assert repair.get("partial_repair", {}).get("pubsub_api_enabled") is True
assert repair.get("partial_repair", {}).get("corrected_stage_install_status") == "PASS"

print(repair["resume_helper_git_blob_sha"])
print(repair["resume_stage_id"])
print(repair["resume_release_head"])
PY
) || fail "source_truth_resume_authorization_invalid"

EXPECTED_HELPER_BLOB="${AUTH[0]:-}"
RESUME_STAGE_ID="${AUTH[1]:-}"
RESUME_RELEASE_HEAD="${AUTH[2]:-}"

[[ "$EXPECTED_HELPER_BLOB" =~ ^[0-9a-f]{40}$ ]] || fail "resume_helper_blob_invalid"
[[ "$RESUME_STAGE_ID" =~ ^phase15-telegram-stage-[0-9a-f]{24}$ ]] ||
  fail "resume_stage_id_invalid"
[[ "$RESUME_RELEASE_HEAD" =~ ^[0-9a-f]{40}$ ]] ||
  fail "resume_release_head_invalid"

ACTUAL_HELPER_BLOB=$(git hash-object "$ROOT/scripts/deploy/phase15_v3_telegram_transport_runtime_repair_resume_cloudshell.sh")
[[ "$ACTUAL_HELPER_BLOB" == "$EXPECTED_HELPER_BLOB" ]] ||
  fail "runtime_repair_resume_helper_binding_mismatch"

echo "=== PRE-RESUME EXECUTOR SAFETY ==="
HEALTH=$(gcloud compute ssh "$EXEC_VM"   --project="$PROJECT" --zone="$EXEC_ZONE" --quiet   --command="printf '%s' '{\"action\":\"health\"}' | sudo /opt/bp-canary/executor.sh") ||
  fail "executor_health_probe_failed"

python3 - "$HEALTH" <<'PY' || fail "executor_not_safe_idle"
import json
import sys
health = json.loads(sys.argv[1])
assert health["status"] == "ok"
assert health["kill_switch_engaged"] is True
assert health["activation_valid"] is False
assert health["submission_ready"] is False
assert health["live_order_submitted"] is False
assert int((health.get("account") or {}).get("open_order_count", -1)) == 0
PY

gcloud compute ssh "$EXEC_VM"   --project="$PROJECT" --zone="$EXEC_ZONE" --quiet   --command='sudo test ! -f /var/lib/bp-canary/telegram-live-handoff/second-canary.attempt.json' ||
  fail "second_canary_attempt_marker_present"

echo "=== VERIFY PUBSUB ENABLED ==="
enabled=$(gcloud services list --enabled   --project="$PROJECT"   --filter='config.name=pubsub.googleapis.com'   --format='value(config.name)') || fail "pubsub_enablement_read_failed"
[[ "$enabled" == "pubsub.googleapis.com" ]] || fail "pubsub_api_not_enabled"

TMP_DIR=$(mktemp -d /tmp/bp-phase15-telegram-repair-resume.XXXXXX)
trap 'rm -rf "$TMP_DIR"' EXIT

echo "=== VERIFY CORRECTED STAGE ==="
PHASE15_ACCEPT_TELEGRAM_TRANSPORT_STAGE_STATUS=yes   bash "$ROOT/scripts/deploy/phase15_v3_telegram_transport_stage_status_cloudshell.sh"   | tee "$TMP_DIR/stage-status.txt"
grep -q '^TELEGRAM_TRANSPORT_STAGE_READY=true$' "$TMP_DIR/stage-status.txt" ||
  fail "resume_stage_status_not_pass"
grep -Fq "\"stage_id\": \"$RESUME_STAGE_ID\"" "$TMP_DIR/stage-status.txt" ||
  fail "resume_stage_id_mismatch"
grep -Fq "\"release_head\": \"$RESUME_RELEASE_HEAD\"" "$TMP_DIR/stage-status.txt" ||
  fail "resume_release_head_mismatch"

echo "=== REVALIDATE READINESS ==="
PHASE15_TELEGRAM_TRANSPORT_RUNTIME_REPAIR_MODE=yes   bash "$ROOT/scripts/deploy/phase15_v3_telegram_pubsub_readiness_cloudshell.sh"   | tee "$TMP_DIR/readiness.txt"
grep -q '^TELEGRAM_PUBSUB_READY=true$' "$TMP_DIR/readiness.txt" ||
  fail "pubsub_readiness_not_pass"

echo "=== REACTIVATE CORRECTED TRANSPORT ==="
PHASE15_TELEGRAM_TRANSPORT_RUNTIME_REPAIR_MODE=yes PHASE15_ACCEPT_TELEGRAM_TRANSPORT_ACTIVATION=yes   bash "$ROOT/scripts/deploy/phase15_v3_telegram_transport_activate_cloudshell.sh"   | tee "$TMP_DIR/activation.txt"
grep -q '^PHASE15_V3_TELEGRAM_TRANSPORT_ACTIVATE=PASS$' "$TMP_DIR/activation.txt" ||
  fail "transport_reactivation_not_pass"

echo "=== POST-RESUME SAFETY ==="
POST_HEALTH=$(gcloud compute ssh "$EXEC_VM"   --project="$PROJECT" --zone="$EXEC_ZONE" --quiet   --command="printf '%s' '{\"action\":\"health\"}' | sudo /opt/bp-canary/executor.sh") ||
  fail "post_resume_executor_health_failed"

python3 - "$POST_HEALTH" <<'PY' || fail "post_resume_executor_not_safe"
import json
import sys
health = json.loads(sys.argv[1])
assert health["status"] == "ok"
assert health["kill_switch_engaged"] is True
assert health["activation_valid"] is False
assert health["submission_ready"] is False
assert health["live_order_submitted"] is False
assert int((health.get("account") or {}).get("open_order_count", -1)) == 0
PY

gcloud compute ssh "$EXEC_VM"   --project="$PROJECT" --zone="$EXEC_ZONE" --quiet   --command='sudo test ! -f /var/lib/bp-canary/telegram-live-handoff/second-canary.attempt.json' ||
  fail "second_canary_attempt_marker_created_by_resume"

echo "PUBSUB_API_ENABLED=true"
echo "RESUME_STAGE_ID=$RESUME_STAGE_ID"
echo "TRANSPORT_REACTIVATED=true"
echo "SECOND_CANARY_NETWORK_ATTEMPT_CONSUMED=false"
echo "TELEGRAM_APPROVAL_PERFORMED=false"
echo "EXECUTOR_ARMED=false"
echo "REAL_ORDER_SUBMITTED=false"
echo "PHASE15_V3_TELEGRAM_TRANSPORT_RUNTIME_REPAIR_RESUME=PASS"
