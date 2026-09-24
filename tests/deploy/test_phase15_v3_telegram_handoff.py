from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HANDOFF = ROOT / "scripts" / "deploy" / "phase15_v3_canary_telegram_handoff.sh"
SERVICE = ROOT / "deploy" / "bp-phase15-canary-telegram-approval.service"
STATE = ROOT / "PROJECT_STATE.json"


def test_telegram_handoff_shell_syntax_is_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(HANDOFF)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_telegram_handoff_requires_full_source_truth_and_dispatch_claim() -> None:
    text = HANDOFF.read_text(encoding="utf-8")
    for marker in (
        "PHASE15_TELEGRAM_DISPATCH_CLAIM_FILE",
        "dispatch_claim_file_not_configured",
        'PHASE15_ACCEPT_TELEGRAM_REAL_MONEY:-no',
        'gate.get("second_order_authorized") is True',
        'gate.get("automated_real_money_submission") is True',
        'gate.get("manual_real_money_submission_required") is False',
        'gate.get("telegram_one_tap_submission_authorized") is True',
        'gate.get("telegram_persistent_execution_transport_authorized") is True',
        'gate.get("telegram_pubsub_transport_authorized") is True',
        'claim["status"] == "dispatch_claimed"',
        'claim["source_truth_sha256"] == source_truth_sha256(state)',
        'claim["retry_allowed"] is False',
        'claim["executor_invoked"] is False',
        'claim["real_order_submitted"] is False',
        'assert now < expires_at',
        "DISPATCH_CLAIM_SHA256=",
        "dispatch_claim_changed_after_arm",
        'PHASE15_ACCEPT_REAL_MONEY=yes',
        "phase15_v3_canary_arm_cloudshell.sh",
        "sudo /opt/bp-canary/executor.sh",
        "phase15_v3_canary_record_cloudshell.sh",
        "DO_NOT_RETRY=true",
    ):
        assert marker in text
    assert text.count("validate_approved_handoff") >= 2


def test_current_source_truth_does_not_enable_telegram_real_money_handoff() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    assert state["live_trading_enabled"] is False
    assert gate["live_trading_enabled"] is False
    assert gate["canary_order_submitted"] is True
    assert gate["second_order_authorized"] is False
    assert gate["automated_real_money_submission"] is False
    assert gate["manual_real_money_submission_required"] is True
    assert gate.get("telegram_one_tap_submission_authorized") is not True
    assert gate.get("telegram_persistent_execution_transport_authorized") is not True
    assert gate.get("telegram_pubsub_transport_authorized") is not True


def test_dispatch_claim_and_submission_markers_precede_executor_call() -> None:
    text = HANDOFF.read_text(encoding="utf-8")
    dispatch_gate = text.index(
        'claim["source_truth_sha256"] == source_truth_sha256(state)'
    )
    arm = text.index('bash "$ARM_HELPER"')
    dispatch_unchanged = text.index("dispatch_claim_changed_after_arm")
    submission_marker = text.index(
        'SUBMISSION_MARKER="$STATE_DIR/submission-attempt.json"'
    )
    executor = text.index("--command='sudo /opt/bp-canary/executor.sh'")
    assert dispatch_gate < arm < dispatch_unchanged < submission_marker < executor


def test_systemd_service_does_not_configure_handoff_by_default() -> None:
    text = SERVICE.read_text(encoding="utf-8")
    assert "BP_TELEGRAM_HANDOFF_COMMAND=" not in text
    assert "PHASE15_TELEGRAM_DISPATCH_CLAIM_FILE=" not in text


def test_dispatch_claim_is_revalidated_again_after_arm() -> None:
    text = HANDOFF.read_text(encoding="utf-8")
    assert text.count('"DISPATCH_CLAIM_FILE"') >= 3
    assert text.count('claim["expires_at"]') >= 2
    assert text.count('claim["status"] == "dispatch_claimed"') >= 2
    assert text.count('claim["retry_allowed"] is False') >= 2
    assert text.count('claim["executor_invoked"] is False') >= 2
    assert text.count('claim["real_order_submitted"] is False') >= 2
