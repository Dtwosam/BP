from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESUME = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_telegram_transport_runtime_repair_resume_cloudshell.sh"
)


def test_runtime_repair_resume_shell_is_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(RESUME)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_runtime_repair_resume_is_exact_state_and_fail_closed() -> None:
    text = RESUME.read_text(encoding="utf-8")
    for marker in (
        "PHASE15_ACCEPT_TELEGRAM_TRANSPORT_RUNTIME_REPAIR_RESUME",
        "explicit_transport_runtime_repair_resume_authorization_required",
        'repair.get("status") == "AUTHORIZED_RESUME_PENDING"',
        "runtime_repair_resume_helper_binding_mismatch",
        "second_canary_attempt_marker_present",
        "pubsub_api_not_enabled",
        "TELEGRAM_TRANSPORT_STAGE_READY=true",
        "resume_stage_id_mismatch",
        "resume_release_head_mismatch",
        "PHASE15_TELEGRAM_TRANSPORT_RUNTIME_REPAIR_MODE=yes",
        "TELEGRAM_PUBSUB_READY=true",
        "PHASE15_ACCEPT_TELEGRAM_TRANSPORT_ACTIVATION=yes",
        "second_canary_attempt_marker_created_by_resume",
        "SECOND_CANARY_NETWORK_ATTEMPT_CONSUMED=false",
        "TELEGRAM_APPROVAL_PERFORMED=false",
        "EXECUTOR_ARMED=false",
        "REAL_ORDER_SUBMITTED=false",
        "PHASE15_V3_TELEGRAM_TRANSPORT_RUNTIME_REPAIR_RESUME=PASS",
    ):
        assert marker in text


def test_runtime_repair_resume_does_not_repeat_completed_mutations_or_submit_orders() -> None:
    text = RESUME.read_text(encoding="utf-8")
    for forbidden in (
        "gcloud services enable pubsub.googleapis.com",
        "PHASE15_ACCEPT_TELEGRAM_TRANSPORT_STAGE_ROLLBACK",
        "PHASE15_ACCEPT_TELEGRAM_TRANSPORT_STAGE_INSTALL",
        "phase15_v3_telegram_transport_build_release.py",
        "post_order",
        "create_limit_order",
        "cancel_order",
        '"action":"submit"',
        "PHASE15_ACCEPT_REAL_MONEY",
        "PHASE15_ACCEPT_TELEGRAM_REAL_MONEY",
    ):
        assert forbidden not in text
