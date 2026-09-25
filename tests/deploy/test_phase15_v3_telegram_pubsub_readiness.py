from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
READINESS = ROOT / "scripts/deploy/phase15_v3_telegram_pubsub_readiness_cloudshell.sh"


def test_pubsub_readiness_is_read_only_and_checks_least_privilege_shape() -> None:
    text = READINESS.read_text(encoding="utf-8")
    for marker in (
        "publisher_uses_default_compute_service_account",
        "subscriber_uses_default_compute_service_account",
        "publisher_and_subscriber_service_accounts_not_separate",
        "publisher_vm_cloud_platform_scope_missing",
        "subscriber_vm_cloud_platform_scope_missing",
        "roles/pubsub.publisher",
        "roles/pubsub.subscriber",
        "publisher_topic_role_missing",
        "subscriber_subscription_role_missing",
        "publisher_has_broad_project_level_role",
        "subscriber_has_broad_project_level_role",
        '"telegram_pubsub_transport_authorized"',
        "telegram_pubsub_transport_not_authorized",
        "TELEGRAM_PUBSUB_READY=false",
        "NO_MUTATION_PERFORMED=true",
        '"mutation_performed": False',
        '"real_order_submitted": False',
    ):
        assert marker in text

    for forbidden in (
        "gcloud pubsub topics create",
        "gcloud pubsub subscriptions create",
        "set-iam-policy",
        "add-iam-policy-binding",
        "remove-iam-policy-binding",
        "instances set-service-account",
        "iam service-accounts create",
        "systemctl start",
        "systemctl restart",
        "systemctl enable",
        "PHASE15_ACCEPT_REAL_MONEY",
        "post_order",
        "create_limit_order",
        "/opt/bp-canary/executor.sh",
    ):
        assert forbidden not in text


def test_pubsub_readiness_shell_and_embedded_python_are_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(READINESS)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    text = READINESS.read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PY'[^\n]*\n(.*?)\nPY(?:\n|$)", text, flags=re.DOTALL)
    assert len(blocks) >= 2
    for block in blocks:
        ast.parse(block)

def test_pubsub_readiness_requires_transport_activation_authorization() -> None:
    text = READINESS.read_text(encoding="utf-8")
    assert '"telegram_transport_activation_authorized"' in text
    assert "telegram_transport_activation_not_authorized" in text
