#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
EXEC_ZONE="${PHASE15_CANARY_EXEC_ZONE:-africa-south1-a}"
EXEC_VM="${PHASE15_CANARY_EXEC_VM:-bp-v3-canary-exec}"
TOPIC_ID="${BP_TELEGRAM_PUBSUB_TOPIC_ID:-bp-phase15-telegram-transport-v1}"
SUBSCRIPTION_ID="${BP_TELEGRAM_PUBSUB_SUBSCRIPTION_ID:-bp-phase15-telegram-exec-v1}"

fail() {
  echo "PHASE15_V3_TELEGRAM_PUBSUB_READINESS=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

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

SOURCE=$(
python3 - "$ROOT/PROJECT_STATE.json" <<'PY'
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gate = state["phase_15_v3_live_canary"]
print(json.dumps(
    {
        "live_trading_enabled": state["live_trading_enabled"],
        "second_order_authorized": gate["second_order_authorized"],
        "automated_real_money_submission": gate["automated_real_money_submission"],
        "telegram_one_tap_submission_authorized": gate.get(
            "telegram_one_tap_submission_authorized", False
        ),
        "telegram_persistent_execution_transport_authorized": gate.get(
            "telegram_persistent_execution_transport_authorized", False
        ),
        "telegram_pubsub_transport_authorized": gate.get(
            "telegram_pubsub_transport_authorized", False
        ),
    },
    separators=(",", ":"),
    sort_keys=True,
))
PY
) || fail "source_truth_read_failed"

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)') ||
  fail "project_number_lookup_failed"
[[ "$PROJECT_NUMBER" =~ ^[0-9]+$ ]] || fail "project_number_invalid"
DEFAULT_COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

US_INSTANCE=$(gcloud compute instances describe "$US_VM"   --project="$PROJECT"   --zone="$US_ZONE"   --format=json) || fail "recorder_instance_describe_failed"
EXEC_INSTANCE=$(gcloud compute instances describe "$EXEC_VM"   --project="$PROJECT"   --zone="$EXEC_ZONE"   --format=json) || fail "executor_instance_describe_failed"

TOPIC_NAME="projects/$PROJECT/topics/$TOPIC_ID"
SUBSCRIPTION_NAME="projects/$PROJECT/subscriptions/$SUBSCRIPTION_ID"

TOPIC='null'
if gcloud pubsub topics describe "$TOPIC_NAME" --project="$PROJECT" --format=json   >/tmp/bp-telegram-topic.json 2>/dev/null; then
  TOPIC=$(cat /tmp/bp-telegram-topic.json)
fi
rm -f /tmp/bp-telegram-topic.json

SUBSCRIPTION='null'
if gcloud pubsub subscriptions describe "$SUBSCRIPTION_NAME"   --project="$PROJECT" --format=json   >/tmp/bp-telegram-subscription.json 2>/dev/null; then
  SUBSCRIPTION=$(cat /tmp/bp-telegram-subscription.json)
fi
rm -f /tmp/bp-telegram-subscription.json

TOPIC_IAM='null'
if [[ "$TOPIC" != "null" ]]; then
  TOPIC_IAM=$(gcloud pubsub topics get-iam-policy "$TOPIC_NAME"     --project="$PROJECT" --format=json) || fail "topic_iam_read_failed"
fi

SUBSCRIPTION_IAM='null'
if [[ "$SUBSCRIPTION" != "null" ]]; then
  SUBSCRIPTION_IAM=$(gcloud pubsub subscriptions get-iam-policy "$SUBSCRIPTION_NAME"     --project="$PROJECT" --format=json) || fail "subscription_iam_read_failed"
fi

PROJECT_IAM=$(gcloud projects get-iam-policy "$PROJECT" --format=json) ||
  fail "project_iam_read_failed"

python3 -   "$SOURCE"   "$US_INSTANCE"   "$EXEC_INSTANCE"   "$TOPIC"   "$SUBSCRIPTION"   "$TOPIC_IAM"   "$SUBSCRIPTION_IAM"   "$PROJECT_IAM"   "$TOPIC_NAME"   "$SUBSCRIPTION_NAME"   "$DEFAULT_COMPUTE_SA" <<'PY'
import json
import sys

(
    source_raw,
    us_raw,
    exec_raw,
    topic_raw,
    subscription_raw,
    topic_iam_raw,
    subscription_iam_raw,
    project_iam_raw,
    topic_name,
    subscription_name,
    default_compute_sa,
) = sys.argv[1:]

source = json.loads(source_raw)
us = json.loads(us_raw)
executor = json.loads(exec_raw)
topic = json.loads(topic_raw)
subscription = json.loads(subscription_raw)
topic_iam = json.loads(topic_iam_raw)
subscription_iam = json.loads(subscription_iam_raw)
project_iam = json.loads(project_iam_raw)

cloud_platform = "https://www.googleapis.com/auth/cloud-platform"


def service_account(instance: dict[str, object], label: str) -> tuple[str | None, list[str]]:
    accounts = instance.get("serviceAccounts") or []
    if not isinstance(accounts, list) or len(accounts) != 1:
        return None, [f"{label}_service_account_count_not_one"]
    entry = accounts[0]
    if not isinstance(entry, dict):
        return None, [f"{label}_service_account_invalid"]
    email = str(entry.get("email") or "")
    scopes = entry.get("scopes") or []
    blockers: list[str] = []
    if not email:
        blockers.append(f"{label}_service_account_missing")
    if not isinstance(scopes, list) or cloud_platform not in scopes:
        blockers.append(f"{label}_cloud_platform_scope_missing")
    return email or None, blockers


def has_binding(policy: dict[str, object], role: str, member: str) -> bool:
    bindings = policy.get("bindings") or []
    if not isinstance(bindings, list):
        return False
    for binding in bindings:
        if not isinstance(binding, dict) or binding.get("role") != role:
            continue
        members = binding.get("members") or []
        if isinstance(members, list) and member in members:
            return True
    return False


def project_roles(policy: dict[str, object], member: str) -> list[str]:
    roles: list[str] = []
    bindings = policy.get("bindings") or []
    if not isinstance(bindings, list):
        return roles
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        members = binding.get("members") or []
        if isinstance(members, list) and member in members:
            roles.append(str(binding.get("role") or ""))
    return sorted(set(role for role in roles if role))


blockers: list[str] = []
publisher_sa, publisher_blockers = service_account(us, "publisher_vm")
subscriber_sa, subscriber_blockers = service_account(executor, "subscriber_vm")
blockers.extend(publisher_blockers)
blockers.extend(subscriber_blockers)

if publisher_sa == default_compute_sa:
    blockers.append("publisher_uses_default_compute_service_account")
if subscriber_sa == default_compute_sa:
    blockers.append("subscriber_uses_default_compute_service_account")
if publisher_sa and subscriber_sa and publisher_sa == subscriber_sa:
    blockers.append("publisher_and_subscriber_service_accounts_not_separate")

if topic is None:
    blockers.append("pubsub_topic_missing")
elif str(topic.get("name") or "") != topic_name:
    blockers.append("pubsub_topic_name_mismatch")

if subscription is None:
    blockers.append("pubsub_subscription_missing")
else:
    if str(subscription.get("name") or "") != subscription_name:
        blockers.append("pubsub_subscription_name_mismatch")
    if str(subscription.get("topic") or "") != topic_name:
        blockers.append("pubsub_subscription_topic_mismatch")

if publisher_sa:
    publisher_member = f"serviceAccount:{publisher_sa}"
    if not has_binding(topic_iam or {}, "roles/pubsub.publisher", publisher_member):
        blockers.append("publisher_topic_role_missing")
    publisher_project_roles = project_roles(project_iam, publisher_member)
else:
    publisher_project_roles = []

if subscriber_sa:
    subscriber_member = f"serviceAccount:{subscriber_sa}"
    if not has_binding(
        subscription_iam or {},
        "roles/pubsub.subscriber",
        subscriber_member,
    ):
        blockers.append("subscriber_subscription_role_missing")
    subscriber_project_roles = project_roles(project_iam, subscriber_member)
else:
    subscriber_project_roles = []

dangerous_roles = {
    "roles/owner",
    "roles/editor",
    "roles/pubsub.admin",
    "roles/pubsub.editor",
    "roles/pubsub.publisher",
    "roles/pubsub.subscriber",
}
if any(role in dangerous_roles for role in publisher_project_roles):
    blockers.append("publisher_has_broad_project_level_role")
if any(role in dangerous_roles for role in subscriber_project_roles):
    blockers.append("subscriber_has_broad_project_level_role")

if source["live_trading_enabled"] is not False:
    blockers.append("global_live_trading_not_disabled")
if source["second_order_authorized"] is not True:
    blockers.append("second_order_not_authorized")
if source["automated_real_money_submission"] is not True:
    blockers.append("automated_submission_not_authorized")
if source["telegram_one_tap_submission_authorized"] is not True:
    blockers.append("telegram_one_tap_not_authorized")
if source["telegram_persistent_execution_transport_authorized"] is not True:
    blockers.append("persistent_execution_transport_not_authorized")
if source["telegram_pubsub_transport_authorized"] is not True:
    blockers.append("telegram_pubsub_transport_not_authorized")

report = {
    "ready": not blockers,
    "blockers": blockers,
    "source_truth": source,
    "topic_name": topic_name,
    "subscription_name": subscription_name,
    "publisher_service_account": publisher_sa,
    "subscriber_service_account": subscriber_sa,
    "publisher_project_roles": publisher_project_roles,
    "subscriber_project_roles": subscriber_project_roles,
    "mutation_performed": False,
    "real_order_submitted": False,
}
print(json.dumps(report, indent=2, sort_keys=True))
if blockers:
    print("TELEGRAM_PUBSUB_READY=false")
    print("NO_MUTATION_PERFORMED=true")
    print("PHASE15_V3_TELEGRAM_PUBSUB_READINESS=BLOCKED")
else:
    print("TELEGRAM_PUBSUB_READY=true")
    print("NO_MUTATION_PERFORMED=true")
    print("PHASE15_V3_TELEGRAM_PUBSUB_READINESS=PASS")
PY
