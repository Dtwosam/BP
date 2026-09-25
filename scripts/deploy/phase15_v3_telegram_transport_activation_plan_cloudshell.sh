#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
EXEC_ZONE="${PHASE15_CANARY_EXEC_ZONE:-africa-south1-a}"
EXEC_VM="${PHASE15_CANARY_EXEC_VM:-bp-v3-canary-exec}"
TOPIC_ID="${BP_TELEGRAM_PUBSUB_TOPIC_ID:-bp-phase15-telegram-transport-v1}"
SUBSCRIPTION_ID="${BP_TELEGRAM_PUBSUB_SUBSCRIPTION_ID:-bp-phase15-telegram-exec-v1}"
TRANSPORT_KEY_ID="${BP_TELEGRAM_TRANSPORT_KEY_ID:-}"
ORIGIN_KEY_ID="${BP_TELEGRAM_ORIGIN_KEY_ID:-}"

fail() {
  echo "PHASE15_V3_TELEGRAM_TRANSPORT_ACTIVATION_PLAN=FAIL" >&2
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

SOURCE_JSON=$(
  python3 - "$ROOT/PROJECT_STATE.json" <<'PY'
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gate = state["phase_15_v3_live_canary"]
first = gate.get("first_live_canary") or {}
print(json.dumps(
    {
        "live_trading_enabled": state["live_trading_enabled"],
        "phase15_live_trading_enabled": gate["live_trading_enabled"],
        "phase15_canary_authorized": gate["phase15_canary_authorized"],
        "canary_order_submitted": gate["canary_order_submitted"],
        "first_canary_reconciliation_complete": first.get(
            "official_reconciliation_complete", False
        ),
        "pending_unsubmitted_intent": gate.get("pending_unsubmitted_intent"),
        "v3_strategy_mutation_performed": gate.get(
            "v3_strategy_mutation_performed", False
        ),
        "second_order_authorized": gate["second_order_authorized"],
        "automated_real_money_submission": gate["automated_real_money_submission"],
        "manual_real_money_submission_required": gate[
            "manual_real_money_submission_required"
        ],
        "wallet_material_allowed_on_us_host": gate[
            "wallet_material_allowed_on_us_host"
        ],
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
if VALUE=$(gcloud pubsub topics describe "$TOPIC_NAME"   --project="$PROJECT" --format=json 2>/dev/null); then
  TOPIC="$VALUE"
fi

SUBSCRIPTION='null'
if VALUE=$(gcloud pubsub subscriptions describe "$SUBSCRIPTION_NAME"   --project="$PROJECT" --format=json 2>/dev/null); then
  SUBSCRIPTION="$VALUE"
fi

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

python3 -   "$SOURCE_JSON"   "$US_INSTANCE"   "$EXEC_INSTANCE"   "$TOPIC"   "$SUBSCRIPTION"   "$TOPIC_IAM"   "$SUBSCRIPTION_IAM"   "$PROJECT_IAM"   "$PROJECT"   "$TOPIC_ID"   "$SUBSCRIPTION_ID"   "$TOPIC_NAME"   "$SUBSCRIPTION_NAME"   "$TRANSPORT_KEY_ID"   "$ORIGIN_KEY_ID"   "$DEFAULT_COMPUTE_SA"   "$LOCAL_HEAD" <<'PY'
import json
import re
import sys

(
    source_raw,
    us_raw,
    executor_raw,
    topic_raw,
    subscription_raw,
    topic_iam_raw,
    subscription_iam_raw,
    project_iam_raw,
    project_id,
    topic_id,
    subscription_id,
    topic_name,
    subscription_name,
    transport_key_id,
    origin_key_id,
    default_compute_sa,
    release_head,
) = sys.argv[1:]

source = json.loads(source_raw)
us = json.loads(us_raw)
executor = json.loads(executor_raw)
topic = json.loads(topic_raw)
subscription = json.loads(subscription_raw)
topic_iam = json.loads(topic_iam_raw)
subscription_iam = json.loads(subscription_iam_raw)
project_iam = json.loads(project_iam_raw)

cloud_platform = "https://www.googleapis.com/auth/cloud-platform"
identifier = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def service_account(instance: dict[str, object]) -> tuple[str | None, bool]:
    accounts = instance.get("serviceAccounts") or []
    if not isinstance(accounts, list) or len(accounts) != 1:
        return None, False
    entry = accounts[0]
    if not isinstance(entry, dict):
        return None, False
    email = str(entry.get("email") or "")
    scopes = entry.get("scopes") or []
    return (
        email or None,
        isinstance(scopes, list) and cloud_platform in scopes,
    )


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
            role = str(binding.get("role") or "")
            if role:
                roles.append(role)
    return sorted(set(roles))


publisher_sa, publisher_scope_ok = service_account(us)
subscriber_sa, subscriber_scope_ok = service_account(executor)
publisher_member = f"serviceAccount:{publisher_sa}" if publisher_sa else ""
subscriber_member = f"serviceAccount:{subscriber_sa}" if subscriber_sa else ""

publisher_topic_binding = bool(
    publisher_member
    and isinstance(topic_iam, dict)
    and has_binding(topic_iam, "roles/pubsub.publisher", publisher_member)
)
subscriber_subscription_binding = bool(
    subscriber_member
    and isinstance(subscription_iam, dict)
    and has_binding(
        subscription_iam,
        "roles/pubsub.subscriber",
        subscriber_member,
    )
)

publisher_project_roles = (
    project_roles(project_iam, publisher_member) if publisher_member else []
)
subscriber_project_roles = (
    project_roles(project_iam, subscriber_member) if subscriber_member else []
)

dangerous_project_roles = {
    "roles/owner",
    "roles/editor",
    "roles/pubsub.admin",
    "roles/pubsub.editor",
    "roles/pubsub.publisher",
    "roles/pubsub.subscriber",
}

blockers: list[str] = []
if not publisher_sa:
    blockers.append("publisher_service_account_missing_or_ambiguous")
if not subscriber_sa:
    blockers.append("subscriber_service_account_missing_or_ambiguous")
if not publisher_scope_ok:
    blockers.append("publisher_cloud_platform_scope_missing")
if not subscriber_scope_ok:
    blockers.append("subscriber_cloud_platform_scope_missing")
if publisher_sa == default_compute_sa:
    blockers.append("publisher_uses_default_compute_service_account")
if subscriber_sa == default_compute_sa:
    blockers.append("subscriber_uses_default_compute_service_account")
if publisher_sa and publisher_sa == subscriber_sa:
    blockers.append("publisher_and_subscriber_service_accounts_not_separate")
if any(role in dangerous_project_roles for role in publisher_project_roles):
    blockers.append("publisher_has_broad_project_level_role")
if any(role in dangerous_project_roles for role in subscriber_project_roles):
    blockers.append("subscriber_has_broad_project_level_role")

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
if not publisher_topic_binding:
    blockers.append("publisher_topic_role_missing")
if not subscriber_subscription_binding:
    blockers.append("subscriber_subscription_role_missing")

if not identifier.fullmatch(transport_key_id):
    blockers.append("transport_key_id_not_configured_or_invalid")
if not identifier.fullmatch(origin_key_id):
    blockers.append("origin_key_id_not_configured_or_invalid")
if transport_key_id and origin_key_id and transport_key_id == origin_key_id:
    blockers.append("origin_and_transport_key_ids_not_separate")

required_source_truth = {
    "live_trading_enabled": False,
    "phase15_live_trading_enabled": False,
    "phase15_canary_authorized": True,
    "canary_order_submitted": True,
    "first_canary_reconciliation_complete": True,
    "pending_unsubmitted_intent": None,
    "v3_strategy_mutation_performed": False,
    "second_order_authorized": True,
    "automated_real_money_submission": True,
    "manual_real_money_submission_required": False,
    "wallet_material_allowed_on_us_host": False,
    "telegram_one_tap_submission_authorized": True,
    "telegram_persistent_execution_transport_authorized": True,
    "telegram_pubsub_transport_authorized": True,
}
for key, expected in required_source_truth.items():
    if source.get(key) != expected:
        blockers.append(f"source_truth_{key}_not_activation_value")

# The privileged local consumer is implemented but remains inactive until the
# staged release, secrets, IAM, environment files, and services are explicitly activated.

transport_key_path = "/etc/bp-telegram-transport/transport.key"
origin_key_path = "/etc/bp-telegram-transport/origin.key"
listener_handoff_path = "/opt/bp-telegram-transport/bin/approved-outbox-handoff"

env_files = {
    "/etc/bp/telegram-pubsub-publisher.env": {
        "owner": "root",
        "group": "bp",
        "mode": "0640",
        "values": {
            "BP_TELEGRAM_PUBSUB_PUBLISH_WORKER_ENABLED": "yes",
            "BP_TELEGRAM_TRANSPORT_KEY_FILE": transport_key_path,
            "BP_TELEGRAM_TRANSPORT_KEY_ID": transport_key_id or "<required>",
            "BP_TELEGRAM_PUBSUB_PROJECT_ID": project_id,
            "BP_TELEGRAM_PUBSUB_TOPIC_ID": topic_id,
        },
    },
    "/etc/bp/telegram-approval-handoff.env": {
        "owner": "root",
        "group": "bp",
        "mode": "0640",
        "values": {
            "BP_TELEGRAM_HANDOFF_ENABLED": "yes",
            "BP_TELEGRAM_HANDOFF_COMMAND": listener_handoff_path,
            "BP_TELEGRAM_APPROVED_OUTBOX_ENABLED": "yes",
            "BP_TELEGRAM_PROJECT_STATE_FILE": "/etc/bp-telegram-transport/project-state.json",
            "BP_TELEGRAM_ORIGIN_KEY_FILE": origin_key_path,
            "BP_TELEGRAM_ORIGIN_KEY_ID": origin_key_id or "<required>",
            "BP_TELEGRAM_TRANSPORT_KEY_FILE": transport_key_path,
            "BP_TELEGRAM_TRANSPORT_KEY_ID": transport_key_id or "<required>",
        },
    },
    "/etc/bp-telegram-transport/receiver.env": {
        "owner": "root",
        "group": "bp-transport",
        "mode": "0640",
        "values": {
            "BP_TELEGRAM_PUBSUB_STREAMING_RECEIVE_ENABLED": "yes",
            "BP_TELEGRAM_TRANSPORT_KEY_FILE": transport_key_path,
            "BP_TELEGRAM_TRANSPORT_KEY_ID": transport_key_id or "<required>",
            "BP_TELEGRAM_PUBSUB_PROJECT_ID": project_id,
            "BP_TELEGRAM_PUBSUB_SUBSCRIPTION_ID": subscription_id,
        },
    },
    "/etc/bp-telegram-transport/claim.env": {
        "owner": "root",
        "group": "bp-transport",
        "mode": "0640",
        "values": {
            "BP_TELEGRAM_TRANSPORT_CLAIM_WORKER_ENABLED": "yes",
            "BP_TELEGRAM_TRANSPORT_KEY_FILE": transport_key_path,
            "BP_TELEGRAM_TRANSPORT_KEY_ID": transport_key_id or "<required>",
        },
    },
    "/etc/bp-telegram-transport/execution-auth.env": {
        "owner": "root",
        "group": "root",
        "mode": "0600",
        "values": {
            "BP_TELEGRAM_EXECUTION_AUTH_WORKER_ENABLED": "yes",
            "BP_TELEGRAM_ORIGIN_KEY_FILE": origin_key_path,
            "BP_TELEGRAM_ORIGIN_KEY_ID": origin_key_id or "<required>",
        },
    },
    "/etc/bp-telegram-transport/privileged-handoff.env": {
        "owner": "root",
        "group": "root",
        "mode": "0600",
        "values": {
            "BP_TELEGRAM_PRIVILEGED_HANDOFF_ENABLED": "yes",
        },
    },
}

key_files = {
    "recorder_transport_key": {
        "path": transport_key_path,
        "owner": "root",
        "group": "bp",
        "mode": "0640",
        "material": "<never emitted by planner>",
    },
    "recorder_origin_key": {
        "path": origin_key_path,
        "owner": "root",
        "group": "bp",
        "mode": "0640",
        "material": "<never emitted by planner>",
    },
    "executor_transport_key": {
        "path": transport_key_path,
        "owner": "root",
        "group": "bp-transport",
        "mode": "0640",
        "material": "<same transport material as recorder; never emitted>",
    },
    "executor_origin_key": {
        "path": origin_key_path,
        "owner": "root",
        "group": "root",
        "mode": "0600",
        "material": "<same origin material as recorder; never emitted>",
    },
}

report = {
    "schema_version": 1,
    "purpose": "phase15-v3-telegram-transport-activation-plan-v1",
    "status": "blocked" if blockers else "ready_for_activation_review",
    "activation_permitted": False,
    "release_head": release_head,
    "blockers": blockers,
    "source_truth_current": source,
    "source_truth_required_for_future_activation": required_source_truth,
    "pubsub": {
        "project_id": project_id,
        "topic_id": topic_id,
        "topic_name": topic_name,
        "topic_exists": topic is not None,
        "subscription_id": subscription_id,
        "subscription_name": subscription_name,
        "subscription_exists": subscription is not None,
        "publisher_service_account": publisher_sa,
        "subscriber_service_account": subscriber_sa,
        "publisher_topic_binding_present": publisher_topic_binding,
        "subscriber_subscription_binding_present": subscriber_subscription_binding,
        "required_iam_bindings": [
            {
                "resource": topic_name,
                "role": "roles/pubsub.publisher",
                "member": publisher_member or "<publisher service account unresolved>",
            },
            {
                "resource": subscription_name,
                "role": "roles/pubsub.subscriber",
                "member": subscriber_member or "<subscriber service account unresolved>",
            },
        ],
        "publisher_project_roles": publisher_project_roles,
        "subscriber_project_roles": subscriber_project_roles,
    },
    "key_ids": {
        "transport": transport_key_id or "<required>",
        "origin": origin_key_id or "<required>",
        "must_use_different_material": True,
        "secret_material_generated": False,
        "secret_material_emitted": False,
    },
    "key_files": key_files,
    "environment_files": env_files,
    "activation_helper": "scripts/deploy/phase15_v3_telegram_transport_activate_cloudshell.sh",
    "service_activation_order_after_all_future_authorizations": [
        "bp-phase15-telegram-pubsub-streaming-receiver.service",
        "bp-phase15-telegram-transport-claim-worker.service",
        "bp-phase15-telegram-execution-authorization-worker.service",
        "bp-phase15-telegram-privileged-handoff.service",
        "bp-phase15-telegram-pubsub-publisher.service",
        "bp-phase15-canary-telegram-approval.service (restart only after reviewed handoff env exists)",
    ],
    "mandatory_preconditions_not_executed_by_planner": [
        "transport stage status PASS on both hosts",
        "separate key provisioning with different origin/transport material",
        "resource-scoped Pub/Sub IAM provisioning",
        "privileged local executor handoff unit and release hashes verified on Johannesburg",
        "read-only execution authorization package verifier PASS immediately before handoff",
        "read-only privileged handoff contract verifier PASS against the exact executor bytes",
        "explicit source-truth authorization for second order and Telegram automation",
        "independent safe-idle/geoblock/account checks immediately before any live execution",
    ],
    "persistent_execution_authorization_consumer": {
        "defined": True,
        "service": (
            "bp-phase15-telegram-execution-authorization-worker.service"
        ),
        "worker": (
            "scripts/run_phase15_v3_telegram_execution_authorization_worker.py"
        ),
        "package_verifier": (
            "scripts/run_phase15_v3_telegram_execution_package_verify.py"
        ),
        "package_verifier_module": (
            "src/bp_engine/execution/telegram_execution_package.py"
        ),
        "privileged_handoff_contract_verifier": (
            "scripts/run_phase15_v3_telegram_privileged_handoff_verify.py"
        ),
        "privileged_handoff_contract_module": (
            "src/bp_engine/execution/telegram_privileged_handoff.py"
        ),
        "privileged_handoff_contract_defined": True,
        "privileged_handoff_consumer_service": (
            "bp-phase15-telegram-privileged-handoff.service"
        ),
        "privileged_handoff_consumer_worker": (
            "scripts/run_phase15_v3_telegram_privileged_handoff_worker.py"
        ),
        "privileged_handoff_consumer_module": (
            "src/bp_engine/execution/telegram_privileged_consumer.py"
        ),
        "privileged_handoff_consumer_defined": True,
        "origin_key_only": True,
        "network_enabled": False,
        "handoff_invoked": False,
        "executor_invoked": False,
        "real_order_submitted": False,
        "implemented_chain": [
            "execution-ready origin HMAC verification",
            "short-lived signed source-truth authorization verification",
            "fresh exact-main source-truth snapshot authorization evaluation at recorder approval time",
            "one-shot dispatch ticket creation/claim",
            "exact prepared/approval/dispatch binding",
            "immutable Johannesburg handoff package materialization",
            "read-only full package verification before privileged handoff",
            "read-only exact executor identity and handoff contract verification",
            "one-shot privileged executor invocation with terminal no-retry marker",
            "kill-switch re-engagement and local result receipt",
        ],
        "remaining_gap": "deployment_and_runtime_preconditions_pending",
    },
    "mutation_performed": False,
    "secret_generated": False,
    "secret_emitted": False,
    "iam_changed": False,
    "pubsub_resources_changed": False,
    "environment_files_written": False,
    "services_started": False,
    "services_enabled": False,
    "executor_invoked": False,
    "real_order_submitted": False,
}
print(json.dumps(report, indent=2, sort_keys=True))
print("TELEGRAM_TRANSPORT_ACTIVATION_PERMITTED=false")
print("NO_MUTATION_PERFORMED=true")
print("PHASE15_V3_TELEGRAM_TRANSPORT_ACTIVATION_PLAN=PASS")
PY
