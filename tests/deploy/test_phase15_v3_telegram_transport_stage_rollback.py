from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROLLBACK = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_telegram_transport_stage_rollback_cloudshell.sh"
)


def test_transport_stage_rollback_shell_and_embedded_python_are_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(ROLLBACK)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    text = ROLLBACK.read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PY'[^\n]*\n(.*?)\nPY(?:\n|$)", text, flags=re.DOTALL)
    assert len(blocks) >= 9
    for block in blocks:
        ast.parse(block)


def test_transport_stage_rollback_requires_explicit_stage_identity_and_safe_source() -> None:
    text = ROLLBACK.read_text(encoding="utf-8")
    for marker in (
        "PHASE15_ACCEPT_TELEGRAM_TRANSPORT_STAGE_ROLLBACK",
        "explicit_transport_stage_rollback_authorization_required",
        "PHASE15_TELEGRAM_TRANSPORT_STAGE_ID",
        "stage_id_invalid",
        "local_working_tree_dirty",
        "local_main_not_current",
        "source_truth_not_safe_for_stage_rollback",
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


def test_transport_stage_rollback_refuses_activated_or_configured_transport() -> None:
    text = ROLLBACK.read_text(encoding="utf-8")
    for marker in (
        "publisher_service_active",
        "publisher_service_enabled",
        "publisher_env_present",
        "publisher_key_present",
        "executor_service_active",
        "executor_service_enabled",
        "executor_secret_or_env_present",
        "bp-phase15-telegram-privileged-handoff.service",
        "/var/lib/bp-canary/telegram-live-handoff",
        "privileged-handoff.env",
        "stage_rollback_preconditions_failed",
    ):
        assert marker in text

    assert "systemctl start " not in text
    assert "systemctl restart " not in text
    assert "systemctl stop " not in text
    assert "systemctl disable " not in text
    assert re.search(r"\bsystemctl enable\b", text) is None


def test_transport_stage_rollback_revalidates_owner_and_metadata_before_deletion() -> None:
    text = ROLLBACK.read_text(encoding="utf-8")
    for marker in (
        "phase15-canary-telegram-transport-stage-owner.json",
        "telegram-transport-stage-owner.json",
        'assert owner["stage_id"] == sys.argv[3] == meta["stage_id"]',
        'assert owner["role"] == "executor" == meta["role"]',
        'assert owner["role"] == "publisher" == meta["role"]',
        'assert meta["stage_complete"] is True',
        'assert owner["release_head"] == meta["release_head"]',
        'assert owner["archive_sha256"] == meta["archive_sha256"]',
        "cross_host_release_head_mismatch",
        "cross_host_archive_sha256_mismatch",
    ):
        assert marker in text


def test_transport_stage_rollback_preserves_executor_and_core_runtime() -> None:
    text = ROLLBACK.read_text(encoding="utf-8")
    for marker in (
        "RECORDER_PID_BEFORE",
        "PREDICTOR_PID_BEFORE",
        "PAPER_PID_BEFORE",
        "RECORDER_PID_AFTER",
        "PREDICTOR_PID_AFTER",
        "PAPER_PID_AFTER",
        "HEALTH_BEFORE",
        "HEALTH_AFTER",
        'assert payload["kill_switch_engaged"] is True',
        'assert payload["activation_valid"] is False',
        'assert payload["submission_ready"] is False',
        'assert payload["live_order_submitted"] is False',
        'assert geoblock.get("blocked") is False',
        'assert geoblock.get("country") == "ZA"',
    ):
        assert marker in text


def test_transport_stage_rollback_has_no_live_or_cloud_resource_mutation_path() -> None:
    text = ROLLBACK.read_text(encoding="utf-8")
    for forbidden in (
        "gcloud compute scp",
        "gcloud pubsub topics create",
        "gcloud pubsub subscriptions create",
        "add-iam-policy-binding",
        "set-iam-policy",
        "PHASE15_ACCEPT_REAL_MONEY",
        "phase15_v3_canary_arm_cloudshell.sh",
        "post_order",
        "create_limit_order",
        "cancel_order",
        "rm -f /etc/bp-canary/KILL",
        "POLYMARKET_PRIVATE_KEY=",
        "POLYMARKET_WALLET_ADDRESS=",
    ):
        assert forbidden not in text

    for marker in (
        "KEY_FILES_REMOVED=false",
        "ENVIRONMENT_FILES_REMOVED=false",
        "IAM_CHANGED=false",
        "PUBSUB_RESOURCES_CHANGED=false",
        "LIVE_TRADING_ENABLED=false",
        "NO_REAL_ORDER_SUBMITTED=true",
    ):
        assert marker in text

def test_transport_stage_rollback_user_group_cleanup_is_idempotent() -> None:
    text = ROLLBACK.read_text(encoding="utf-8")
    assert (
        '[[ "$CREATED_USER" == "true" ]] && id bp-transport >/dev/null 2>&1'
        in text
    )
    assert (
        '[[ "$CREATED_GROUP" == "true" ]] && '
        'getent group bp-transport >/dev/null 2>&1'
        in text
    )
    assert '! id bp-transport >/dev/null 2>&1' in text
    assert '! getent group bp-transport >/dev/null 2>&1' in text
