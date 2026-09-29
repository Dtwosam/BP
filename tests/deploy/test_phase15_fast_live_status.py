from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATUS = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_fast_live_status_cloudshell.sh"
)


def test_fast_live_status_helper_is_shell_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(STATUS)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_fast_live_status_helper_is_read_only_and_complete() -> None:
    text = STATUS.read_text(encoding="utf-8")

    for marker in (
        "PHASE15_FAST_LIVE_STATUS=PASS",
        "RECORDER_SOURCE_ACTIVE",
        "TELEGRAM_APPROVAL_ACTIVE",
        "RUNTIME_AUTHORIZATION_PRESENT",
        "RUNTIME_AUTHORIZATION_ID",
        "RUNTIME_RELEASE_MAIN",
        "RUNTIME_EXPIRES_AT",
        "RUNTIME_EXPIRED",
        "RUNTIME_CONTINUOUS_SESSION",
        "TELEGRAM_CURRENT_RUN_PRESENT",
        "RESULT_INTEGRITY_FAULT_PRESENT",
        "LIVE_PUBLICATION_COUNT",
        "LIVE_RESULT_COUNT",
        "LIVE_SETTLEMENT_COUNT",
        "UNRESOLVED_RESULT_COUNT",
        "UNRESOLVED_SETTLEMENT_COUNT",
        "EXECUTOR_RECEIVER_ACTIVE",
        "EXECUTOR_KILL_SWITCH_ENGAGED",
        "LIVE_ATTEMPT_COUNT",
        "EXECUTION_RESULT_COUNT",
        "PENDING_CANCELLATION_COUNT",
        "PENDING_RECOVERY_RESULT_PUBLISH_COUNT",
        "APPROVAL_DECISION_CLAIM_COUNT",
        "APPROVAL_DECISION_RESULT_COUNT",
        "OFFICIAL_ACCOUNT_HEALTH",
        "GEO_BLOCKED",
        "GEO_COUNTRY",
        "OFFICIAL_OPEN_ORDERS",
        "OFFICIAL_COLLATERAL_USD",
        "OFFICIAL_ACCOUNT_CLEAN",
        "MUTATIONS_PERFORMED=false",
        "REAL_ORDER_SUBMITTED=false",
    ):
        assert marker in text

    for forbidden in (
        "systemctl start ",
        "systemctl stop ",
        "systemctl restart ",
        "systemctl enable ",
        "systemctl disable ",
        "gcloud pubsub topics create",
        "gcloud pubsub topics delete",
        "gcloud pubsub subscriptions create",
        "gcloud pubsub subscriptions delete",
        "add-iam-policy-binding",
        "gcloud compute scp",
        "install -o ",
        "rm -f /var/lib/bp-canary/fast-live/KILL",
        "rm -f /etc/bp-fast-live",
        "post_order",
    ):
        assert forbidden not in text
