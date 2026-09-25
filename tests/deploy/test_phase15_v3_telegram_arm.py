from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARM = ROOT / "scripts" / "deploy" / "phase15_v3_canary_telegram_arm_cloudshell.sh"


def test_telegram_arm_shell_and_embedded_python_are_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(ARM)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    text = ARM.read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PY'[^\n]*\n(.*?)\nPY(?:\n|$)", text, flags=re.DOTALL)
    assert len(blocks) >= 7
    for block in blocks:
        ast.parse(block)


def test_telegram_arm_requires_second_action_source_truth_and_dispatch_claim() -> None:
    text = ARM.read_text(encoding="utf-8")
    for marker in (
        "PHASE15_ACCEPT_TELEGRAM_REAL_MONEY",
        "telegram_real_money_not_explicitly_accepted",
        "PHASE15_TELEGRAM_APPROVAL_FILE",
        "PHASE15_TELEGRAM_DISPATCH_CLAIM_FILE",
        'state["live_trading_enabled"] is False',
        'gate["live_trading_enabled"] is False',
        'gate["canary_order_submitted"] is True',
        'first.get("official_reconciliation_complete") is True',
        'gate.get("pending_unsubmitted_intent") is None',
        'gate.get("v3_strategy_mutation_performed") is False',
        'gate.get("second_order_authorized") is True',
        'gate.get("automated_real_money_submission") is True',
        'gate.get("manual_real_money_submission_required") is False',
        'gate.get("telegram_one_tap_submission_authorized") is True',
        'gate.get("telegram_persistent_execution_transport_authorized") is True',
        'gate.get("telegram_pubsub_transport_authorized") is True',
        'claim["source_truth_sha256"] == source_truth_sha256(state)',
        'claim["status"] == "dispatch_claimed"',
        'claim["retry_allowed"] is False',
        'claim["executor_invoked"] is False',
        'claim["real_order_submitted"] is False',
        "validate_approved_handoff",
        'claim["prepared_sha256"] == payload_sha256(prepared)',
        'claim["approval_sha256"] == payload_sha256(execution_approval(approval))',
        'claim["approval_source_sha256"] == payload_sha256(approval)',
    ):
        assert marker in text


def test_telegram_arm_activation_is_exact_short_lived_and_claim_capped() -> None:
    text = ARM.read_text(encoding="utf-8")
    for marker in (
        '"executor_sha256"',
        '"authorization_id"',
        '"intent_id"',
        '"prediction_id"',
        '"paper_order_id"',
        '"request_sha256"',
        '"source_prediction_version": "v3-frozen-paper-v1"',
        '"source_execution_version": "paper-execution-v3-frozen-v1"',
        '"max_submission_attempts": 1',
        "now + timedelta(seconds=45)",
        "market_end - timedelta(seconds=10)",
        "claim_expires",
        "telegram dispatch authorization expired before arm",
        "/etc/bp-canary/activation.json",
        "/etc/bp-canary/KILL",
        "activation_valid",
        "submission_ready",
        "REAL_ORDER_SUBMITTED=false",
    ):
        assert marker in text


def test_telegram_arm_does_not_submit_or_record_order() -> None:
    text = ARM.read_text(encoding="utf-8")
    for forbidden in (
        "phase15_v3_canary_record_cloudshell.sh",
        "PHASE15_CANARY_RESULT_FILE",
        "post_order",
        "create_limit_order",
        "cancel_order",
        '< "$PREPARED_FILE"',
        "submission-attempt.json",
        "executor-result.json",
    ):
        assert forbidden not in text

    # The executor wrapper is called only for the explicit health action.
    assert text.count("--command='sudo /opt/bp-canary/executor.sh'") == 1
    assert """printf '%s' '{"action":"health"}'""" in text


def test_telegram_arm_failure_reengages_kill_switch() -> None:
    text = ARM.read_text(encoding="utf-8")
    armed_flag = text.index("ARMED=true")
    arm_mutation = text.index("/etc/bp-canary/activation.json", armed_flag)
    health = text.index('{"action":"health"}', arm_mutation)
    assert armed_flag < arm_mutation < health
    assert "telegram-arm-failure-reengaged" in text
