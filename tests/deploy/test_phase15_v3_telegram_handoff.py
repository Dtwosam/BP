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


def test_telegram_handoff_is_source_truth_and_exact_approval_gated() -> None:
    text = HANDOFF.read_text(encoding="utf-8")
    assert 'PHASE15_ACCEPT_TELEGRAM_REAL_MONEY:-no' in text
    assert 'gate.get("telegram_one_tap_submission_authorized") is True' in text
    assert text.count("validate_approved_handoff") >= 2
    assert 'PHASE15_ACCEPT_REAL_MONEY=yes' in text
    assert 'phase15_v3_canary_arm_cloudshell.sh' in text
    assert "sudo /opt/bp-canary/executor.sh" in text
    assert 'phase15_v3_canary_record_cloudshell.sh' in text
    assert "DO_NOT_RETRY=true" in text


def test_current_source_truth_does_not_enable_telegram_real_money_handoff() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    assert gate.get("telegram_one_tap_submission_authorized") is not True
    assert gate["canary_order_submitted"] is True
    assert gate["second_order_authorized"] is False
    assert state["live_trading_enabled"] is False


def test_submission_attempt_marker_precedes_executor_network_call() -> None:
    text = HANDOFF.read_text(encoding="utf-8")
    marker = text.index('SUBMISSION_MARKER="$STATE_DIR/submission-attempt.json"')
    executor = text.index("--command='sudo /opt/bp-canary/executor.sh'")
    assert marker < executor


def test_systemd_service_does_not_configure_handoff_by_default() -> None:
    text = SERVICE.read_text(encoding="utf-8")
    assert "BP_TELEGRAM_HANDOFF_COMMAND=" not in text
