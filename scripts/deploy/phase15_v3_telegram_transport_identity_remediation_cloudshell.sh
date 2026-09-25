#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
EXEC_ZONE="${PHASE15_CANARY_EXEC_ZONE:-africa-south1-a}"
EXEC_VM="${PHASE15_CANARY_EXEC_VM:-bp-v3-canary-exec}"
PUBLISHER_SA_ID="${BP_TELEGRAM_PUBLISHER_SERVICE_ACCOUNT_ID:-bp-phase15-telegram-publisher}"
SUBSCRIBER_SA_ID="${BP_TELEGRAM_SUBSCRIBER_SERVICE_ACCOUNT_ID:-bp-phase15-telegram-subscriber}"
ACCEPT="${PHASE15_ACCEPT_TELEGRAM_TRANSPORT_ACTIVATION:-}"
CLOUD_SCOPE="https://www.googleapis.com/auth/cloud-platform"

fail() {
  echo "PHASE15_V3_TELEGRAM_TRANSPORT_IDENTITY_REMEDIATION=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$ACCEPT" == "yes" ]] ||
  fail "explicit_transport_activation_authorization_required"

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

if ! python3 - "$ROOT/PROJECT_STATE.json" <<'PY'
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gate = state["phase_15_v3_live_canary"]
stage = gate.get("telegram_transport_stage") or {}
activation = gate.get("telegram_transport_activation_authorization") or {}

assert state["live_trading_enabled"] is False
assert gate["live_trading_enabled"] is False
assert gate["second_order_authorized"] is True
assert gate["telegram_persistent_execution_transport_authorized"] is True
assert gate["telegram_pubsub_transport_authorized"] is True
assert stage.get("status") == "PRODUCTION_STAGED_INACTIVE"
assert stage.get("activation_authorized") is True
assert activation.get("status") == "AUTHORIZED_NOT_ACTIVATED"
assert activation.get("does_not_submit_real_order") is True
assert activation.get("fresh_private_telegram_approval_still_required_for_second_canary") is True
assert activation.get("broad_autonomous_live_rollout_authorized") is False
PY
then
  fail "source_truth_not_authorized_for_transport_activation"
fi

bash "$ROOT/scripts/deploy/phase15_v3_telegram_transport_stage_status_cloudshell.sh"   >"/tmp/bp-phase15-identity-stage-status.$$" || {
    rm -f "/tmp/bp-phase15-identity-stage-status.$$"
    fail "transport_stage_status_not_pass"
  }
grep -q '^TELEGRAM_TRANSPORT_STAGE_READY=true$'   "/tmp/bp-phase15-identity-stage-status.$$" || {
    rm -f "/tmp/bp-phase15-identity-stage-status.$$"
    fail "transport_stage_not_ready"
  }
rm -f "/tmp/bp-phase15-identity-stage-status.$$"

bash "$ROOT/scripts/deploy/phase15_v3_telegram_approval_status_cloudshell.sh"   >"/tmp/bp-phase15-identity-listener-status.$$" || {
    rm -f "/tmp/bp-phase15-identity-listener-status.$$"
    fail "telegram_listener_status_not_pass"
  }
grep -q '^LISTENER_BINDING_CURRENT=true$'   "/tmp/bp-phase15-identity-listener-status.$$" || {
    rm -f "/tmp/bp-phase15-identity-listener-status.$$"
    fail "telegram_listener_binding_not_current"
  }
grep -q '^HANDOFF_CONFIGURED=false$'   "/tmp/bp-phase15-identity-listener-status.$$" || {
    rm -f "/tmp/bp-phase15-identity-listener-status.$$"
    fail "telegram_listener_handoff_already_configured"
  }
rm -f "/tmp/bp-phase15-identity-listener-status.$$"

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)') ||
  fail "project_number_lookup_failed"
[[ "$PROJECT_NUMBER" =~ ^[0-9]+$ ]] || fail "project_number_invalid"
DEFAULT_COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
PUBLISHER_SA="${PUBLISHER_SA_ID}@${PROJECT}.iam.gserviceaccount.com"
SUBSCRIBER_SA="${SUBSCRIBER_SA_ID}@${PROJECT}.iam.gserviceaccount.com"

[[ "$PUBLISHER_SA" != "$SUBSCRIBER_SA" ]] || fail "service_accounts_not_distinct"
[[ "$PUBLISHER_SA" != "$DEFAULT_COMPUTE_SA" ]] ||
  fail "publisher_service_account_is_default"
[[ "$SUBSCRIBER_SA" != "$DEFAULT_COMPUTE_SA" ]] ||
  fail "subscriber_service_account_is_default"

ensure_service_account() {
  local id="$1"
  local email="$2"
  local display_name="$3"
  if ! gcloud iam service-accounts describe "$email"       --project="$PROJECT" --format=json >/dev/null 2>&1; then
    gcloud iam service-accounts create "$id"       --project="$PROJECT"       --display-name="$display_name" >/dev/null ||
      fail "service_account_create_failed:$id"
    if [[ "$id" == "$PUBLISHER_SA_ID" ]]; then
      PUBLISHER_SA_CREATED=true
    else
      SUBSCRIBER_SA_CREATED=true
    fi
  fi
  local disabled
  disabled=$(gcloud iam service-accounts describe "$email"     --project="$PROJECT" --format='value(disabled)') ||
    fail "service_account_describe_failed:$id"
  [[ "$disabled" != "True" && "$disabled" != "true" ]] ||
    fail "service_account_disabled:$id"
}

PUBLISHER_SA_CREATED=false
SUBSCRIBER_SA_CREATED=false
ensure_service_account "$PUBLISHER_SA_ID" "$PUBLISHER_SA"   "BP Phase 15 Telegram publisher"
ensure_service_account "$SUBSCRIBER_SA_ID" "$SUBSCRIBER_SA"   "BP Phase 15 Telegram subscriber"

PROJECT_IAM=$(gcloud projects get-iam-policy "$PROJECT" --format=json) ||
  fail "project_iam_read_failed"
if ! python3 - "$PROJECT_IAM" "$PUBLISHER_SA" "$SUBSCRIBER_SA" <<'PY'
import json
import sys

policy = json.loads(sys.argv[1])
emails = sys.argv[2:]
roles = {email: [] for email in emails}
for binding in policy.get("bindings") or []:
    if not isinstance(binding, dict):
        continue
    role = str(binding.get("role") or "")
    members = binding.get("members") or []
    for email in emails:
        if f"serviceAccount:{email}" in members and role:
            roles[email].append(role)
for email, assigned in roles.items():
    assert not assigned, f"{email} has project roles: {sorted(set(assigned))}"
PY
then
  fail "dedicated_service_account_has_project_level_role"
fi

US_BEFORE=$(gcloud compute instances describe "$US_VM"   --project="$PROJECT" --zone="$US_ZONE" --format=json) ||
  fail "recorder_instance_describe_failed"
EXEC_BEFORE=$(gcloud compute instances describe "$EXEC_VM"   --project="$PROJECT" --zone="$EXEC_ZONE" --format=json) ||
  fail "executor_instance_describe_failed"

readarray -t US_ORIGINAL < <(
  python3 - "$US_BEFORE" <<'PY'
import json
import sys
instance = json.loads(sys.argv[1])
assert instance.get("status") == "RUNNING"
accounts = instance.get("serviceAccounts") or []
assert isinstance(accounts, list)
if not accounts:
    print("__NONE__")
    print("")
else:
    assert len(accounts) == 1 and isinstance(accounts[0], dict)
    print(str(accounts[0].get("email") or "__NONE__"))
    scopes = accounts[0].get("scopes") or []
    assert isinstance(scopes, list)
    print(",".join(str(value) for value in scopes))
PY
) || fail "recorder_original_identity_invalid"

readarray -t EXEC_ORIGINAL < <(
  python3 - "$EXEC_BEFORE" <<'PY'
import json
import sys
instance = json.loads(sys.argv[1])
assert instance.get("status") == "RUNNING"
accounts = instance.get("serviceAccounts") or []
assert isinstance(accounts, list)
if not accounts:
    print("__NONE__")
    print("")
else:
    assert len(accounts) == 1 and isinstance(accounts[0], dict)
    print(str(accounts[0].get("email") or "__NONE__"))
    scopes = accounts[0].get("scopes") or []
    assert isinstance(scopes, list)
    print(",".join(str(value) for value in scopes))
PY
) || fail "executor_original_identity_invalid"

US_ORIGINAL_SA="${US_ORIGINAL[0]:-__NONE__}"
US_ORIGINAL_SCOPES="${US_ORIGINAL[1]:-}"
EXEC_ORIGINAL_SA="${EXEC_ORIGINAL[0]:-__NONE__}"
EXEC_ORIGINAL_SCOPES="${EXEC_ORIGINAL[1]:-}"

identity_is_desired() {
  local raw="$1"
  local expected="$2"
  python3 - "$raw" "$expected" "$CLOUD_SCOPE" <<'PY'
import json
import sys
instance = json.loads(sys.argv[1])
expected = sys.argv[2]
scope = sys.argv[3]
accounts = instance.get("serviceAccounts") or []
ok = (
    isinstance(accounts, list)
    and len(accounts) == 1
    and isinstance(accounts[0], dict)
    and accounts[0].get("email") == expected
    and isinstance(accounts[0].get("scopes"), list)
    and scope in accounts[0]["scopes"]
)
raise SystemExit(0 if ok else 1)
PY
}

US_CHANGED=false
EXEC_CHANGED=false
MUTATION_STARTED=false

wait_running() {
  local vm="$1"
  local zone="$2"
  for _ in $(seq 1 60); do
    local status
    status=$(gcloud compute instances describe "$vm"       --project="$PROJECT" --zone="$zone" --format='value(status)' 2>/dev/null || true)
    [[ "$status" == "RUNNING" ]] && return 0
    sleep 5
  done
  return 1
}

wait_ssh() {
  local vm="$1"
  local zone="$2"
  for _ in $(seq 1 60); do
    if gcloud compute ssh "$vm"         --project="$PROJECT" --zone="$zone" --quiet         --command='true' >/dev/null 2>&1; then
      return 0
    fi
    sleep 5
  done
  return 1
}

restore_identity() {
  local vm="$1"
  local zone="$2"
  local original_sa="$3"
  local original_scopes="$4"
  gcloud compute instances stop "$vm"     --project="$PROJECT" --zone="$zone" --quiet >/dev/null 2>&1 || return 1
  if [[ "$original_sa" == "__NONE__" ]]; then
    gcloud compute instances set-service-account "$vm"       --project="$PROJECT" --zone="$zone"       --no-service-account --no-scopes >/dev/null 2>&1 || return 1
  else
    local args=(
      gcloud compute instances set-service-account "$vm"
      "--project=$PROJECT"
      "--zone=$zone"
      "--service-account=$original_sa"
    )
    if [[ -n "$original_scopes" ]]; then
      args+=("--scopes=$original_scopes")
    else
      args+=("--no-scopes")
    fi
    "${args[@]}" >/dev/null 2>&1 || return 1
  fi
  gcloud compute instances start "$vm"     --project="$PROJECT" --zone="$zone" --quiet >/dev/null 2>&1 || return 1
  wait_running "$vm" "$zone" || return 1
  wait_ssh "$vm" "$zone" || return 1
}

cleanup() {
  local status=$?
  if [[ "$status" -ne 0 && "$MUTATION_STARTED" == "true" ]]; then
    if [[ "$EXEC_CHANGED" == "true" ]]; then
      restore_identity "$EXEC_VM" "$EXEC_ZONE"         "$EXEC_ORIGINAL_SA" "$EXEC_ORIGINAL_SCOPES" || true
    fi
    if [[ "$US_CHANGED" == "true" ]]; then
      restore_identity "$US_VM" "$US_ZONE"         "$US_ORIGINAL_SA" "$US_ORIGINAL_SCOPES" || true
    fi
  fi
}
trap cleanup EXIT

set_identity() {
  local vm="$1"
  local zone="$2"
  local email="$3"
  gcloud compute instances stop "$vm"     --project="$PROJECT" --zone="$zone" --quiet >/dev/null ||
    fail "instance_stop_failed:$vm"
  gcloud compute instances set-service-account "$vm"     --project="$PROJECT" --zone="$zone"     --service-account="$email"     --scopes="$CLOUD_SCOPE" >/dev/null ||
    fail "instance_set_service_account_failed:$vm"
  gcloud compute instances start "$vm"     --project="$PROJECT" --zone="$zone" --quiet >/dev/null ||
    fail "instance_start_failed:$vm"
  wait_running "$vm" "$zone" || fail "instance_not_running_after_restart:$vm"
  wait_ssh "$vm" "$zone" || fail "instance_ssh_not_ready_after_restart:$vm"
}

if ! identity_is_desired "$US_BEFORE" "$PUBLISHER_SA"; then
  MUTATION_STARTED=true
  US_CHANGED=true
  set_identity "$US_VM" "$US_ZONE" "$PUBLISHER_SA"
fi

gcloud compute ssh "$US_VM"   --project="$PROJECT" --zone="$US_ZONE" --quiet --command='
set -Eeuo pipefail
for unit in   bp-postgres.service   bp-recorder.service   bp-v3-frozen-predictor.service   bp-v3-paper-execution.service   bp-phase15-canary-telegram-approval.service
do
  sudo systemctl is-active --quiet "$unit"
done
! sudo systemctl is-active --quiet bp-phase15-telegram-pubsub-publisher.service
! sudo systemctl is-enabled --quiet bp-phase15-telegram-pubsub-publisher.service
' || fail "recorder_health_after_identity_change_failed"

if ! identity_is_desired "$EXEC_BEFORE" "$SUBSCRIBER_SA"; then
  MUTATION_STARTED=true
  EXEC_CHANGED=true
  set_identity "$EXEC_VM" "$EXEC_ZONE" "$SUBSCRIBER_SA"
fi

gcloud compute ssh "$EXEC_VM"   --project="$PROJECT" --zone="$EXEC_ZONE" --quiet --command='
set -Eeuo pipefail
for unit in   bp-phase15-telegram-pubsub-streaming-receiver.service   bp-phase15-telegram-transport-claim-worker.service   bp-phase15-telegram-execution-authorization-worker.service   bp-phase15-telegram-privileged-handoff.service
do
  ! sudo systemctl is-active --quiet "$unit"
  ! sudo systemctl is-enabled --quiet "$unit"
done
' || fail "executor_transport_not_inactive_after_identity_change"

HEALTH=$(gcloud compute ssh "$EXEC_VM"   --project="$PROJECT" --zone="$EXEC_ZONE" --quiet   --command="printf '%s' '{\"action\":\"health\"}' | sudo /opt/bp-canary/executor.sh") ||
  fail "executor_health_probe_failed_after_identity_change"
if ! python3 - "$HEALTH" <<'PY'
import json
import sys
payload = json.loads(sys.argv[1])
assert payload["status"] == "ok"
assert payload["kill_switch_engaged"] is True
assert payload["activation_valid"] is False
assert payload["submission_ready"] is False
assert payload["live_order_submitted"] is False
geoblock = payload.get("geoblock") or {}
assert geoblock.get("blocked") is False
assert geoblock.get("country") == "ZA"
account = payload.get("account") or {}
assert account.get("clean_for_canary") is True
assert int(account.get("open_order_count", -1)) == 0
PY
then
  fail "executor_not_safe_idle_after_identity_change"
fi

US_AFTER=$(gcloud compute instances describe "$US_VM"   --project="$PROJECT" --zone="$US_ZONE" --format=json) ||
  fail "recorder_post_identity_describe_failed"
EXEC_AFTER=$(gcloud compute instances describe "$EXEC_VM"   --project="$PROJECT" --zone="$EXEC_ZONE" --format=json) ||
  fail "executor_post_identity_describe_failed"

identity_is_desired "$US_AFTER" "$PUBLISHER_SA" ||
  fail "publisher_identity_not_exact"
identity_is_desired "$EXEC_AFTER" "$SUBSCRIBER_SA" ||
  fail "subscriber_identity_not_exact"

bash "$ROOT/scripts/deploy/phase15_v3_telegram_transport_stage_status_cloudshell.sh"   >"/tmp/bp-phase15-identity-stage-status-after.$$" || {
    rm -f "/tmp/bp-phase15-identity-stage-status-after.$$"
    fail "transport_stage_status_not_pass_after_identity_change"
  }
grep -q '^TELEGRAM_TRANSPORT_STAGE_READY=true$'   "/tmp/bp-phase15-identity-stage-status-after.$$" || {
    rm -f "/tmp/bp-phase15-identity-stage-status-after.$$"
    fail "transport_stage_not_ready_after_identity_change"
  }
rm -f "/tmp/bp-phase15-identity-stage-status-after.$$"

trap - EXIT

echo "PUBLISHER_SERVICE_ACCOUNT=$PUBLISHER_SA"
echo "SUBSCRIBER_SERVICE_ACCOUNT=$SUBSCRIBER_SA"
echo "PUBLISHER_SERVICE_ACCOUNT_CREATED=$PUBLISHER_SA_CREATED"
echo "SUBSCRIBER_SERVICE_ACCOUNT_CREATED=$SUBSCRIBER_SA_CREATED"
echo "RECORDER_IDENTITY_CHANGED=$US_CHANGED"
echo "EXECUTOR_IDENTITY_CHANGED=$EXEC_CHANGED"
echo "CLOUD_PLATFORM_SCOPE=true"
echo "PROJECT_LEVEL_WORKLOAD_ROLES_GRANTED=false"
echo "TRANSPORT_SERVICES_STARTED=false"
echo "TRANSPORT_SERVICES_ENABLED=false"
echo "GLOBAL_LIVE_TRADING_ENABLED=false"
echo "EXECUTOR_SAFE_IDLE=true"
echo "REAL_ORDER_SUBMITTED=false"
echo "PHASE15_V3_TELEGRAM_TRANSPORT_IDENTITY_REMEDIATION=PASS"
