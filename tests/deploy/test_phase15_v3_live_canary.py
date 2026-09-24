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
TIMING_LATENCY_ROLLOUT = ROOT / "scripts/deploy/phase15_v3_timing_latency_rollout_cloudshell.sh"
RECONCILE_UNSUBMITTED = (
    ROOT / "scripts/deploy/phase15_v3_canary_reconcile_unsubmitted_cloudshell.sh"
)
PERSISTENT_PREPARE_RUNNER = ROOT / "scripts/run_phase15_v3_canary_prepare_watch.py"
PERSISTENT_PREPARE_START = (
    ROOT / "scripts/deploy/phase15_v3_canary_prepare_watch_start_cloudshell.sh"
)
PERSISTENT_PREPARE_STATUS = (
    ROOT / "scripts/deploy/phase15_v3_canary_prepare_watch_status_cloudshell.sh"
)
PERSISTENT_PREPARE_FOLLOW = (
    ROOT / "scripts/deploy/phase15_v3_canary_prepare_watch_follow_cloudshell.sh"
)
PERSISTENT_PREPARE_UNIT = ROOT / "deploy/bp-phase15-canary-prepare-watch.service"


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


def test_prepare_armability_guard_runs_before_intent_persistence() -> None:
    text = (ROOT / "src/bp_engine/execution/canary.py").read_text(encoding="utf-8")
    guard = text.index(
        "if arm_window_seconds < CANARY_MIN_PREPARE_ARM_WINDOW_SECONDS:"
    )
    persist = text.index("intent_store = repository.store_order_intent(")
    assert guard < persist
    assert '"reason": "insufficient_arm_window"' in text


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


def test_timing_latency_rollout_is_paper_only_fail_closed_and_reversible() -> None:
    text = TIMING_LATENCY_ROLLOUT.read_text(encoding="utf-8")
    for marker in (
        "PHASE15_ACCEPT_V3_TIMING_LATENCY_ROLLOUT",
        "authorized_timing_commit_not_in_current_main",
        "timing_runtime_binding_changed_after_authorized_commit",
        "EXPECTED_OLD_RUNTIME",
        "v3-paper-timing-",
        "runtime_diff_not_single_file",
        "bp-v3-paper-execution.service",
        "report_count >= 6",
        "median_gap > 6",
        "max_gap > 10",
        "ROLLBACK=restoring_previous_v3_runtime",
        "RECORDER_PID_PRESERVED=true",
        "PREDICTOR_PID_PRESERVED=true",
        "LIVE_TRADING_ENABLED=false",
        "MAX_TRADE_SIZE_USD=0",
        "MAX_DAILY_LOSS_USD=0",
        "PHASE15_V3_TIMING_LATENCY_ROLLOUT=PASS",
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


def test_timing_latency_rollout_embedded_python_and_shell_are_syntax_valid() -> None:
    text = TIMING_LATENCY_ROLLOUT.read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PY'[^\n]*\n(.*?)\nPY(?:\n|$)", text, flags=re.DOTALL)
    assert blocks
    for block in blocks:
        ast.parse(block)
    subprocess.run(
        ["bash", "-n", str(TIMING_LATENCY_ROLLOUT)],
        check=True,
        capture_output=True,
        text=True,
    )


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


def test_persistent_prepare_runner_is_prepare_only_and_bounded() -> None:
    text = PERSISTENT_PREPARE_RUNNER.read_text(encoding="utf-8")
    for marker in (
        "prepare_next_canary",
        'prepared_payload["action"] = "submit"',
        "max wait must be within 1..7200 seconds",
        '"real_order_submitted": False',
        '"arm_attempted": False',
        '"submission_attempt_consumed": False',
        '"failed_after_intent_persisted"',
        '"requires_reconciliation": True',
        "wallet material must not be present on prepare watcher",
        'parser.add_argument("--poll-seconds", type=float, default=0.5)',
        '"timing": normalized.get("timing")',
    ):
        assert marker in text
    for forbidden in (
        "post_order",
        "create_limit_order",
        "cancel_order",
        "gcloud",
        "PHASE15_ACCEPT_REAL_MONEY",
    ):
        assert forbidden not in text
    ast.parse(text)


def test_phase15_canary_prepared_report_exposes_latency_diagnostics() -> None:
    text = (ROOT / "src/bp_engine/execution/canary.py").read_text(encoding="utf-8")
    for marker in (
        '"prediction_scheduled_at"',
        '"prediction_recorded_at"',
        '"paper_order_created_at"',
        '"prepared_observed_at"',
        '"prediction_lateness_seconds"',
        '"paper_order_after_prediction_seconds"',
        '"prepare_after_paper_order_seconds"',
        '"seconds_to_market_end_at_prepare"',
    ):
        assert marker in text


def test_persistent_prepare_unit_is_research_zero_money_localhost_only() -> None:
    text = PERSISTENT_PREPARE_UNIT.read_text(encoding="utf-8")
    for marker in (
        "User=bp",
        "Group=bp",
        "Environment=MODE=research",
        "Environment=LIVE_TRADING_ENABLED=false",
        "Environment=MAX_TRADE_SIZE_USD=0",
        "Environment=MAX_DAILY_LOSS_USD=0",
        "RuntimeMaxSec=2h5min",
        "NoNewPrivileges=true",
        "ProtectSystem=full",
        "ReadWritePaths=/var/lib/bp/phase15-canary-prepare-watch",
        "IPAddressDeny=any",
        "IPAddressAllow=localhost",
    ):
        assert marker in text
    assert "ExecStart=" in text
    assert "phase15_v3_canary_arm" not in text
    assert "phase15_v3_canary_executor" not in text


def test_persistent_prepare_start_requires_explicit_scope_and_health_only() -> None:
    text = PERSISTENT_PREPARE_START.read_text(encoding="utf-8")
    for marker in (
        "PHASE15_ACCEPT_PERSISTENT_PREPARE_WATCH",
        "explicit_persistent_prepare_authorization_required",
        '{"action":"health"}',
        "persistent_prepare_watch_already_active",
        "bp-phase15-canary-prepare-watch.service",
        "MAX_WAIT_SECONDS >= 1 && MAX_WAIT_SECONDS <= 7200",
        "NO_REAL_ORDER_SUBMITTED=true",
        "ARM_AUTOMATED=false",
        "SUBMISSION_AUTOMATED=false",
        'POLL_SECONDS="${PHASE15_CANARY_POLL_SECONDS:-0.5}"',
    ):
        assert marker in text
    assert "deploy/bp-phase15-canary-prepare-watch.service" in text
    assert "deploy/bp-phase15-v3-canary-prepare-watch.service" not in text
    assert PERSISTENT_PREPARE_UNIT.exists()
    for forbidden in (
        '{"action":"submit"}',
        "post_order",
        "create_limit_order",
        "PHASE15_ACCEPT_REAL_MONEY",
        "systemctl enable",
    ):
        assert forbidden not in text


def test_persistent_prepare_status_materializes_only_fresh_current_payload() -> None:
    text = PERSISTENT_PREPARE_STATUS.read_text(encoding="utf-8")
    for marker in (
        "watcher_prepare_or_execution_binding_changed",
        'git diff --quiet "$HELPER_HEAD" "$LOCAL_HEAD"',
        "src/bp_engine/execution/live.py",
        "src/bp_engine/execution/canary.py",
        "scripts/run_phase15_v3_canary_prepare_watch.py",
        "deploy/bp-phase15-canary-prepare-watch.service",
        "scripts/deploy/phase15_v3_canary_arm_cloudshell.sh",
        "scripts/deploy/phase15_v3_canary_executor.py",
        "float(sys.argv[1]) >= 20",
        "REQUIRES_CLOSED_BEFORE_SUBMISSION_RECONCILIATION=true",
        "prepared_payload_binding_invalid",
        'assert payload["action"] == "submit"',
        "ARMABLE_NOW=true",
        "NO_REAL_ORDER_SUBMITTED=true",
    ):
        assert marker in text
    assert "bash scripts/deploy/phase15_v3_canary_arm_cloudshell.sh" not in text
    assert "exec scripts/deploy/phase15_v3_canary_arm_cloudshell.sh" not in text
    for forbidden in (
        "PHASE15_ACCEPT_REAL_MONEY",
        "post_order",
        "create_limit_order",
    ):
        assert forbidden not in text


def test_persistent_prepare_follow_only_observes_then_delegates_to_status() -> None:
    text = PERSISTENT_PREPARE_FOLLOW.read_text(encoding="utf-8")
    assert "phase15_v3_canary_prepare_watch_status_cloudshell.sh" in text
    assert "systemctl is-active --quiet bp-phase15-canary-prepare-watch.service" in text
    for forbidden in (
        "PHASE15_ACCEPT_REAL_MONEY",
        "post_order",
        "create_limit_order",
        "phase15_v3_canary_arm_cloudshell.sh",
    ):
        assert forbidden not in text


def test_persistent_prepare_shell_helpers_have_valid_syntax() -> None:
    for path in (
        PERSISTENT_PREPARE_START,
        PERSISTENT_PREPARE_STATUS,
        PERSISTENT_PREPARE_FOLLOW,
    ):
        subprocess.run(
            ["bash", "-n", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
