from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACTIVATE = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_telegram_transport_activate_cloudshell.sh"
)


def test_transport_activate_shell_and_embedded_python_are_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(ACTIVATE)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    text = ACTIVATE.read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PY'[^\n]*\n(.*?)\nPY(?:\n|$)", text, flags=re.DOTALL)
    assert len(blocks) >= 5
    for block in blocks:
        ast.parse(block)


def test_transport_activate_requires_exact_authorized_source_truth_and_stage() -> None:
    text = ACTIVATE.read_text(encoding="utf-8")
    for marker in (
        "PHASE15_ACCEPT_TELEGRAM_TRANSPORT_ACTIVATION",
        "explicit_transport_activation_authorization_required",
        "local_main_not_current",
        "phase15_v3_telegram_approval_status_cloudshell.sh",
        "telegram_listener_status_not_pass",
        "telegram_listener_not_exact_current_main",
        "telegram_listener_handoff_already_configured",
        "phase15_v3_telegram_transport_stage_status_cloudshell.sh",
        "transport_stage_status_not_pass",
        "installed_executor_not_exact_current_main",
        "BP_TELEGRAM_PROJECT_STATE_FILE=/etc/bp-telegram-transport/project-state.json",
        "installed_source_truth_not_exact_current_main",
        "SOURCE_TRUTH_SHA256=",
        'assert state["live_trading_enabled"] is False',
        'assert gate["live_trading_enabled"] is False',
        'assert gate["second_order_authorized"] is True',
        'assert gate["automated_real_money_submission"] is True',
        'assert gate["manual_real_money_submission_required"] is False',
        'assert gate["telegram_one_tap_submission_authorized"] is True',
        'assert gate["telegram_persistent_execution_transport_authorized"] is True',
        'assert gate["telegram_pubsub_transport_authorized"] is True',
        'assert second.get("strategy_target_notional_usd") == 5',
        'assert second.get("hard_max_trade_size_usd") == 10',
        'assert second.get("max_network_submission_attempts") == 1',
        'assert second.get("broad_autonomous_live_rollout_authorized") is False',
    ):
        assert marker in text


def test_transport_activate_enforces_identity_and_resource_scoped_iam() -> None:
    text = ACTIVATE.read_text(encoding="utf-8")
    for marker in (
        "publisher != default_sa",
        "subscriber != default_sa",
        "publisher != subscriber",
        "roles/owner",
        "roles/editor",
        "roles/pubsub.admin",
        "gcloud pubsub topics create",
        "gcloud pubsub subscriptions create",
        "roles/pubsub.publisher",
        "roles/pubsub.subscriber",
        "gcloud pubsub topics add-iam-policy-binding",
        "gcloud pubsub subscriptions add-iam-policy-binding",
    ):
        assert marker in text
    assert "gcloud projects add-iam-policy-binding" not in text


def test_transport_activate_generates_separate_unprinted_keys_and_least_privilege_files() -> None:
    text = ACTIVATE.read_text(encoding="utf-8")
    for marker in (
        "secrets.token_bytes(32)",
        "transport.key",
        "origin.key",
        "root -g bp -m 0640",
        "root -g bp-transport -m 0640",
        "root -g root -m 0600",
        "telegram-pubsub-publisher.env",
        "telegram-approval-handoff.env",
        "receiver.env",
        "claim.env",
        "execution-auth.env",
        "privileged-handoff.env",
        "BP_TELEGRAM_PRIVILEGED_HANDOFF_ENABLED=yes",
    ):
        assert marker in text
    assert "cat $TMP_DIR/transport.key" not in text
    assert "cat $TMP_DIR/origin.key" not in text


def test_transport_activate_starts_in_dependency_order_and_fails_closed() -> None:
    text = ACTIVATE.read_text(encoding="utf-8")
    receiver = text.index("enable --now bp-phase15-telegram-pubsub-streaming-receiver.service")
    claim = text.index("enable --now bp-phase15-telegram-transport-claim-worker.service")
    auth = text.index("enable --now bp-phase15-telegram-execution-authorization-worker.service")
    privileged = text.index("enable --now bp-phase15-telegram-privileged-handoff.service")
    publisher = text.index("enable --now bp-phase15-telegram-pubsub-publisher.service")
    assert receiver < claim < auth < privileged < publisher

    for marker in (
        "activation-failure-safe-stop",
        'printf "%s\\n" activation-failure-safe-stop > /etc/bp-canary/KILL',
        "systemctl stop bp-phase15-telegram-privileged-handoff.service",
        "systemctl disable bp-phase15-telegram-privileged-handoff.service",
        "rm -f /etc/bp-telegram-transport/receiver.env",
        "rm -f /etc/bp/telegram-pubsub-publisher.env",
        "rmdir /etc/bp-telegram-transport",
        "rm -f /etc/bp/telegram-approval-handoff.env",
        "systemctl restart bp-phase15-canary-telegram-approval.service",
        'assert payload["kill_switch_engaged"] is True',
        'assert payload["activation_valid"] is False',
        'assert payload["submission_ready"] is False',
        'assert payload["live_order_submitted"] is False',
        'assert geoblock.get("blocked") is False',
        'assert geoblock.get("country") == "ZA"',
        'assert account.get("clean_for_canary") is True',
        "WAITING_FOR_FRESH_TELEGRAM_APPROVAL=true",
        "REAL_ORDER_SUBMITTED=false",
    ):
        assert marker in text


def test_transport_activate_preserves_remote_health_json() -> None:
    text = ACTIVATE.read_text(encoding="utf-8")
    assert text.count(r"""'{\"action\":\"health\"}'""") == 2
    assert """'{"action":"health"}'""" not in text


def test_transport_activate_never_contains_order_submission_logic() -> None:
    text = ACTIVATE.read_text(encoding="utf-8")
    for forbidden in (
        "post_order",
        "create_limit_order",
        "cancel_order",
        "PHASE15_ACCEPT_REAL_MONEY",
        "PHASE15_ACCEPT_TELEGRAM_REAL_MONEY",
        '"action":"submit"',
        "'action':'submit'",
    ):
        assert forbidden not in text

def test_transport_activate_requires_durable_activation_authorization() -> None:
    text = ACTIVATE.read_text(encoding="utf-8")
    for marker in (
        'stage.get("status") == "PRODUCTION_STAGED_INACTIVE"',
        'stage.get("activation_authorized") is True',
        'stage.get("stage_ready_for_later_configuration_review") is True',
        'stage.get("restage_required_before_activation_retry") is False',
        'activation.get("status") == "AUTHORIZED_NOT_ACTIVATED"',
        'activation.get("does_not_submit_real_order") is True',
        (
            'activation.get('
            '"fresh_private_telegram_approval_still_required_for_second_canary"'
            ') is True'
        ),
        'activation.get("broad_autonomous_live_rollout_authorized") is False',
    ):
        assert marker in text
