#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
EXEC_ZONE="${PHASE15_CANARY_EXEC_ZONE:-africa-south1-a}"
EXEC_VM="${PHASE15_CANARY_EXEC_VM:-bp-v3-canary-exec}"
ACCEPT="${PHASE15_ACCEPT_TELEGRAM_TRANSPORT_RUNTIME_REPAIR:-}"

fail() {
  echo "PHASE15_V3_TELEGRAM_TRANSPORT_RUNTIME_REPAIR=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$ACCEPT" == "yes" ]] ||
  fail "explicit_transport_runtime_repair_authorization_required"

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
watch = gate.get("persistent_prepare_watch") or {}

assert state["live_trading_enabled"] is False
assert gate["live_trading_enabled"] is False
assert second.get("status") == "AUTHORIZED_NOT_SUBMITTED"
assert second.get("max_network_submission_attempts") == 1
assert repair.get("status") == "AUTHORIZED_NOT_RUN"
assert repair.get("authorized") is True
assert repair.get("authorization_consumed") is False
assert repair.get("enable_pubsub_api_authorized") is True
assert repair.get("transport_restage_authorized") is True
assert repair.get("transport_reactivation_authorized") is True
assert repair.get("does_not_authorize_telegram_approve") is True
assert repair.get("does_not_authorize_executor_arm_or_invoke") is True
assert repair.get("does_not_authorize_order_submission") is True
assert watch.get("current_run_second_canary_network_attempt_consumed") is False

print(repair["broken_stage_id"])
print(repair["helper_git_blob_sha"])
PY
) || fail "source_truth_repair_authorization_invalid"

BROKEN_STAGE_ID="${AUTH[0]:-}"
EXPECTED_HELPER_BLOB="${AUTH[1]:-}"
[[ "$BROKEN_STAGE_ID" =~ ^phase15-telegram-stage-[0-9a-f]{24}$ ]] ||
  fail "authorized_broken_stage_id_invalid"
[[ "$EXPECTED_HELPER_BLOB" =~ ^[0-9a-f]{40}$ ]] ||
  fail "authorized_helper_blob_invalid"

ACTUAL_HELPER_BLOB=$(git hash-object "$ROOT/scripts/deploy/phase15_v3_telegram_transport_runtime_repair_cloudshell.sh")
[[ "$ACTUAL_HELPER_BLOB" == "$EXPECTED_HELPER_BLOB" ]] ||
  fail "runtime_repair_helper_binding_mismatch"

echo "=== PRE-MUTATION EXECUTOR SAFETY ==="
EXEC_HEALTH=$(gcloud compute ssh "$EXEC_VM"   --project="$PROJECT" --zone="$EXEC_ZONE" --quiet   --command="printf '%s' '{\"action\":\"health\"}' | sudo /opt/bp-canary/executor.sh") ||
  fail "executor_health_probe_failed"

gcloud compute ssh "$EXEC_VM"   --project="$PROJECT" --zone="$EXEC_ZONE" --quiet   --command='sudo test ! -f /var/lib/bp-canary/telegram-live-handoff/second-canary.attempt.json' ||
  fail "second_canary_attempt_marker_present"

python3 - "$EXEC_HEALTH" <<'PY' || fail "executor_not_safe_idle"
import json
import sys

health = json.loads(sys.argv[1])
assert health["status"] == "ok"
assert health["kill_switch_engaged"] is True
assert health["activation_valid"] is False
assert health["submission_ready"] is False
assert health["live_order_submitted"] is False
assert (health.get("account") or {}).get("clean_for_canary") is True
assert int((health.get("account") or {}).get("open_order_count", -1)) == 0
assert (health.get("geoblock") or {}).get("blocked") is False
assert (health.get("geoblock") or {}).get("country") == "ZA"
PY

TMP_DIR=$(mktemp -d /tmp/bp-phase15-telegram-runtime-repair.XXXXXX)
chmod 0700 "$TMP_DIR"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "=== DEACTIVATE BROKEN TRANSPORT ==="
gcloud compute ssh "$EXEC_VM"   --project="$PROJECT" --zone="$EXEC_ZONE" --quiet   --command='sudo bash -s' <<'REMOTE'
set -Eeuo pipefail
SERVICES=(
  bp-phase15-telegram-privileged-handoff.service
  bp-phase15-telegram-execution-authorization-worker.service
  bp-phase15-telegram-transport-claim-worker.service
  bp-phase15-telegram-pubsub-streaming-receiver.service
)
systemctl stop "${SERVICES[@]}" >/dev/null 2>&1 || true
systemctl disable "${SERVICES[@]}" >/dev/null 2>&1 || true
rm -f   /etc/bp-telegram-transport/receiver.env   /etc/bp-telegram-transport/claim.env   /etc/bp-telegram-transport/execution-auth.env   /etc/bp-telegram-transport/privileged-handoff.env   /etc/bp-telegram-transport/transport.key   /etc/bp-telegram-transport/origin.key
install -d -o root -g root -m 0700 /etc/bp-canary
printf '%s\n' transport-runtime-repair-safe-stop > /etc/bp-canary/KILL
systemctl daemon-reload
REMOTE

gcloud compute ssh "$US_VM"   --project="$PROJECT" --zone="$US_ZONE" --quiet   --command='sudo bash -s' <<'REMOTE'
set -Eeuo pipefail
systemctl stop bp-phase15-telegram-pubsub-publisher.service >/dev/null 2>&1 || true
systemctl disable bp-phase15-telegram-pubsub-publisher.service >/dev/null 2>&1 || true
rm -f   /etc/bp/telegram-pubsub-publisher.env   /etc/bp/telegram-approval-handoff.env   /etc/bp-telegram-transport/transport.key   /etc/bp-telegram-transport/origin.key   /etc/bp-telegram-transport/project-state.json
rmdir /etc/bp-telegram-transport >/dev/null 2>&1 || true
systemctl restart bp-phase15-canary-telegram-approval.service
systemctl is-active --quiet bp-phase15-canary-telegram-approval.service
REMOTE

echo "=== ROLLBACK BROKEN STAGE ==="
PHASE15_TELEGRAM_TRANSPORT_STAGE_ID="$BROKEN_STAGE_ID" PHASE15_ACCEPT_TELEGRAM_TRANSPORT_STAGE_ROLLBACK=yes   bash "$ROOT/scripts/deploy/phase15_v3_telegram_transport_stage_rollback_cloudshell.sh"

echo "=== ENABLE PUBSUB API ==="
gcloud services enable pubsub.googleapis.com --project="$PROJECT" >/dev/null ||
  fail "pubsub_api_enable_failed"

for _ in $(seq 1 30); do
  enabled=$(gcloud services list --enabled     --project="$PROJECT"     --filter='config.name=pubsub.googleapis.com'     --format='value(config.name)') || fail "pubsub_enablement_recheck_failed"
  [[ "$enabled" == "pubsub.googleapis.com" ]] && break
  sleep 2
done
[[ "${enabled:-}" == "pubsub.googleapis.com" ]] || fail "pubsub_api_enablement_not_observed"

echo "=== BUILD CORRECTED TRANSPORT RELEASE ==="
RELEASE="$TMP_DIR/telegram-transport-$LOCAL_HEAD.tar.gz"
python3 "$ROOT/scripts/deploy/phase15_v3_telegram_transport_build_release.py"   --root "$ROOT" --output "$RELEASE" >"$TMP_DIR/build.json" ||
  fail "transport_release_build_failed"

echo "=== RESTAGE CORRECTED TRANSPORT ==="
PHASE15_TELEGRAM_TRANSPORT_RELEASE_ARCHIVE="$RELEASE" PHASE15_ACCEPT_TELEGRAM_TRANSPORT_STAGE_INSTALL=yes   bash "$ROOT/scripts/deploy/phase15_v3_telegram_transport_stage_install_cloudshell.sh"   | tee "$TMP_DIR/stage-install.txt"

echo "=== REVALIDATE READINESS ==="
bash "$ROOT/scripts/deploy/phase15_v3_telegram_pubsub_readiness_cloudshell.sh"   | tee "$TMP_DIR/readiness.txt"
grep -q '^TELEGRAM_PUBSUB_READY=true$' "$TMP_DIR/readiness.txt" ||
  fail "pubsub_readiness_not_pass"

echo "=== REACTIVATE CORRECTED TRANSPORT ==="
PHASE15_TELEGRAM_TRANSPORT_RUNTIME_REPAIR_MODE=yes PHASE15_ACCEPT_TELEGRAM_TRANSPORT_ACTIVATION=yes   bash "$ROOT/scripts/deploy/phase15_v3_telegram_transport_activate_cloudshell.sh"   | tee "$TMP_DIR/activation.txt"
grep -q '^PHASE15_V3_TELEGRAM_TRANSPORT_ACTIVATE=PASS$' "$TMP_DIR/activation.txt" ||
  fail "transport_reactivation_not_pass"

echo "=== POST-REPAIR SAFETY ==="
POST_HEALTH=$(gcloud compute ssh "$EXEC_VM"   --project="$PROJECT" --zone="$EXEC_ZONE" --quiet   --command="printf '%s' '{\"action\":\"health\"}' | sudo /opt/bp-canary/executor.sh") ||
  fail "post_repair_executor_health_failed"

python3 - "$POST_HEALTH" <<'PY' || fail "post_repair_executor_not_safe"
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
  fail "second_canary_attempt_marker_created_by_repair"

echo "PUBSUB_API_ENABLED=true"
echo "BROKEN_STAGE_ROLLED_BACK=true"
echo "CORRECTED_RELEASE_HEAD=$LOCAL_HEAD"
echo "TRANSPORT_REACTIVATED=true"
echo "SECOND_CANARY_NETWORK_ATTEMPT_CONSUMED=false"
echo "TELEGRAM_APPROVAL_PERFORMED=false"
echo "EXECUTOR_ARMED=false"
echo "REAL_ORDER_SUBMITTED=false"
echo "PHASE15_V3_TELEGRAM_TRANSPORT_RUNTIME_REPAIR=PASS"
