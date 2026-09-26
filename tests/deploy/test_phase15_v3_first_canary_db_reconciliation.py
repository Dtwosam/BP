from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = (
    ROOT
    / "scripts/deploy/phase15_v3_first_canary_db_reconciliation_cloudshell.sh"
)


def test_first_canary_db_reconciliation_is_explicitly_authorized_and_bound() -> None:
    text = HELPER.read_text(encoding="utf-8")
    for marker in (
        "PHASE15_ACCEPT_FIRST_CANARY_DB_RECONCILIATION",
        'repair["status"] == "AUTHORIZED_READY"',
        'repair["authorized"] is True',
        'repair["authorization_consumed"] is False',
        'repair["helper_git_blob_sha"] == actual_blob',
        "post_submission_official_zero_fill",
        "store_reconciliation_run",
        "unresolved_count=0",
        "critical_count=0",
        '"total_exposure_usd": "0"',
        "NETWORK_SUBMISSION_ATTEMPT_CONSUMED_BY_REPAIR=false",
        "TELEGRAM_APPROVAL_PERFORMED=false",
        "EXECUTOR_ARMED=false",
        "EXECUTOR_INVOKED=false",
        "ORDER_SUBMISSION_PERFORMED=false",
        "PHASE15_V3_FIRST_CANARY_DB_RECONCILIATION=PASS",
    ):
        assert marker in text


def test_first_canary_db_reconciliation_freshly_verifies_zero_fill() -> None:
    text = HELPER.read_text(encoding="utf-8")
    for marker in (
        "list_open_orders",
        "list_account_trades",
        "snapshot_stable_across_3_seconds",
        'payload["fill_state"] == "zero_fill_observed"',
        'payload["official_reconciliation_complete"] is True',
        'payload["open_order_count"] == 0',
        'payload["matching_trade_count"] == 0',
        'payload["kill_switch_engaged"] is True',
        'payload["submission_ready"] is False',
        'payload["live_order_submitted"] is False',
    ):
        assert marker in text
    for forbidden in (
        "post_order(",
        "create_limit_order(",
        "cancel_order(",
        "PHASE15_ACCEPT_REAL_MONEY",
    ):
        assert forbidden not in text


def test_first_canary_db_reconciliation_shell_and_python_are_syntax_valid() -> None:
    text = HELPER.read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PY'[^\n]*\n(.*?)\nPY(?:\n|$)", text, flags=re.DOTALL)
    assert len(blocks) >= 5
    for block in blocks:
        ast.parse(block)
    subprocess.run(
        ["bash", "-n", str(HELPER)],
        check=True,
        capture_output=True,
        text=True,
    )
