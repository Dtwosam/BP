from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_telegram_transport_install_preflight_cloudshell.sh"
)


def test_transport_install_preflight_shell_syntax_is_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(PREFLIGHT)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_transport_install_preflight_verifies_local_release_before_cloud_contact() -> None:
    text = PREFLIGHT.read_text(encoding="utf-8")
    release_verify = text.index(
        "phase15_v3_telegram_transport_verify_release.py"
    )
    source_truth = text.index('"second_order_authorized"')
    local_safe = text.index('fail "local_preflight_not_safe"')
    first_gcloud = text.index("command -v gcloud")
    assert release_verify < source_truth < local_safe < first_gcloud

    for marker in (
        "--expected-commit-sha",
        'assert source["live_trading_enabled"] is False',
        'assert source["phase15_live_trading_enabled"] is False',
        'assert source["canary_order_submitted"] is True',
        'assert source["second_order_authorized"] is True',
        'assert source["automated_real_money_submission"] is True',
        'assert source["manual_real_money_submission_required"] is False',
        'assert source["telegram_one_tap_submission_authorized"] is True',
        (
            'assert source["telegram_persistent_execution_transport_authorized"] '
            "is True"
        ),
        'assert source["telegram_pubsub_transport_authorized"] is True',
    ):
        assert marker in text


def test_transport_install_preflight_checks_safe_idle_and_clean_install_shape() -> None:
    text = PREFLIGHT.read_text(encoding="utf-8")
    for marker in (
        "executor_vm_name_mismatch",
        "executor_vm_zone_mismatch",
        "executor_service_account_count_not_one",
        "executor_cloud_platform_scope_missing",
        'activation_blockers.append("executor_cloud_platform_scope_missing")',
        '"activation_ready": not blockers and not activation_blockers',
        '"activation_blockers": activation_blockers',
        "existing_transport_unit_active",
        "existing_transport_unit_enabled",
        "existing_transport_root_present",
        "existing_transport_config_present",
        "executor_script_missing",
        "kill_switch_missing",
        "executor_health_not_ok",
        "kill_switch_not_engaged",
        "unexpected_active_authorization",
        "unexpected_submission_ready",
        "executor_reports_live_order_submitted",
        "executor_geoblock_not_eligible",
        "TELEGRAM_TRANSPORT_INSTALL_PREFLIGHT_READY=",
        "NO_MUTATION_PERFORMED=true",
    ):
        assert marker in text


def test_transport_install_preflight_is_read_only() -> None:
    text = PREFLIGHT.read_text(encoding="utf-8")
    for forbidden in (
        "gcloud compute scp",
        "gcloud pubsub topics create",
        "gcloud pubsub subscriptions create",
        "add-iam-policy-binding",
        "set-iam-policy",
        "systemctl start",
        "systemctl restart",
        "systemctl enable",
        "systemctl stop",
        "useradd",
        "groupadd",
        "install -",
        "mkdir -",
        "cp ",
        "mv ",
        "rm -",
        "tar -x",
        "pip install",
        "apt-get",
        "post_order",
        "create_limit_order",
        "cancel_order",
        "PHASE15_ACCEPT_REAL_MONEY",
        "POLYMARKET_PRIVATE_KEY=",
        "POLYMARKET_WALLET_ADDRESS=",
    ):
        assert forbidden not in text


def test_transport_install_preflight_embedded_python_is_syntax_valid() -> None:
    text = PREFLIGHT.read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PY'[^\n]*\n(.*?)\nPY(?:\n|$)", text, flags=re.DOTALL)
    assert len(blocks) >= 4
    for block in blocks:
        ast.parse(block)


def test_transport_install_preflight_health_action_is_read_only() -> None:
    text = PREFLIGHT.read_text(encoding="utf-8")
    assert """printf '%s' '{"action":"health"}'""" in text
    assert "--command='sudo /opt/bp-canary/executor.sh'" in text
    assert '"mutation_performed": False' in text
    assert '"release_copied": False' in text
    assert '"iam_changed": False' in text
    assert '"service_installed": False' in text
    assert '"service_enabled": False' in text
    assert '"service_started": False' in text
    assert '"real_order_submitted": False' in text


def test_transport_install_preflight_keeps_cloud_scope_for_activation_only() -> None:
    text = PREFLIGHT.read_text(encoding="utf-8")
    assert 'activation_blockers.append("executor_cloud_platform_scope_missing")' in text
    assert (
        re.search(
            r'(?m)^\\s*blockers\\.append\\("executor_cloud_platform_scope_missing"\\)$',
            text,
        )
        is None
    )
    assert '"activation_ready": not blockers and not activation_blockers' in text
