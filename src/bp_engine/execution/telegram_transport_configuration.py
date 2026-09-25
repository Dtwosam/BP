from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

CONFIGURATION_PLAN_SCHEMA_VERSION = 1
CONFIGURATION_PLAN_PURPOSE = (
    "phase15-v3-telegram-transport-configuration-plan-v1"
)
DEFAULT_PROJECT_ID = "project-4397f2c0-7098-4c1c-abb"
DEFAULT_TOPIC_ID = "bp-phase15-telegram-transport-v1"
DEFAULT_SUBSCRIPTION_ID = "bp-phase15-telegram-exec-v1"
DEFAULT_TRANSPORT_KEY_ID = "phase15-telegram-transport-v1"
DEFAULT_ORIGIN_KEY_ID = "phase15-telegram-origin-v1"

TRANSPORT_KEY_PATH = "/etc/bp-telegram-transport/transport.key"
ORIGIN_KEY_PATH = "/etc/bp-telegram-transport/origin.key"
PUBLISHER_ENV_PATH = "/etc/bp/telegram-pubsub-publisher.env"
RECEIVER_ENV_PATH = "/etc/bp-telegram-transport/receiver.env"
CLAIM_ENV_PATH = "/etc/bp-telegram-transport/claim.env"


class TransportConfigurationError(RuntimeError):
    pass


def _canonical(payload: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TransportConfigurationError(
            "configuration plan is not canonicalizable"
        ) from exc


def payload_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _identifier(value: str, *, label: str, maximum: int = 128) -> str:
    normalized = value.strip()
    if not normalized or len(normalized.encode("utf-8")) > maximum:
        raise TransportConfigurationError(f"{label} invalid")
    if re.fullmatch(r"[A-Za-z0-9._-]+", normalized) is None:
        raise TransportConfigurationError(f"{label} invalid")
    return normalized


def _project_id(value: str) -> str:
    normalized = value.strip()
    if (
        len(normalized) < 6
        or len(normalized) > 63
        or re.fullmatch(r"[a-z][a-z0-9-]*[a-z0-9]", normalized) is None
    ):
        raise TransportConfigurationError("project id invalid")
    return normalized


def _release_head(value: str) -> str:
    normalized = value.strip().lower()
    if re.fullmatch(r"[0-9a-f]{40}", normalized) is None:
        raise TransportConfigurationError("release head must be a 40-character SHA")
    return normalized


def source_truth_blockers(project_state: Mapping[str, Any]) -> list[str]:
    phase = project_state.get("phase_15_v3_live_canary")
    if not isinstance(phase, Mapping):
        raise TransportConfigurationError("phase 15 source truth missing")

    first_canary = phase.get("first_live_canary")
    if not isinstance(first_canary, Mapping):
        first_canary = {}

    blockers: list[str] = []
    if project_state.get("live_trading_enabled") is not False:
        blockers.append("global_live_trading_not_safely_disabled")
    if phase.get("live_trading_enabled") is not False:
        blockers.append("phase15_live_trading_not_safely_disabled")
    if phase.get("phase15_canary_authorized") is not True:
        blockers.append("phase15_canary_not_authorized")
    if phase.get("canary_order_submitted") is not True:
        blockers.append("first_canary_not_submitted")
    if first_canary.get("official_reconciliation_complete") is not True:
        blockers.append("first_canary_reconciliation_not_complete")
    if phase.get("pending_unsubmitted_intent") is not None:
        blockers.append("pending_unsubmitted_intent_present")
    if phase.get("v3_strategy_mutation_performed") is not False:
        blockers.append("v3_strategy_mutation_detected")
    if phase.get("second_order_authorized") is not True:
        blockers.append("second_order_not_authorized")
    if phase.get("automated_real_money_submission") is not True:
        blockers.append("automated_real_money_submission_not_authorized")
    if phase.get("manual_real_money_submission_required") is not False:
        blockers.append("manual_submission_still_required")
    if phase.get("telegram_one_tap_submission_authorized") is not True:
        blockers.append("telegram_one_tap_not_authorized")
    if phase.get("telegram_persistent_execution_transport_authorized") is not True:
        blockers.append("persistent_execution_transport_not_authorized")
    if phase.get("telegram_pubsub_transport_authorized") is not True:
        blockers.append("telegram_pubsub_transport_not_authorized")
    return blockers


def create_configuration_plan(
    project_state: Mapping[str, Any],
    *,
    release_head: str,
    project_id: str = DEFAULT_PROJECT_ID,
    topic_id: str = DEFAULT_TOPIC_ID,
    subscription_id: str = DEFAULT_SUBSCRIPTION_ID,
    transport_key_id: str = DEFAULT_TRANSPORT_KEY_ID,
    origin_key_id: str = DEFAULT_ORIGIN_KEY_ID,
) -> dict[str, Any]:
    head = _release_head(release_head)
    project = _project_id(project_id)
    topic = _identifier(topic_id, label="topic id")
    subscription = _identifier(subscription_id, label="subscription id")
    transport_id = _identifier(transport_key_id, label="transport key id", maximum=64)
    origin_id = _identifier(origin_key_id, label="origin key id", maximum=64)
    if transport_id == origin_id:
        raise TransportConfigurationError(
            "transport and origin key ids must be distinct"
        )

    blockers = source_truth_blockers(project_state)
    source_hash = payload_sha256(project_state)

    publisher_env = {
        "BP_TELEGRAM_PUBSUB_PUBLISH_WORKER_ENABLED": "yes",
        "BP_TELEGRAM_TRANSPORT_KEY_FILE": TRANSPORT_KEY_PATH,
        "BP_TELEGRAM_TRANSPORT_KEY_ID": transport_id,
        "BP_TELEGRAM_PUBSUB_PROJECT_ID": project,
        "BP_TELEGRAM_PUBSUB_TOPIC_ID": topic,
    }
    receiver_env = {
        "BP_TELEGRAM_PUBSUB_STREAMING_RECEIVE_ENABLED": "yes",
        "BP_TELEGRAM_TRANSPORT_KEY_FILE": TRANSPORT_KEY_PATH,
        "BP_TELEGRAM_TRANSPORT_KEY_ID": transport_id,
        "BP_TELEGRAM_PUBSUB_PROJECT_ID": project,
        "BP_TELEGRAM_PUBSUB_SUBSCRIPTION_ID": subscription,
    }
    claim_env = {
        "BP_TELEGRAM_TRANSPORT_CLAIM_WORKER_ENABLED": "yes",
        "BP_TELEGRAM_TRANSPORT_KEY_FILE": TRANSPORT_KEY_PATH,
        "BP_TELEGRAM_TRANSPORT_KEY_ID": transport_id,
    }

    plan: dict[str, Any] = {
        "schema_version": CONFIGURATION_PLAN_SCHEMA_VERSION,
        "purpose": CONFIGURATION_PLAN_PURPOSE,
        "release_head": head,
        "source_truth_sha256": source_hash,
        "source_truth_authorized_for_activation": not blockers,
        "source_truth_blockers": blockers,
        "cloud_resources": {
            "project_id": project,
            "topic_id": topic,
            "topic_name": f"projects/{project}/topics/{topic}",
            "subscription_id": subscription,
            "subscription_name": (
                f"projects/{project}/subscriptions/{subscription}"
            ),
            "subscription_topic": f"projects/{project}/topics/{topic}",
        },
        "iam_contract": {
            "publisher": {
                "resource": f"projects/{project}/topics/{topic}",
                "required_role": "roles/pubsub.publisher",
                "project_level_role_allowed": False,
                "dedicated_service_account_required": True,
            },
            "subscriber": {
                "resource": f"projects/{project}/subscriptions/{subscription}",
                "required_role": "roles/pubsub.subscriber",
                "project_level_role_allowed": False,
                "dedicated_service_account_required": True,
            },
            "publisher_and_subscriber_accounts_must_be_distinct": True,
            "default_compute_service_account_allowed": False,
        },
        "key_contract": {
            "transport": {
                "key_id": transport_id,
                "path": TRANSPORT_KEY_PATH,
                "secret_value_included": False,
                "required_on_hosts": ["bp-recorder", "bp-v3-canary-exec"],
                "consumers": [
                    "bp-phase15-telegram-pubsub-publisher.service",
                    "bp-phase15-telegram-pubsub-streaming-receiver.service",
                    "bp-phase15-telegram-transport-claim-worker.service",
                ],
            },
            "origin": {
                "key_id": origin_id,
                "path": ORIGIN_KEY_PATH,
                "secret_value_included": False,
                "required_on_hosts": ["bp-v3-canary-exec"],
                "consumers": ["telegram_execution_ready_verifier"],
                "inaccessible_to": [
                    "bp-phase15-telegram-pubsub-publisher.service",
                    "bp-phase15-telegram-pubsub-streaming-receiver.service",
                    "bp-phase15-telegram-transport-claim-worker.service",
                ],
            },
            "key_material_must_be_distinct": True,
            "key_material_generated_by_plan": False,
        },
        "environment_files": {
            "publisher": {
                "host": "bp-recorder",
                "path": PUBLISHER_ENV_PATH,
                "mode": "0600",
                "values": publisher_env,
                "contains_secret_value": False,
            },
            "receiver": {
                "host": "bp-v3-canary-exec",
                "path": RECEIVER_ENV_PATH,
                "mode": "0640",
                "values": receiver_env,
                "contains_secret_value": False,
            },
            "claim_worker": {
                "host": "bp-v3-canary-exec",
                "path": CLAIM_ENV_PATH,
                "mode": "0640",
                "values": claim_env,
                "contains_secret_value": False,
            },
        },
        "service_contract": [
            {
                "host": "bp-recorder",
                "unit": "bp-phase15-telegram-pubsub-publisher.service",
                "staged_state": "inactive_disabled",
                "activation_requires_fresh_authorization": True,
            },
            {
                "host": "bp-v3-canary-exec",
                "unit": "bp-phase15-telegram-pubsub-streaming-receiver.service",
                "staged_state": "inactive_disabled",
                "activation_requires_fresh_authorization": True,
            },
            {
                "host": "bp-v3-canary-exec",
                "unit": "bp-phase15-telegram-transport-claim-worker.service",
                "staged_state": "inactive_disabled",
                "activation_requires_fresh_authorization": True,
            },
        ],
        "systemd_safety_environment": {
            "MODE": "research",
            "LIVE_TRADING_ENABLED": "false",
            "MAX_TRADE_SIZE_USD": "0",
            "MAX_DAILY_LOSS_USD": "0",
        },
        "activation_contract": {
            "plan_authorizes_activation": False,
            "requires_fresh_source_truth_authorization": True,
            "requires_pubsub_readiness_pass": True,
            "requires_stage_status_pass": True,
            "requires_separate_cloud_resource_mutation_authorization": True,
            "requires_separate_secret_provisioning_authorization": True,
            "requires_separate_service_activation_authorization": True,
            "requires_live_order_authorization": True,
        },
        "secrets_included": False,
        "mutation_performed": False,
        "network_action_performed": False,
        "cloud_resource_mutation_performed": False,
        "iam_mutation_performed": False,
        "secret_provisioning_performed": False,
        "service_started": False,
        "service_enabled": False,
        "executor_invoked": False,
        "real_order_submitted": False,
    }
    plan["configuration_plan_sha256"] = payload_sha256(plan)
    return plan


def verify_configuration_plan(
    plan: Mapping[str, Any],
    *,
    project_state: Mapping[str, Any],
    release_head: str,
    project_id: str = DEFAULT_PROJECT_ID,
    topic_id: str = DEFAULT_TOPIC_ID,
    subscription_id: str = DEFAULT_SUBSCRIPTION_ID,
    transport_key_id: str = DEFAULT_TRANSPORT_KEY_ID,
    origin_key_id: str = DEFAULT_ORIGIN_KEY_ID,
) -> dict[str, Any]:
    expected = create_configuration_plan(
        project_state,
        release_head=release_head,
        project_id=project_id,
        topic_id=topic_id,
        subscription_id=subscription_id,
        transport_key_id=transport_key_id,
        origin_key_id=origin_key_id,
    )
    if dict(plan) != expected:
        raise TransportConfigurationError(
            "configuration plan is stale or modified"
        )
    return expected
