#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
EXEC_ZONE="${PHASE15_CANARY_EXEC_ZONE:-africa-south1-a}"
EXEC_VM="${PHASE15_CANARY_EXEC_VM:-bp-v3-canary-exec}"
TOPIC_ID="${BP_TELEGRAM_PUBSUB_TOPIC_ID:-bp-phase15-telegram-transport-v1}"
SUBSCRIPTION_ID="${BP_TELEGRAM_PUBSUB_SUBSCRIPTION_ID:-bp-phase15-telegram-exec-v1}"
ACCEPT="${PHASE15_ACCEPT_TELEGRAM_TRANSPORT_ACTIVATION:-}"

fail() {
  echo "PHASE15_V3_TELEGRAM_TRANSPORT_ACTIVATE=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$ACCEPT" == "yes" ]] || fail "explicit_transport_activation_authorization_required"

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

python3 - "$ROOT/PROJECT_STATE.json" <<'PY' ||
  fail "source_truth_not_authorized_for_second_telegram_canary"
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gate = state["phase_15_v3_live_canary"]
second = gate.get("second_live_canary_authorization") or {}
first = gate.get("first_live_canary") or {}

assert state["live_trading_enabled"] is False
assert gate["live_trading_enabled"] is False
assert gate["phase15_canary_authorized"] is True
assert gate["canary_order_submitted"] is True
assert first.get("official_reconciliation_complete") is True
assert gate.get("pending_unsubmitted_intent") is None
assert gate.get("v3_strategy_mutation_performed") is False
assert gate["second_order_authorized"] is True
assert gate["automated_real_money_submission"] is True
assert gate["manual_real_money_submission_required"] is False
assert gate["telegram_one_tap_submission_authorized"] is True
assert gate["telegram_persistent_execution_transport_authorized"] is True
assert gate["telegram_pubsub_transport_authorized"] is True
assert second.get("status") == "AUTHORIZED_NOT_SUBMITTED"
assert second.get("strategy_target_notional_usd") == 5
assert second.get("hard_max_trade_size_usd") == 10
assert second.get("max_network_submission_attempts") == 1
assert second.get("broad_autonomous_live_rollout_authorized") is False
assert second.get("requires_official_reconciliation_before_any_third_order") is True
PY

PHASE15_ACCEPT_TELEGRAM_TRANSPORT_STAGE_STATUS=yes   bash "$ROOT/scripts/deploy/phase15_v3_telegram_transport_stage_status_cloudshell.sh"   >/tmp/bp-phase15-telegram-stage-status.txt ||
  fail "transport_stage_status_not_pass"
grep -q '^TELEGRAM_TRANSPORT_STAGE_READY=true$'   /tmp/bp-phase15-telegram-stage-status.txt ||
  fail "transport_stage_not_ready"

EXPECTED_EXECUTOR_SHA256=$(python3 - "$ROOT/scripts/deploy/phase15_v3_canary_executor.py" <<'PY'
import hashlib
import sys
from pathlib import Path
print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
) || fail "expected_executor_sha256_failed"
[[ "$EXPECTED_EXECUTOR_SHA256" =~ ^[0-9a-f]{64}$ ]] ||
  fail "expected_executor_sha256_invalid"

INSTALLED_EXECUTOR_SHA256=$(gcloud compute ssh "$EXEC_VM"   --project="$PROJECT" --zone="$EXEC_ZONE" --quiet   --command="sudo sha256sum /opt/bp-canary/executor.py | awk '{print \\$1}'") ||
  fail "installed_executor_sha256_failed"
[[ "$INSTALLED_EXECUTOR_SHA256" == "$EXPECTED_EXECUTOR_SHA256" ]] ||
  fail "installed_executor_not_exact_current_main"

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)') ||
  fail "project_number_lookup_failed"
[[ "$PROJECT_NUMBER" =~ ^[0-9]+$ ]] || fail "project_number_invalid"
DEFAULT_COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

US_INSTANCE=$(gcloud compute instances describe "$US_VM"   --project="$PROJECT" --zone="$US_ZONE" --format=json) ||
  fail "recorder_instance_describe_failed"
EXEC_INSTANCE=$(gcloud compute instances describe "$EXEC_VM"   --project="$PROJECT" --zone="$EXEC_ZONE" --format=json) ||
  fail "executor_instance_describe_failed"
PROJECT_IAM=$(gcloud projects get-iam-policy "$PROJECT" --format=json) ||
  fail "project_iam_read_failed"

readarray -t IDENTITY < <(
  python3 - "$US_INSTANCE" "$EXEC_INSTANCE" "$PROJECT_IAM" "$DEFAULT_COMPUTE_SA" <<'PY'
import json
import sys

us = json.loads(sys.argv[1])
ex = json.loads(sys.argv[2])
project_iam = json.loads(sys.argv[3])
default_sa = sys.argv[4]
cloud_scope = "https://www.googleapis.com/auth/cloud-platform"
dangerous = {
    "roles/owner",
    "roles/editor",
    "roles/pubsub.admin",
    "roles/pubsub.editor",
    "roles/pubsub.publisher",
    "roles/pubsub.subscriber",
}

def identity(instance):
    values = instance.get("serviceAccounts") or []
    assert isinstance(values, list) and len(values) == 1
    item = values[0]
    assert isinstance(item, dict)
    email = str(item.get("email") or "")
    scopes = item.get("scopes") or []
    assert email and isinstance(scopes, list) and cloud_scope in scopes
    return email

def roles(member):
    found = []
    for binding in project_iam.get("bindings") or []:
        if not isinstance(binding, dict):
            continue
        if member in (binding.get("members") or []):
            found.append(str(binding.get("role") or ""))
    return set(found)

publisher = identity(us)
subscriber = identity(ex)
assert publisher != default_sa
assert subscriber != default_sa
assert publisher != subscriber
assert not (roles("serviceAccount:" + publisher) & dangerous)
assert not (roles("serviceAccount:" + subscriber) & dangerous)
print(publisher)
print(subscriber)
PY
) || fail "instance_service_account_policy_invalid"

PUBLISHER_SA="${IDENTITY[0]:-}"
SUBSCRIBER_SA="${IDENTITY[1]:-}"
[[ -n "$PUBLISHER_SA" && -n "$SUBSCRIBER_SA" ]] ||
  fail "service_account_resolution_failed"

TOPIC_NAME="projects/$PROJECT/topics/$TOPIC_ID"
SUBSCRIPTION_NAME="projects/$PROJECT/subscriptions/$SUBSCRIPTION_ID"

CREATED_TOPIC=false
CREATED_SUBSCRIPTION=false
if ! gcloud pubsub topics describe "$TOPIC_NAME" --project="$PROJECT" >/dev/null 2>&1; then
  gcloud pubsub topics create "$TOPIC_NAME" --project="$PROJECT" >/dev/null ||
    fail "pubsub_topic_create_failed"
  CREATED_TOPIC=true
fi
if ! gcloud pubsub subscriptions describe "$SUBSCRIPTION_NAME"   --project="$PROJECT" >/dev/null 2>&1; then
  gcloud pubsub subscriptions create "$SUBSCRIPTION_NAME"     --project="$PROJECT"     --topic="$TOPIC_NAME" >/dev/null ||
    fail "pubsub_subscription_create_failed"
  CREATED_SUBSCRIPTION=true
fi

gcloud pubsub topics add-iam-policy-binding "$TOPIC_NAME"   --project="$PROJECT"   --member="serviceAccount:$PUBLISHER_SA"   --role="roles/pubsub.publisher" >/dev/null ||
  fail "publisher_topic_iam_failed"
gcloud pubsub subscriptions add-iam-policy-binding "$SUBSCRIPTION_NAME"   --project="$PROJECT"   --member="serviceAccount:$SUBSCRIBER_SA"   --role="roles/pubsub.subscriber" >/dev/null ||
  fail "subscriber_subscription_iam_failed"

TMP_DIR=$(mktemp -d /tmp/bp-phase15-telegram-activate.XXXXXX)
chmod 0700 "$TMP_DIR"
TRANSPORT_KEY_ID="transport-$(python3 - <<'PY'
import secrets
print(secrets.token_hex(8))
PY
)"
ORIGIN_KEY_ID="origin-$(python3 - <<'PY'
import secrets
print(secrets.token_hex(8))
PY
)"
python3 - "$TMP_DIR/transport.key" "$TMP_DIR/origin.key" <<'PY'
import os
import secrets
import sys
from pathlib import Path

for raw in sys.argv[1:]:
    path = Path(raw)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(secrets.token_bytes(32))
        handle.flush()
        os.fsync(handle.fileno())
PY

cat >"$TMP_DIR/publisher.env" <<EOF
BP_TELEGRAM_PUBSUB_PUBLISH_WORKER_ENABLED=yes
BP_TELEGRAM_TRANSPORT_KEY_FILE=/etc/bp-telegram-transport/transport.key
BP_TELEGRAM_TRANSPORT_KEY_ID=$TRANSPORT_KEY_ID
BP_TELEGRAM_PUBSUB_PROJECT_ID=$PROJECT
BP_TELEGRAM_PUBSUB_TOPIC_ID=$TOPIC_ID
EOF

cat >"$TMP_DIR/handoff.env" <<EOF
BP_TELEGRAM_HANDOFF_ENABLED=yes
BP_TELEGRAM_HANDOFF_COMMAND=/opt/bp-telegram-transport/bin/approved-outbox-handoff
BP_TELEGRAM_APPROVED_OUTBOX_ENABLED=yes
BP_TELEGRAM_PROJECT_STATE_FILE=/opt/bp/PROJECT_STATE.json
BP_TELEGRAM_ORIGIN_KEY_FILE=/etc/bp-telegram-transport/origin.key
BP_TELEGRAM_ORIGIN_KEY_ID=$ORIGIN_KEY_ID
BP_TELEGRAM_TRANSPORT_KEY_FILE=/etc/bp-telegram-transport/transport.key
BP_TELEGRAM_TRANSPORT_KEY_ID=$TRANSPORT_KEY_ID
EOF

cat >"$TMP_DIR/receiver.env" <<EOF
BP_TELEGRAM_PUBSUB_STREAMING_RECEIVE_ENABLED=yes
BP_TELEGRAM_TRANSPORT_KEY_FILE=/etc/bp-telegram-transport/transport.key
BP_TELEGRAM_TRANSPORT_KEY_ID=$TRANSPORT_KEY_ID
BP_TELEGRAM_PUBSUB_PROJECT_ID=$PROJECT
BP_TELEGRAM_PUBSUB_SUBSCRIPTION_ID=$SUBSCRIPTION_ID
EOF

cat >"$TMP_DIR/claim.env" <<EOF
BP_TELEGRAM_TRANSPORT_CLAIM_WORKER_ENABLED=yes
BP_TELEGRAM_TRANSPORT_KEY_FILE=/etc/bp-telegram-transport/transport.key
BP_TELEGRAM_TRANSPORT_KEY_ID=$TRANSPORT_KEY_ID
EOF

cat >"$TMP_DIR/execution-auth.env" <<EOF
BP_TELEGRAM_EXECUTION_AUTH_WORKER_ENABLED=yes
BP_TELEGRAM_ORIGIN_KEY_FILE=/etc/bp-telegram-transport/origin.key
BP_TELEGRAM_ORIGIN_KEY_ID=$ORIGIN_KEY_ID
EOF

cat >"$TMP_DIR/privileged-handoff.env" <<EOF
BP_TELEGRAM_PRIVILEGED_HANDOFF_ENABLED=yes
EOF

for file in publisher.env handoff.env receiver.env claim.env execution-auth.env privileged-handoff.env; do
  chmod 0600 "$TMP_DIR/$file"
done

cleanup() {
  status=$?
  rm -rf "$TMP_DIR"
  rm -f /tmp/bp-phase15-telegram-stage-status.txt
  if [[ "$status" -ne 0 ]]; then
    gcloud compute ssh "$EXEC_VM" --project="$PROJECT" --zone="$EXEC_ZONE" --quiet       --command="sudo systemctl stop bp-phase15-telegram-privileged-handoff.service bp-phase15-telegram-execution-authorization-worker.service bp-phase15-telegram-transport-claim-worker.service bp-phase15-telegram-pubsub-streaming-receiver.service >/dev/null 2>&1 || true; sudo systemctl disable bp-phase15-telegram-privileged-handoff.service bp-phase15-telegram-execution-authorization-worker.service bp-phase15-telegram-transport-claim-worker.service bp-phase15-telegram-pubsub-streaming-receiver.service >/dev/null 2>&1 || true; sudo sh -c 'umask 077; printf %s\\n activation-failure-safe-stop > /etc/bp-canary/KILL'"       >/dev/null 2>&1 || true
    gcloud compute ssh "$US_VM" --project="$PROJECT" --zone="$US_ZONE" --quiet       --command="sudo systemctl stop bp-phase15-telegram-pubsub-publisher.service >/dev/null 2>&1 || true; sudo systemctl disable bp-phase15-telegram-pubsub-publisher.service >/dev/null 2>&1 || true"       >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

gcloud compute scp "$TMP_DIR/transport.key" "$US_VM:/tmp/bp-telegram-transport.key"   --project="$PROJECT" --zone="$US_ZONE" --quiet >/dev/null ||
  fail "publisher_transport_key_upload_failed"
gcloud compute scp "$TMP_DIR/origin.key" "$US_VM:/tmp/bp-telegram-origin.key"   --project="$PROJECT" --zone="$US_ZONE" --quiet >/dev/null ||
  fail "publisher_origin_key_upload_failed"
gcloud compute scp "$TMP_DIR/publisher.env" "$US_VM:/tmp/bp-telegram-publisher.env"   --project="$PROJECT" --zone="$US_ZONE" --quiet >/dev/null ||
  fail "publisher_env_upload_failed"
gcloud compute scp "$TMP_DIR/handoff.env" "$US_VM:/tmp/bp-telegram-handoff.env"   --project="$PROJECT" --zone="$US_ZONE" --quiet >/dev/null ||
  fail "handoff_env_upload_failed"

gcloud compute ssh "$US_VM" --project="$PROJECT" --zone="$US_ZONE" --quiet --command='
set -Eeuo pipefail
sudo install -d -o root -g bp -m 0750 /etc/bp-telegram-transport
sudo install -o root -g bp -m 0640 /tmp/bp-telegram-transport.key /etc/bp-telegram-transport/transport.key
sudo install -o root -g bp -m 0640 /tmp/bp-telegram-origin.key /etc/bp-telegram-transport/origin.key
sudo install -o root -g bp -m 0640 /tmp/bp-telegram-publisher.env /etc/bp/telegram-pubsub-publisher.env
sudo install -o root -g bp -m 0640 /tmp/bp-telegram-handoff.env /etc/bp/telegram-approval-handoff.env
rm -f /tmp/bp-telegram-transport.key /tmp/bp-telegram-origin.key /tmp/bp-telegram-publisher.env /tmp/bp-telegram-handoff.env
' || fail "publisher_configuration_install_failed"

for name in transport.key origin.key receiver.env claim.env execution-auth.env privileged-handoff.env; do
  gcloud compute scp "$TMP_DIR/$name" "$EXEC_VM:/tmp/bp-telegram-$name"     --project="$PROJECT" --zone="$EXEC_ZONE" --quiet >/dev/null ||
    fail "executor_configuration_upload_failed:$name"
done

gcloud compute ssh "$EXEC_VM" --project="$PROJECT" --zone="$EXEC_ZONE" --quiet --command='
set -Eeuo pipefail
sudo install -o root -g bp-transport -m 0640 /tmp/bp-telegram-transport.key /etc/bp-telegram-transport/transport.key
sudo install -o root -g root -m 0600 /tmp/bp-telegram-origin.key /etc/bp-telegram-transport/origin.key
sudo install -o root -g bp-transport -m 0640 /tmp/bp-telegram-receiver.env /etc/bp-telegram-transport/receiver.env
sudo install -o root -g bp-transport -m 0640 /tmp/bp-telegram-claim.env /etc/bp-telegram-transport/claim.env
sudo install -o root -g root -m 0600 /tmp/bp-telegram-execution-auth.env /etc/bp-telegram-transport/execution-auth.env
sudo install -o root -g root -m 0600 /tmp/bp-telegram-privileged-handoff.env /etc/bp-telegram-transport/privileged-handoff.env
rm -f /tmp/bp-telegram-transport.key /tmp/bp-telegram-origin.key /tmp/bp-telegram-receiver.env /tmp/bp-telegram-claim.env /tmp/bp-telegram-execution-auth.env /tmp/bp-telegram-privileged-handoff.env
' || fail "executor_configuration_install_failed"

HEALTH_BEFORE=$(gcloud compute ssh "$EXEC_VM" --project="$PROJECT" --zone="$EXEC_ZONE"   --quiet --command="printf '%s' '{"action":"health"}' | sudo /opt/bp-canary/executor.sh") ||
  fail "executor_safe_idle_probe_failed"
python3 - "$HEALTH_BEFORE" <<'PY' || fail "executor_not_safe_idle_before_activation"
import json
import sys
payload=json.loads(sys.argv[1])
assert payload["status"] == "ok"
assert payload["kill_switch_engaged"] is True
assert payload["activation_valid"] is False
assert payload["submission_ready"] is False
assert payload["live_order_submitted"] is False
geo=payload.get("geoblock") or {}
assert geo.get("blocked") is False
assert geo.get("country") == "ZA"
account=payload.get("account") or {}
assert account.get("clean_for_canary") is True
assert int(account.get("open_order_count", -1)) == 0
PY

BP_TELEGRAM_TRANSPORT_KEY_ID="$TRANSPORT_KEY_ID" BP_TELEGRAM_ORIGIN_KEY_ID="$ORIGIN_KEY_ID"   bash "$ROOT/scripts/deploy/phase15_v3_telegram_transport_activation_plan_cloudshell.sh"   >"$TMP_DIR/activation-plan.json.txt" ||
  fail "activation_plan_failed"
grep -q '"blockers": \[\]' "$TMP_DIR/activation-plan.json.txt" ||
  fail "activation_plan_still_blocked"

gcloud compute ssh "$EXEC_VM" --project="$PROJECT" --zone="$EXEC_ZONE" --quiet --command='
set -Eeuo pipefail
sudo systemctl enable --now bp-phase15-telegram-pubsub-streaming-receiver.service
sudo systemctl enable --now bp-phase15-telegram-transport-claim-worker.service
sudo systemctl enable --now bp-phase15-telegram-execution-authorization-worker.service
sudo systemctl enable --now bp-phase15-telegram-privileged-handoff.service
for unit in   bp-phase15-telegram-pubsub-streaming-receiver.service   bp-phase15-telegram-transport-claim-worker.service   bp-phase15-telegram-execution-authorization-worker.service   bp-phase15-telegram-privileged-handoff.service
do
  sudo systemctl is-active --quiet "$unit"
  sudo systemctl is-enabled --quiet "$unit"
done
' || fail "executor_transport_service_activation_failed"

gcloud compute ssh "$US_VM" --project="$PROJECT" --zone="$US_ZONE" --quiet --command='
set -Eeuo pipefail
sudo systemctl enable --now bp-phase15-telegram-pubsub-publisher.service
sudo systemctl is-active --quiet bp-phase15-telegram-pubsub-publisher.service
sudo systemctl is-enabled --quiet bp-phase15-telegram-pubsub-publisher.service
sudo systemctl restart bp-phase15-canary-telegram-approval.service
sudo systemctl is-active --quiet bp-phase15-canary-telegram-approval.service
' || fail "publisher_or_listener_activation_failed"

HEALTH_AFTER=$(gcloud compute ssh "$EXEC_VM" --project="$PROJECT" --zone="$EXEC_ZONE"   --quiet --command="printf '%s' '{"action":"health"}' | sudo /opt/bp-canary/executor.sh") ||
  fail "executor_post_activation_probe_failed"
python3 - "$HEALTH_AFTER" <<'PY' || fail "executor_not_safe_idle_after_activation"
import json
import sys
payload=json.loads(sys.argv[1])
assert payload["status"] == "ok"
assert payload["kill_switch_engaged"] is True
assert payload["activation_valid"] is False
assert payload["submission_ready"] is False
assert payload["live_order_submitted"] is False
geo=payload.get("geoblock") or {}
assert geo.get("blocked") is False
assert geo.get("country") == "ZA"
account=payload.get("account") or {}
assert account.get("clean_for_canary") is True
assert int(account.get("open_order_count", -1)) == 0
PY

trap - EXIT
rm -rf "$TMP_DIR"
rm -f /tmp/bp-phase15-telegram-stage-status.txt

echo "PHASE15_V3_TELEGRAM_TRANSPORT_ACTIVATE=PASS"
echo "RELEASE_HEAD=$LOCAL_HEAD"
echo "TOPIC_ID=$TOPIC_ID"
echo "SUBSCRIPTION_ID=$SUBSCRIPTION_ID"
echo "PUBLISHER_SERVICE_ACCOUNT=$PUBLISHER_SA"
echo "SUBSCRIBER_SERVICE_ACCOUNT=$SUBSCRIBER_SA"
echo "TOPIC_CREATED=$CREATED_TOPIC"
echo "SUBSCRIPTION_CREATED=$CREATED_SUBSCRIPTION"
echo "TRANSPORT_KEY_ID=$TRANSPORT_KEY_ID"
echo "ORIGIN_KEY_ID=$ORIGIN_KEY_ID"
echo "GLOBAL_LIVE_TRADING_ENABLED=false"
echo "SECOND_CANARY_AUTHORIZED=true"
echo "SERVICES_ACTIVE=true"
echo "EXECUTOR_SAFE_IDLE=true"
echo "REAL_ORDER_SUBMITTED=false"
echo "WAITING_FOR_FRESH_TELEGRAM_APPROVAL=true"
