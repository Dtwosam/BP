from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALL = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_telegram_transport_stage_install_cloudshell.sh"
)


def test_transport_stage_install_shell_and_embedded_python_are_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(INSTALL)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    text = INSTALL.read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PY'[^\n]*\n(.*?)\nPY(?:\n|$)", text, flags=re.DOTALL)
    assert len(blocks) >= 8
    for block in blocks:
        ast.parse(block)


def test_transport_stage_install_requires_exact_reviewed_release_and_preflights() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    for marker in (
        "PHASE15_ACCEPT_TELEGRAM_TRANSPORT_STAGE_INSTALL",
        "explicit_transport_stage_install_authorization_required",
        "local_working_tree_dirty",
        "local_main_not_current",
        "phase15_v3_telegram_transport_verify_release.py",
        "--expected-commit-sha",
        "phase15_v3_telegram_transport_publisher_install_preflight_cloudshell.sh",
        "PHASE15_V3_TELEGRAM_TRANSPORT_PUBLISHER_INSTALL_PREFLIGHT=PASS",
        "phase15_v3_telegram_transport_install_preflight_cloudshell.sh",
        "PHASE15_V3_TELEGRAM_TRANSPORT_INSTALL_PREFLIGHT=PASS",
        "release_archive_sha256_mismatch",
    ):
        assert marker in text

    first_copy = text.index("gcloud compute scp")
    assert text.index("transport_release_verification_failed") < first_copy
    assert text.index("publisher_install_preflight_not_pass") < first_copy
    assert text.index("executor_install_preflight_not_pass") < first_copy


def test_transport_stage_install_stages_runtime_but_never_activates_services() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    for marker in (
        "gcloud compute scp",
        "python3 -m venv",
        "--only-binary=:all:",
        "phase15-telegram-approved-outbox-handoff.sh",
        "APPROVED_OUTBOX_HANDOFF_STAGED=true",
        'assert version("httpx") == "0.28.1"',
        'assert version("google-cloud-pubsub") == "2.41.0"',
        '"$VENV/bin/pip" check',
        "systemctl daemon-reload",
        "publisher_service_unexpectedly_active",
        "publisher_service_unexpectedly_enabled",
        "transport_service_started_during_stage",
        "transport_service_enabled_during_stage",
        "SERVICES_STARTED=false",
        "SERVICES_ENABLED=false",
    ):
        assert marker in text

    assert "systemctl start " not in text
    assert "systemctl restart " not in text
    assert re.search(r"\bsystemctl enable\b", text) is None


def test_transport_stage_install_provisions_no_runtime_secrets_or_permissions() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    for marker in (
        "publisher_env_must_not_exist_before_stage",
        "transport_key_must_not_exist_before_stage",
        "publisher_env_created_during_stage",
        "transport_key_created_during_stage",
        "secret_or_env_created_during_stage",
        "ENVIRONMENT_FILES_CREATED=false",
        "KEY_FILES_CREATED=false",
        "IAM_CHANGED=false",
        "PUBSUB_RESOURCES_CHANGED=false",
        "NO_REAL_ORDER_SUBMITTED=true",
    ):
        assert marker in text

    for forbidden in (
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


def test_transport_stage_install_rollback_is_bound_to_its_own_stage() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    for marker in (
        "STAGE-METADATA.json",
        "phase15-canary-telegram-transport-stage-owner.json",
        "telegram-transport-stage-owner.json",
        "stage_owner_already_exists",
        '"stage_complete": True',
        'assert payload["stage_id"] == sys.argv[2]',
        'assert payload["role"] == "publisher"',
        'assert payload["role"] == "executor"',
        '"created_bp_transport_user"',
        '"created_bp_transport_group"',
        'if [[ "$CREATED_USER" == "true" ]]',
        'if [[ "$CREATED_GROUP" == "true" ]]',
        "rollback_executor",
        "rollback_recorder",
    ):
        assert marker in text


def test_transport_stage_install_rolls_back_ambiguous_remote_attempts() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    recorder_flag = text.index("RECORDER_STAGED=true")
    recorder_ssh = text.index('gcloud compute ssh "$US_VM"', recorder_flag)
    executor_flag = text.index("EXEC_STAGED=true")
    executor_ssh = text.index('gcloud compute ssh "$EXEC_VM"', executor_flag)
    assert recorder_flag < recorder_ssh
    assert executor_flag < executor_ssh

    for marker in (
        '[[ -f "$OWNER" ]] || exit 0',
        'assert payload["stage_id"] == sys.argv[2]',
        'assert payload["role"] == "publisher"',
        'assert payload["role"] == "executor"',
    ):
        assert marker in text


def test_transport_stage_install_rolls_back_if_main_moves_mid_transaction() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    executor_attempt = text.index("EXEC_STAGED=true")
    main_recheck = text.index(
        "REMOTE_MAIN_AFTER=$(git ls-remote origin refs/heads/main",
        executor_attempt,
    )
    final_commit = text.index("COMMITTED=true", main_recheck)
    assert executor_attempt < main_recheck < final_commit
    assert "remote_main_changed_during_stage" in text


def test_transport_stage_install_separates_transport_and_authorization_state() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    for marker in (
        "TRANSPORT_STATE_ROOT=/var/lib/bp-telegram-transport",
        "TRANSPORT_PRIVATE_STATE_DIRS=(",
        'TRANSPORT_READY_DIR="$TRANSPORT_STATE_ROOT/ready"',
        "AUTH_STATE_DIRS=(",
        "/var/lib/bp-canary/telegram-dispatch-claims",
        "/var/lib/bp-canary/telegram-execution-authorized",
        "/var/lib/bp-canary/telegram-execution-auth-processed",
        "/var/lib/bp-canary/telegram-execution-auth-failures",
        "/var/lib/bp-canary/telegram-live-handoff",
        'install -d -o root -g bp-transport -m 0710 "$TRANSPORT_STATE_ROOT"',
        'install -d -o bp-transport -g bp-transport -m 0700 "$dir"',
        'install -d -o bp-transport -g bp-transport -m 0750 "$TRANSPORT_READY_DIR"',
        'install -d -o root -g root -m 0700 "$dir"',
        "bp-phase15-telegram-execution-authorization-worker.service",
        "bp-phase15-telegram-privileged-handoff.service",
        "run_phase15_v3_telegram_execution_package_verify.py",
        "run_phase15_v3_telegram_privileged_handoff_verify.py",
        "src/bp_engine/execution/telegram_execution_package.py",
        "src/bp_engine/execution/telegram_privileged_handoff.py",
        "run_phase15_v3_telegram_privileged_handoff_worker.py",
        "src/bp_engine/execution/telegram_privileged_consumer.py",
        "EXECUTION_AUTHORIZATION_WORKER_STAGED=true",
        "PRIVILEGED_HANDOFF_WORKER_STAGED=true",
    ):
        assert marker in text


def test_transport_stage_install_preserves_live_runtime_boundaries() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    for marker in (
        "RECORDER_PID_BEFORE",
        "PREDICTOR_PID_BEFORE",
        "PAPER_PID_BEFORE",
        "RECORDER_PID_AFTER",
        "PREDICTOR_PID_AFTER",
        "PAPER_PID_AFTER",
        "recorder_restarted",
        "predictor_restarted",
        "paper_executor_restarted",
        "executor_health_before_failed",
        "executor_health_after_failed",
        'assert payload["kill_switch_engaged"] is True',
        'assert payload["activation_valid"] is False',
        'assert payload["submission_ready"] is False',
        'assert payload["live_order_submitted"] is False',
        'assert geoblock.get("blocked") is False',
        'assert geoblock.get("country") == "ZA"',
        "EXECUTOR_SAFE_IDLE_PRESERVED=true",
    ):
        assert marker in text

def test_transport_stage_install_stages_all_executor_units_and_verifies_bytes() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    executor_start = text.index("EXECUTOR_SCRIPT=$(cat <<'REMOTE'")
    executor_text = text[executor_start:]
    services_block = """SERVICES=(
  bp-phase15-telegram-pubsub-streaming-receiver.service
  bp-phase15-telegram-transport-claim-worker.service
  bp-phase15-telegram-execution-authorization-worker.service
  bp-phase15-telegram-privileged-handoff.service
)"""
    assert services_block in executor_text
    assert 'cmp -s "$RELEASE/deploy/$service" "/etc/systemd/system/$service"' in executor_text
    assert 'fail "transport_unit_install_hash_mismatch:$service"' in executor_text


def test_transport_stage_install_makes_runtime_venvs_service_user_usable() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    executor_start = text.index("EXECUTOR_SCRIPT=$(cat <<'REMOTE'")
    publisher_text = text[:executor_start]
    executor_text = text[executor_start:]

    for marker in (
        'command -v runuser >/dev/null 2>&1 || fail "runuser_missing"',
        'chown -hR root:bp "$VENV"',
        'chmod -R g+rX,o-rwx "$VENV"',
        'runuser -u bp -- "$VENV/bin/python"',
        'fail "publisher_venv_not_usable_by_service_user"',
        'chown -hR root:bp "$RELEASE"',
        'find "$RELEASE" -type d -exec chmod 0750 {} +',
        'find "$RELEASE" -type f -exec chmod 0640 {} +',
        'fail "publisher_release_not_readable_by_service_user"',
    ):
        assert marker in publisher_text

    for marker in (
        'command -v runuser >/dev/null 2>&1 || fail "runuser_missing"',
        'chown -hR root:bp-transport "$VENV"',
        'chmod -R g+rX,o-rwx "$VENV"',
        'runuser -u bp-transport -- "$VENV/bin/python"',
        'fail "executor_venv_not_usable_by_service_user"',
        'chown -hR root:bp-transport "$RELEASE"',
        'find "$RELEASE" -type d -exec chmod 0750 {} +',
        'find "$RELEASE" -type f -exec chmod 0640 {} +',
        'fail "executor_release_not_readable_by_service_user"',
        'fail "executor_claim_worker_not_readable_by_service_user"',
    ):
        assert marker in executor_text
