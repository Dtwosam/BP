from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPAIR = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_telegram_transport_runtime_repair_cloudshell.sh"
)


def test_runtime_repair_shell_is_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(REPAIR)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_runtime_repair_is_explicitly_authorized_and_fail_closed() -> None:
    text = REPAIR.read_text(encoding="utf-8")
    for marker in (
        "PHASE15_ACCEPT_TELEGRAM_TRANSPORT_RUNTIME_REPAIR",
        "explicit_transport_runtime_repair_authorization_required",
        "local_main_not_current",
        "runtime_repair_helper_binding_mismatch",
        "second_canary_attempt_marker_present",
        "executor_not_safe_idle",
        "PHASE15_ACCEPT_TELEGRAM_TRANSPORT_STAGE_ROLLBACK=yes",
        "gcloud services enable pubsub.googleapis.com",
        "pubsub_api_enablement_not_observed",
        "phase15_v3_telegram_transport_build_release.py",
        "PHASE15_ACCEPT_TELEGRAM_TRANSPORT_STAGE_INSTALL=yes",
        "TELEGRAM_PUBSUB_READY=true",
        "PHASE15_TELEGRAM_TRANSPORT_RUNTIME_REPAIR_MODE=yes",
        "PHASE15_ACCEPT_TELEGRAM_TRANSPORT_ACTIVATION=yes",
        "second_canary_attempt_marker_created_by_repair",
        "SECOND_CANARY_NETWORK_ATTEMPT_CONSUMED=false",
        "TELEGRAM_APPROVAL_PERFORMED=false",
        "EXECUTOR_ARMED=false",
        "REAL_ORDER_SUBMITTED=false",
        "PHASE15_V3_TELEGRAM_TRANSPORT_RUNTIME_REPAIR=PASS",
    ):
        assert marker in text


def test_runtime_repair_never_contains_order_submission_logic() -> None:
    text = REPAIR.read_text(encoding="utf-8")
    for forbidden in (
        "post_order",
        "create_limit_order",
        "cancel_order",
        '"action":"submit"',
        "'action':'submit'",
        "PHASE15_ACCEPT_REAL_MONEY",
        "PHASE15_ACCEPT_TELEGRAM_REAL_MONEY",
    ):
        assert forbidden not in text
