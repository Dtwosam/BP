from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXECUTOR = ROOT / "scripts/deploy/phase15_v3_canary_executor.py"
BOOTSTRAP = ROOT / "scripts/deploy/phase15_v3_canary_bootstrap_cloudshell.sh"
PREPARE = ROOT / "scripts/deploy/phase15_v3_canary_prepare_cloudshell.sh"
RECORD = ROOT / "scripts/deploy/phase15_v3_canary_record_cloudshell.sh"
HOTPATH_ROLLOUT = ROOT / "scripts/deploy/phase15_v3_paper_hotpath_rollout_cloudshell.sh"
RECONCILE_UNSUBMITTED = (
    ROOT / "scripts/deploy/phase15_v3_canary_reconcile_unsubmitted_cloudshell.sh"
)


def test_executor_is_ten_dollar_geoblock_checked_and_ttl_bounded() -> None:
    text = EXECUTOR.read_text(encoding="utf-8")
    for marker in (
        'MAX_NOTIONAL_USD = Decimal("10")',
        "TTL_SECONDS = 2",
        "https://polymarket.com/api/geoblock",
        'if geo["blocked"] is not False',
        "canary_notional_limit_exceeded",
        "create_limit_order",
        "post_order",
        "cancel_order",
        "get_balance_allowance",
        "list_open_orders",
        "request_sha256_mismatch",
        "activation_executor_sha256_mismatch",
        "official_open_orders_present",
        "insufficient_official_collateral",
    ):
        assert marker in text
    assert "print(private_key)" not in text
    assert "print(wallet)" not in text


def test_bootstrap_never_submits_an_order() -> None:
    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert "PHASE15_ACCEPT_WALLET_SETUP" in text
    assert "input hidden; never sent to ChatGPT" in text
    assert "polymarket-client==0.7.1" in text
    assert "TRADING_ORDER_SUBMITTED=false" in text
    assert '{"action":"health"}' in text
    assert '"User-Agent": "BP-phase15-geoblock-probe/1"' in text
    assert '{"action":"submit"}' not in text


def test_prepare_is_manual_review_only_and_writes_no_real_order() -> None:
    text = PREPARE.read_text(encoding="utf-8")
    assert "prepare_next_canary" in text
    assert "OFFICIAL_OPEN_ORDER_COUNT" in text
    assert "COLLATERAL_BALANCE_USD" in text
    assert "executor_sha256" in text
    assert "NO_REAL_ORDER_SUBMITTED=true" in text
    assert "PHASE15_V3_CANARY_PREPARE=PASS" in text
    assert "phase15_v3_canary_executor.py" in text
    assert "hashlib.sha256" in text
    assert '{"action":"submit"}' not in text
    assert "post_order" not in text
    assert "PHASE15_ACCEPT_REAL_MONEY" not in text
    assert "insufficient_arm_window" in (
        ROOT / "src/bp_engine/execution/canary.py"
    ).read_text(encoding="utf-8")
    assert 'CANARY_MIN_PREPARE_ARM_WINDOW_SECONDS = Decimal("30")' in (
        ROOT / "src/bp_engine/execution/canary.py"
    ).read_text(encoding="utf-8")


def test_record_helper_only_persists_executor_result() -> None:
    text = RECORD.read_text(encoding="utf-8")
    assert "record_canary_submission" in text
    assert "CANARY_RESULT_B64" in text
    assert "post_order" not in text
    assert "create_limit_order" not in text


def test_prepare_binds_current_live_module_before_current_canary_module() -> None:
    text = PREPARE.read_text(encoding="utf-8")
    assert 'LIVE_SOURCE_B64=$(base64 -w0 "$ROOT/src/bp_engine/execution/live.py")' in text
    assert "LIVE_SOURCE_B64='$LIVE_SOURCE_B64'" in text
    assert "import bp_engine.execution as execution_package" in text
    assert 'types.ModuleType("bp_engine.execution.live")' in text
    assert 'sys.modules[live_module.__name__]=live_module' in text
    assert "execution_package.live=live_module" in text
    assert '<phase15_live_inline>' in text
    assert 'InterlockDecision=live_module.InterlockDecision' in text

    package_import = text.index("import bp_engine.execution as execution_package")
    live_swap = text.index('sys.modules[live_module.__name__]=live_module')
    live_exec = text.index(
        'exec(compile(live_source, "<phase15_live_inline>", "exec"), live_module.__dict__)'
    )
    canary_exec = text.index(
        'exec(compile(source, "<phase15_canary_inline>", "exec"), module.__dict__)'
    )
    assert package_import < live_swap < live_exec < canary_exec
    assert "from bp_engine.execution.live import InterlockDecision" not in text


def test_prepare_live_module_swap_executes_without_package_import_cycle() -> None:
    script = """
import sys
import types
from pathlib import Path

import bp_engine.execution as execution_package

source = Path("src/bp_engine/execution/live.py").read_text(encoding="utf-8")
live_module = types.ModuleType("bp_engine.execution.live")
live_module.__package__ = "bp_engine.execution"
sys.modules[live_module.__name__] = live_module
execution_package.live = live_module
exec(compile(source, "<phase15_live_inline_test>", "exec"), live_module.__dict__)
assert live_module.InterlockDecision.__name__ == "InterlockDecision"
assert live_module._account_snapshot.__name__ == "_account_snapshot"
"""
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_arm_binds_exact_prepared_request_and_executor() -> None:
    text = (ROOT / "scripts/deploy/phase15_v3_canary_arm_cloudshell.sh").read_text(
        encoding="utf-8"
    )
    for marker in (
        "REQUEST_SHA256",
        "EXECUTOR_SHA256",
        '"intent_id": sys.argv[7]',
        '"prediction_id": sys.argv[8]',
        '"paper_order_id": sys.argv[9]',
        '"request_sha256": sys.argv[10]',
        '"max_submission_attempts": 1',
        "PHASE15_ACCEPT_REAL_MONEY",
    ):
        assert marker in text


def test_record_requires_executor_result_binding() -> None:
    text = RECORD.read_text(encoding="utf-8")
    for marker in (
        "executor_result_binding_invalid",
        "request_sha256",
        "executor_sha256",
        "authorization_id",
        "account_preflight",
    ):
        assert marker in text


def test_hotpath_rollout_is_paper_only_fail_closed_and_steady_state_validated() -> None:
    text = HOTPATH_ROLLOUT.read_text(encoding="utf-8")
    for marker in (
        "PHASE15_ACCEPT_V3_PAPER_HOTPATH_ROLLOUT",
        "authorized_fix_not_in_current_main",
        "execution_service_changed_after_authorized_fix",
        "EXPECTED_OLD_RUNTIME",
        "runtime_diff_not_single_file",
        "bp-v3-paper-execution.service",
        "report_count >= 5",
        "median_gap > 10",
        "max_gap > 15",
        "ROLLBACK=restoring_previous_v3_runtime",
        "RECORDER_PID_PRESERVED=true",
        "PREDICTOR_PID_PRESERVED=true",
        "LIVE_TRADING_ENABLED=false",
        "MAX_TRADE_SIZE_USD=0",
        "MAX_DAILY_LOSS_USD=0",
        "PHASE15_V3_PAPER_HOTPATH_ROLLOUT=PASS",
    ):
        assert marker in text
    for forbidden in (
        "phase15_v3_canary_executor.py",
        "POLYMARKET_PRIVATE_KEY",
        "post_order",
        "create_limit_order",
        "PHASE15_ACCEPT_REAL_MONEY",
    ):
        assert forbidden not in text


def test_hotpath_rollout_embedded_python_is_syntax_valid() -> None:
    text = HOTPATH_ROLLOUT.read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PY'\n(.*?)\nPY(?:\n|$)", text, flags=re.DOTALL)
    assert blocks
    for block in blocks:
        ast.parse(block)


def test_unsubmitted_reconciliation_is_fail_closed_and_never_submits() -> None:
    text = RECONCILE_UNSUBMITTED.read_text(encoding="utf-8")
    for marker in (
        "PHASE15_ACCEPT_UNSUBMITTED_RECONCILIATION",
        "prepared_intent_still_armable_or_invalid",
        '"action":"health"',
        'payload["kill_switch_engaged"] is True',
        'payload["activation_valid"] is False',
        'payload["submission_ready"] is False',
        'payload["live_order_submitted"] is False',
        "reconcile_unsubmitted_canary_intent",
        "closed_before_submission",
        "SUBMISSION_ATTEMPT_CONSUMED=false",
        "PHASE15_V3_CANARY_RECONCILE_UNSUBMITTED=PASS",
    ):
        assert marker in text
    for forbidden in (
        "post_order",
        "create_limit_order",
        '{"action":"submit"}',
        "PHASE15_ACCEPT_REAL_MONEY",
        "POLYMARKET_PRIVATE_KEY",
    ):
        assert forbidden not in text


def test_unsubmitted_reconciliation_embedded_python_is_syntax_valid() -> None:
    text = RECONCILE_UNSUBMITTED.read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PY'[^\n]*\n(.*?)\nPY(?:\n|$)", text, flags=re.DOTALL)
    assert len(blocks) >= 6
    for block in blocks:
        ast.parse(block)


def test_unsubmitted_reconciliation_shell_syntax_is_valid() -> None:
    subprocess.run(
        ["bash", "-n", str(RECONCILE_UNSUBMITTED)],
        check=True,
        capture_output=True,
        text=True,
    )
