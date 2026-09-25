from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAN = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_telegram_transport_activation_plan_cloudshell.sh"
)


def test_activation_plan_shell_and_embedded_python_are_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(PLAN)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    text = PLAN.read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PY'[^\n]*\n(.*?)\nPY(?:\n|$)", text, flags=re.DOTALL)
    assert len(blocks) >= 2
    for block in blocks:
        ast.parse(block)


def test_activation_plan_reuses_existing_pubsub_resource_contract() -> None:
    text = PLAN.read_text(encoding="utf-8")
    for marker in (
        "bp-phase15-telegram-transport-v1",
        "bp-phase15-telegram-exec-v1",
        "roles/pubsub.publisher",
        "roles/pubsub.subscriber",
        "publisher_service_account",
        "subscriber_service_account",
        "publisher_topic_binding_present",
        "subscriber_subscription_binding_present",
        "publisher_has_broad_project_level_role",
        "subscriber_has_broad_project_level_role",
    ):
        assert marker in text


def test_activation_plan_spells_out_secret_free_runtime_configuration() -> None:
    text = PLAN.read_text(encoding="utf-8")
    for marker in (
        "/etc/bp/telegram-pubsub-publisher.env",
        "/etc/bp/telegram-approval-handoff.env",
        "/etc/bp-telegram-transport/receiver.env",
        "/etc/bp-telegram-transport/claim.env",
        "/etc/bp-telegram-transport/execution-auth.env",
        "/etc/bp-telegram-transport/transport.key",
        "/etc/bp-telegram-transport/origin.key",
        "/opt/bp-telegram-transport/bin/approved-outbox-handoff",
        "BP_TELEGRAM_PUBSUB_PUBLISH_WORKER_ENABLED",
        "BP_TELEGRAM_PUBSUB_STREAMING_RECEIVE_ENABLED",
        "BP_TELEGRAM_TRANSPORT_CLAIM_WORKER_ENABLED",
        "BP_TELEGRAM_EXECUTION_AUTH_WORKER_ENABLED",
        "BP_TELEGRAM_HANDOFF_ENABLED",
        "BP_TELEGRAM_APPROVED_OUTBOX_ENABLED",
        "BP_TELEGRAM_PROJECT_STATE_FILE",
        "BP_TELEGRAM_ORIGIN_KEY_FILE",
        "BP_TELEGRAM_TRANSPORT_KEY_FILE",
        '"secret_material_generated": False',
        '"secret_material_emitted": False',
        "<never emitted by planner>",
    ):
        assert marker in text


def test_activation_plan_requires_future_live_authorizations_but_never_grants_them() -> None:
    text = PLAN.read_text(encoding="utf-8")
    for marker in (
        '"second_order_authorized": True',
        '"automated_real_money_submission": True',
        '"manual_real_money_submission_required": False',
        '"telegram_one_tap_submission_authorized": True',
        '"telegram_persistent_execution_transport_authorized": True',
        '"telegram_pubsub_transport_authorized": True',
        "privileged_execution_handoff_not_defined",
        '"defined": True',
        '"network_enabled": False',
        '"handoff_invoked": False',
        '"executor_invoked": False',
        '"real_order_submitted": False',
        "TELEGRAM_TRANSPORT_ACTIVATION_PERMITTED=false",
    ):
        assert marker in text


def test_activation_plan_documents_authorization_consumer_and_remaining_handoff_gap() -> None:
    text = PLAN.read_text(encoding="utf-8")
    for marker in (
        "execution-ready origin HMAC verification",
        "short-lived signed source-truth authorization verification",
        "fresh PROJECT_STATE authorization evaluation at recorder approval time",
        "one-shot dispatch ticket creation/claim",
        "exact prepared/approval/dispatch binding",
        "immutable Johannesburg handoff package materialization",
        "privileged local executor handoff consumer implemented and reviewed",
        "bp-phase15-telegram-execution-authorization-worker.service",
        "run_phase15_v3_telegram_execution_authorization_worker.py",
        "run_phase15_v3_telegram_execution_package_verify.py",
        "src/bp_engine/execution/telegram_execution_package.py",
        "read-only full package verification before privileged handoff",
        "read-only execution authorization package verifier PASS immediately before handoff",
        '"remaining_gap": "privileged_execution_handoff_not_defined"',
    ):
        assert marker in text


def test_activation_plan_is_strictly_read_only() -> None:
    text = PLAN.read_text(encoding="utf-8")
    for forbidden in (
        "gcloud compute scp",
        "gcloud pubsub topics create",
        "gcloud pubsub subscriptions create",
        "add-iam-policy-binding",
        "set-iam-policy",
        "systemctl start",
        "systemctl restart",
        "systemctl enable",
        "systemctl stop",
        "systemctl disable",
        "systemctl daemon-reload",
        "useradd",
        "userdel",
        "groupadd",
        "groupdel",
        "install -",
        "mkdir -",
        "rm -",
        "tar -x",
        "pip install",
        "post_order",
        "create_limit_order",
        "cancel_order",
        "phase15_v3_canary_arm_cloudshell.sh",
        "PHASE15_ACCEPT_REAL_MONEY",
        "rm -f /etc/bp-canary/KILL",
    ):
        assert forbidden not in text

    for marker in (
        '"mutation_performed": False',
        '"iam_changed": False',
        '"pubsub_resources_changed": False',
        '"environment_files_written": False',
        '"services_started": False',
        '"services_enabled": False',
        '"executor_invoked": False',
        '"real_order_submitted": False',
        "NO_MUTATION_PERFORMED=true",
    ):
        assert marker in text
