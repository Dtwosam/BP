from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REMEDIATE = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_telegram_transport_identity_remediation_cloudshell.sh"
)


def test_identity_remediation_shell_and_embedded_python_are_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(REMEDIATE)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    text = REMEDIATE.read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PY'[^\n]*\n(.*?)\nPY(?:\n|$)", text, flags=re.DOTALL)
    assert len(blocks) >= 5
    for block in blocks:
        ast.parse(block)


def test_identity_remediation_requires_current_authorized_source_truth_and_stage() -> None:
    text = REMEDIATE.read_text(encoding="utf-8")
    for marker in (
        "PHASE15_ACCEPT_TELEGRAM_TRANSPORT_ACTIVATION",
        "explicit_transport_activation_authorization_required",
        "local_main_not_current",
        'assert state["live_trading_enabled"] is False',
        'assert gate["live_trading_enabled"] is False',
        'stage.get("status") == "PRODUCTION_STAGED_INACTIVE"',
        'stage.get("activation_authorized") is True',
        'activation.get("status") == "AUTHORIZED_NOT_ACTIVATED"',
        "phase15_v3_telegram_transport_stage_status_cloudshell.sh",
        "TELEGRAM_TRANSPORT_STAGE_READY=true",
        "phase15_v3_telegram_approval_status_cloudshell.sh",
        "LISTENER_BINDING_CURRENT=true",
        "HANDOFF_CONFIGURED=false",
    ):
        assert marker in text


def test_identity_remediation_uses_dedicated_accounts_and_cloud_platform_scope() -> None:
    text = REMEDIATE.read_text(encoding="utf-8")
    for marker in (
        "bp-phase15-telegram-publisher",
        "bp-phase15-telegram-subscriber",
        "iam service-accounts create",
        "https://www.googleapis.com/auth/cloud-platform",
        "publisher_service_account_is_default",
        "subscriber_service_account_is_default",
        "service_accounts_not_distinct",
        "dedicated_service_account_has_project_level_role",
    ):
        assert marker in text
    assert "gcloud projects add-iam-policy-binding" not in text
    assert "gcloud iam service-accounts add-iam-policy-binding" not in text


def test_identity_remediation_stops_changes_and_restarts_existing_vms() -> None:
    text = REMEDIATE.read_text(encoding="utf-8")
    for marker in (
        "gcloud compute instances stop",
        "gcloud compute instances set-service-account",
        "gcloud compute instances start",
        '--service-account="$email"',
        '--scopes="$CLOUD_SCOPE"',
        "wait_running",
        "wait_ssh",
        "restore_identity",
        "--no-service-account --no-scopes",
    ):
        assert marker in text


def test_identity_remediation_preserves_inactive_transport_and_safe_idle() -> None:
    text = REMEDIATE.read_text(encoding="utf-8")
    for marker in (
        "bp-phase15-telegram-pubsub-publisher.service",
        "bp-phase15-telegram-pubsub-streaming-receiver.service",
        "bp-phase15-telegram-transport-claim-worker.service",
        "bp-phase15-telegram-execution-authorization-worker.service",
        "bp-phase15-telegram-privileged-handoff.service",
        'payload["kill_switch_engaged"] is True',
        'payload["activation_valid"] is False',
        'payload["submission_ready"] is False',
        'payload["live_order_submitted"] is False',
        'account.get("clean_for_canary") is True',
        'int(account.get("open_order_count", -1)) == 0',
        "PHASE15_V3_TELEGRAM_TRANSPORT_IDENTITY_REMEDIATION=PASS",
        "TRANSPORT_SERVICES_STARTED=false",
        "TRANSPORT_SERVICES_ENABLED=false",
        "GLOBAL_LIVE_TRADING_ENABLED=false",
        "REAL_ORDER_SUBMITTED=false",
    ):
        assert marker in text


def test_identity_remediation_has_no_pubsub_or_order_mutation() -> None:
    text = REMEDIATE.read_text(encoding="utf-8")
    for forbidden in (
        "gcloud pubsub topics create",
        "gcloud pubsub subscriptions create",
        "gcloud pubsub topics add-iam-policy-binding",
        "gcloud pubsub subscriptions add-iam-policy-binding",
        "post_order",
        "create_limit_order",
        "cancel_order",
        "PHASE15_ACCEPT_REAL_MONEY",
        "rm -f /etc/bp-canary/KILL",
    ):
        assert forbidden not in text
