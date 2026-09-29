from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_fast_live_retire_legacy_transport_cloudshell.sh"
)


def test_legacy_retirement_helper_is_shell_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_legacy_retirement_is_bound_to_terminal_reconciled_second_canary() -> None:
    text = HELPER.read_text(encoding="utf-8")

    for marker in (
        "I_ACCEPT_RETIRE_RECONCILED_LEGACY_TELEGRAM_TRANSPORT_FOR_FAST_LIVE",
        'auth["consumed"] is True',
        'auth["network_submission_attempt_consumed"] is True',
        'auth["authorization_slot_consumed"] is True',
        'auth["retry_authorized"] is False',
        'auth["third_order_authorized"] is False',
        'canary["status"] == "RECONCILED_ZERO_FILL"',
        'canary["official_reconciliation_complete"] is True',
        'canary["confirmed_filled_shares"] == 0',
        'canary["open_order_count"] == 0',
        'canary["exposure_usd"] == 0',
        'recon["status"] == "COMPLETED_PASS"',
        'recon["production_db_reconciliation_unresolved_count"] == 0',
        'recon["production_db_reconciliation_critical_count"] == 0',
        'fast["authorization_mode"] == "auto-telegram-continuous-v1"',
        'fast["activation_performed"] is False',
        "phase-15-v3-second-canary-submission-zero-fill-readonly-20260928.json",
        "phase-15-v3-second-canary-zero-fill-completion-pass-production-20260928.json",
        'payload["status"] == "second_canary_network_attempt_starting"',
        'result["status"] == "executor_result_recorded"',
        'result["network_submission_attempt_consumed"] is True',
        'result["real_order_submitted"] is True',
        '(result.get("cancellation") or {}).get("status") == "cancelled"',
        "legacy_recorder_delivery_pending",
        "legacy_executor_delivery_pending",
    ):
        assert marker in text


def test_legacy_retirement_preserves_fail_closed_live_boundaries() -> None:
    text = HELPER.read_text(encoding="utf-8")

    for marker in (
        "/etc/bp-canary/KILL",
        "/var/lib/bp-canary/fast-live/KILL",
        "sudo test ! -e /etc/bp-fast-live/authorization.json",
        "bp-phase15-fast-live-receiver.service",
        "systemctl stop",
        "systemctl disable",
        "/etc/bp/telegram-approval-handoff.env",
        "/etc/bp/telegram-pubsub-publisher.env",
        "/etc/bp-telegram-transport/receiver.env",
        "/etc/bp-telegram-transport/claim.env",
        "/etc/bp-telegram-transport/execution-auth.env",
        "/etc/bp-telegram-transport/privileged-handoff.env",
        "second-canary.attempt.json",
        "os.rename(marker, target)",
        "TELEGRAM_CREDENTIAL_ENV_PRESERVED=true",
        "FAST_LIVE_KILL_SWITCH_ENGAGED=true",
        "FAST_LIVE_RUNTIME_AUTHORIZATION_PRESENT=false",
        "PUBSUB_RESOURCES_CHANGED=false",
        "HISTORICAL_STATE_DELETED=false",
        "NEW_NETWORK_SUBMISSION_ATTEMPT_PERFORMED=false",
        "NEW_REAL_ORDER_SUBMITTED=false",
        "PHASE15_FAST_LIVE_LEGACY_RETIREMENT=PASS",
    ):
        assert marker in text

    assert "rm -f /etc/bp/telegram-approval.env" not in text

    for forbidden in (
        "gcloud pubsub topics delete",
        "gcloud pubsub subscriptions delete",
        "gcloud pubsub topics remove-iam-policy-binding",
        "gcloud pubsub subscriptions remove-iam-policy-binding",
        "create_limit_order(",
        "post_order(",
        "PHASE15_ACCEPT_FAST_LIVE_ACTIVATION",
        "I_ACCEPT_CONTINUOUS_TELEGRAM_APPROVED_LIVE_SESSION",
        "systemctl start bp-phase15-fast-live-receiver.service",
        "rm -rf /var/lib/bp-canary/telegram-live-handoff",
        "rm -rf /var/lib/bp-telegram-transport",
    ):
        assert forbidden not in text


def test_embedded_python_blocks_are_syntax_valid() -> None:
    text = HELPER.read_text(encoding="utf-8")
    blocks = re.findall(
        r"<<'PY'[^\n]*\n(.*?)\nPY(?:\n|$)",
        text,
        flags=re.DOTALL,
    )
    assert len(blocks) >= 6
    for block in blocks:
        ast.parse(block)
