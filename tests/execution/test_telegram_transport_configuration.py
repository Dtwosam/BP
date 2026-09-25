from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from bp_engine.execution.telegram_pre_execution import source_truth_sha256
from bp_engine.execution.telegram_transport_configuration import (
    CLAIM_ENV_PATH,
    ORIGIN_KEY_PATH,
    PUBLISHER_ENV_PATH,
    RECEIVER_ENV_PATH,
    TRANSPORT_KEY_PATH,
    TransportConfigurationError,
    create_configuration_plan,
    verify_configuration_plan,
)

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "PROJECT_STATE.json"
HEAD = "1" * 40


def _state() -> dict[str, object]:
    return json.loads(STATE.read_text(encoding="utf-8"))


def test_current_source_truth_produces_blocked_secret_free_plan() -> None:
    state = _state()
    plan = create_configuration_plan(state, release_head=HEAD)

    assert plan["source_truth_sha256"] == source_truth_sha256(state)
    assert plan["source_truth_authorized_for_activation"] is False
    assert plan["source_truth_blockers"] == [
        "second_order_not_authorized",
        "automated_real_money_submission_not_authorized",
        "manual_submission_still_required",
        "telegram_one_tap_not_authorized",
        "persistent_execution_transport_not_authorized",
        "telegram_pubsub_transport_not_authorized",
    ]
    assert plan["activation_contract"]["plan_authorizes_activation"] is False
    assert plan["secrets_included"] is False
    assert plan["mutation_performed"] is False
    assert plan["network_action_performed"] is False
    assert plan["secret_provisioning_performed"] is False
    assert plan["service_started"] is False
    assert plan["service_enabled"] is False
    assert plan["executor_invoked"] is False
    assert plan["real_order_submitted"] is False


def test_configuration_plan_exactly_describes_runtime_env_contract() -> None:
    plan = create_configuration_plan(_state(), release_head=HEAD)
    env = plan["environment_files"]

    assert env["publisher"] == {
        "host": "bp-recorder",
        "path": PUBLISHER_ENV_PATH,
        "mode": "0600",
        "values": {
            "BP_TELEGRAM_PUBSUB_PUBLISH_WORKER_ENABLED": "yes",
            "BP_TELEGRAM_TRANSPORT_KEY_FILE": TRANSPORT_KEY_PATH,
            "BP_TELEGRAM_TRANSPORT_KEY_ID": "phase15-telegram-transport-v1",
            "BP_TELEGRAM_PUBSUB_PROJECT_ID": (
                "project-4397f2c0-7098-4c1c-abb"
            ),
            "BP_TELEGRAM_PUBSUB_TOPIC_ID": "bp-phase15-telegram-transport-v1",
        },
        "contains_secret_value": False,
    }
    assert env["receiver"] == {
        "host": "bp-v3-canary-exec",
        "path": RECEIVER_ENV_PATH,
        "mode": "0640",
        "values": {
            "BP_TELEGRAM_PUBSUB_STREAMING_RECEIVE_ENABLED": "yes",
            "BP_TELEGRAM_TRANSPORT_KEY_FILE": TRANSPORT_KEY_PATH,
            "BP_TELEGRAM_TRANSPORT_KEY_ID": "phase15-telegram-transport-v1",
            "BP_TELEGRAM_PUBSUB_PROJECT_ID": (
                "project-4397f2c0-7098-4c1c-abb"
            ),
            "BP_TELEGRAM_PUBSUB_SUBSCRIPTION_ID": "bp-phase15-telegram-exec-v1",
        },
        "contains_secret_value": False,
    }
    assert env["claim_worker"] == {
        "host": "bp-v3-canary-exec",
        "path": CLAIM_ENV_PATH,
        "mode": "0640",
        "values": {
            "BP_TELEGRAM_TRANSPORT_CLAIM_WORKER_ENABLED": "yes",
            "BP_TELEGRAM_TRANSPORT_KEY_FILE": TRANSPORT_KEY_PATH,
            "BP_TELEGRAM_TRANSPORT_KEY_ID": "phase15-telegram-transport-v1",
        },
        "contains_secret_value": False,
    }


def test_configuration_plan_preserves_origin_secret_compartmentalization() -> None:
    plan = create_configuration_plan(_state(), release_head=HEAD)
    keys = plan["key_contract"]
    serialized_env = json.dumps(plan["environment_files"], sort_keys=True)

    assert keys["transport"]["path"] == TRANSPORT_KEY_PATH
    assert keys["origin"]["path"] == ORIGIN_KEY_PATH
    assert keys["transport"]["secret_value_included"] is False
    assert keys["origin"]["secret_value_included"] is False
    assert keys["key_material_must_be_distinct"] is True
    assert keys["key_material_generated_by_plan"] is False
    assert ORIGIN_KEY_PATH not in serialized_env
    assert keys["origin"]["consumers"] == ["telegram_execution_ready_verifier"]
    assert set(keys["origin"]["inaccessible_to"]) == {
        "bp-phase15-telegram-pubsub-publisher.service",
        "bp-phase15-telegram-pubsub-streaming-receiver.service",
        "bp-phase15-telegram-transport-claim-worker.service",
    }


def test_even_synthetic_authorized_source_truth_does_not_make_plan_activation() -> None:
    state = copy.deepcopy(_state())
    phase = state["phase_15_v3_live_canary"]
    phase["second_order_authorized"] = True
    phase["automated_real_money_submission"] = True
    phase["manual_real_money_submission_required"] = False
    phase["telegram_one_tap_submission_authorized"] = True
    phase["telegram_persistent_execution_transport_authorized"] = True
    phase["telegram_pubsub_transport_authorized"] = True

    plan = create_configuration_plan(state, release_head=HEAD)
    assert plan["source_truth_authorized_for_activation"] is True
    assert plan["source_truth_blockers"] == []
    assert plan["activation_contract"]["plan_authorizes_activation"] is False
    assert plan["service_started"] is False
    assert plan["service_enabled"] is False
    assert plan["real_order_submitted"] is False


def test_configuration_plan_hash_and_verification_fail_closed_on_drift() -> None:
    state = _state()
    plan = create_configuration_plan(state, release_head=HEAD)

    assert verify_configuration_plan(
        plan,
        project_state=state,
        release_head=HEAD,
    ) == plan

    changed = copy.deepcopy(plan)
    changed["cloud_resources"]["topic_id"] = "different-topic"
    with pytest.raises(
        TransportConfigurationError,
        match="stale or modified",
    ):
        verify_configuration_plan(
            changed,
            project_state=state,
            release_head=HEAD,
        )


def test_configuration_plan_rejects_invalid_or_same_key_ids() -> None:
    state = _state()
    with pytest.raises(TransportConfigurationError, match="must be distinct"):
        create_configuration_plan(
            state,
            release_head=HEAD,
            transport_key_id="same-key",
            origin_key_id="same-key",
        )
    with pytest.raises(TransportConfigurationError, match="project id invalid"):
        create_configuration_plan(
            state,
            release_head=HEAD,
            project_id="BAD PROJECT",
        )
