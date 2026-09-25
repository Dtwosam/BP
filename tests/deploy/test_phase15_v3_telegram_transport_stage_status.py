from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATUS = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_telegram_transport_stage_status_cloudshell.sh"
)


def test_transport_stage_status_shell_and_embedded_python_are_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(STATUS)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    text = STATUS.read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PY'[^\n]*\n(.*?)\nPY(?:\n|$)", text, flags=re.DOTALL)
    assert len(blocks) >= 4
    for block in blocks:
        ast.parse(block)


def test_transport_stage_status_binds_both_hosts_to_one_stage_and_release() -> None:
    text = STATUS.read_text(encoding="utf-8")
    for marker in (
        "local_main_not_current",
        "STAGE-METADATA.json",
        "phase15-canary-telegram-transport-stage-owner.json",
        "telegram-transport-stage-owner.json",
        "stage_id_mismatch",
        "archive_sha256_mismatch",
        "release_head_not_current",
        "publisher_unit_hash_mismatch",
        "approved_outbox_handoff_invalid",
        "transport_unit_hash_mismatch",
        '"httpx": "0.28.1"',
        '"google-cloud-pubsub": "2.41.0"',
        "publisher_runtime_versions_mismatch",
        "executor_runtime_versions_mismatch",
    ):
        assert marker in text


def test_transport_stage_status_requires_inactive_secret_free_stage() -> None:
    text = STATUS.read_text(encoding="utf-8")
    for marker in (
        "publisher_env_present",
        "publisher_transport_key_present",
        "publisher_service_active",
        "publisher_service_enabled",
        "executor_receiver_env_present",
        "executor_claim_env_present",
        "executor_execution_auth_env_present",
        "executor_privileged_handoff_env_present",
        "executor_transport_key_present",
        "executor_origin_key_present",
        "bp-phase15-telegram-privileged-handoff.service",
        "/var/lib/bp-canary/telegram-live-handoff",
        "transport_service_active",
        "transport_service_enabled",
        "environment_files_present",
        "key_files_present",
    ):
        assert marker in text


def test_transport_stage_status_rechecks_source_truth_and_executor_safe_idle() -> None:
    text = STATUS.read_text(encoding="utf-8")
    for marker in (
        '"second_order_authorized"',
        '"automated_real_money_submission"',
        '"manual_real_money_submission_required"',
        '"telegram_one_tap_submission_authorized"',
        '"telegram_persistent_execution_transport_authorized"',
        '"telegram_pubsub_transport_authorized"',
        'printf \'%s\' \'{"action":"health"}\'',
        "executor_health_not_ok",
        "kill_switch_not_engaged",
        "unexpected_active_authorization",
        "unexpected_submission_ready",
        "executor_reports_live_order_submitted",
        "executor_geoblock_not_eligible",
    ):
        assert marker in text


def test_transport_stage_status_is_strictly_read_only() -> None:
    text = STATUS.read_text(encoding="utf-8")
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
        "systemctl disable",
        "systemctl daemon-reload",
        "useradd",
        "userdel",
        "groupadd",
        "groupdel",
        "install -",
        "mkdir -",
        "rm -",
        "tar -x",
        "pip install",
        "post_order",
        "create_limit_order",
        "cancel_order",
        "PHASE15_ACCEPT_REAL_MONEY",
        "rm -f /etc/bp-canary/KILL",
    ):
        assert forbidden not in text

    for marker in (
        '"mutation_performed": False',
        '"service_started": False',
        '"service_enabled": False',
        '"real_order_submitted": False',
        "NO_MUTATION_PERFORMED=true",
    ):
        assert marker in text

def test_transport_stage_status_accepts_active_remain_after_exit_oneshot_pid_zero() -> None:
    text = STATUS.read_text(encoding="utf-8")
    for marker in (
        '"type": service_type if type_code == 0 else ""',
        '"remain_after_exit": remain_after_exit if remain_code == 0 else ""',
        'state.get("type") == "oneshot"',
        'state.get("remain_after_exit") == "yes"',
        "if not pid_valid and not active_oneshot:",
    ):
        assert marker in text
