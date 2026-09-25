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
    / "phase15_v3_telegram_transport_publisher_install_preflight_cloudshell.sh"
)


def test_publisher_install_preflight_shell_syntax_is_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(PREFLIGHT)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_publisher_install_preflight_verifies_release_and_safe_source_first() -> None:
    text = PREFLIGHT.read_text(encoding="utf-8")
    release_verify = text.index(
        "phase15_v3_telegram_transport_verify_release.py"
    )
    source_truth = text.index('"wallet_material_allowed_on_us_host"')
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
        'assert source["wallet_material_allowed_on_us_host"] is False',
        'assert source["telegram_one_tap_submission_authorized"] is True',
        (
            'assert source["telegram_persistent_execution_transport_authorized"] '
            "is True"
        ),
        'assert source["telegram_pubsub_transport_authorized"] is True',
    ):
        assert marker in text


def test_publisher_install_preflight_checks_clean_recorder_install_surface() -> None:
    text = PREFLIGHT.read_text(encoding="utf-8")
    for marker in (
        "recorder_vm_name_mismatch",
        "recorder_vm_zone_mismatch",
        "publisher_service_account_count_not_one",
        "publisher_cloud_platform_scope_missing",
        "bp_user_missing",
        "core_service_not_active",
        "core_service_pid_invalid",
        "existing_publisher_unit_active",
        "existing_publisher_unit_enabled",
        "existing_transport_root_present",
        "existing_transport_config_present",
        "existing_publisher_env_present",
        "existing_transport_state_present",
        "wallet_material_path_present_on_recorder",
        "TELEGRAM_TRANSPORT_PUBLISHER_INSTALL_PREFLIGHT_READY=",
        "NO_MUTATION_PERFORMED=true",
    ):
        assert marker in text


def test_publisher_install_preflight_is_read_only() -> None:
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


def test_publisher_install_preflight_embedded_python_is_syntax_valid() -> None:
    text = PREFLIGHT.read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PY'[^\n]*\n(.*?)\nPY(?:\n|$)", text, flags=re.DOTALL)
    assert len(blocks) >= 4
    for block in blocks:
        ast.parse(block)


def test_publisher_install_preflight_reports_no_mutation_contract() -> None:
    text = PREFLIGHT.read_text(encoding="utf-8")
    for marker in (
        '"mutation_performed": False',
        '"release_copied": False',
        '"iam_changed": False',
        '"service_installed": False',
        '"service_enabled": False',
        '"service_started": False',
        '"real_order_submitted": False',
    ):
        assert marker in text
