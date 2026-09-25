from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "scripts/deploy/phase15_v3_telegram_approval_install_cloudshell.sh"
STATUS = ROOT / "scripts/deploy/phase15_v3_telegram_approval_status_cloudshell.sh"
READINESS = ROOT / "scripts/deploy/phase15_v3_telegram_activation_readiness_cloudshell.sh"
DISABLE = ROOT / "scripts/deploy/phase15_v3_telegram_approval_disable_cloudshell.sh"
RUNNER = ROOT / "scripts/run_phase15_v3_canary_telegram_approval.py"
OUTBOX = ROOT / "scripts/run_phase15_v3_telegram_transport_outbox.py"
INTAKE = ROOT / "scripts/run_phase15_v3_telegram_transport_intake.py"
TRANSPORT = ROOT / "src/bp_engine/execution/telegram_transport.py"
UNIT = ROOT / "deploy/bp-phase15-canary-telegram-approval.service"


def test_telegram_listener_unit_is_research_zero_money_and_secret_limited() -> None:
    text = UNIT.read_text(encoding="utf-8")
    for marker in (
        "User=bp",
        "Group=bp",
        "EnvironmentFile=/etc/bp/telegram-approval.env",
        "EnvironmentFile=-/etc/bp/telegram-approval-handoff.env",
        "Environment=MODE=research",
        "Environment=LIVE_TRADING_ENABLED=false",
        "Environment=MAX_TRADE_SIZE_USD=0",
        "Environment=MAX_DAILY_LOSS_USD=0",
        "Environment=BP_TELEGRAM_HANDOFF_ENABLED=no",
        "UnsetEnvironment=POLYMARKET_PRIVATE_KEY POLYMARKET_WALLET_ADDRESS",
        "NoNewPrivileges=true",
        "ProtectSystem=full",
        "ReadOnlyPaths=-/var/lib/bp/phase15-canary-prepare-watch",
        "ReadWritePaths=/var/lib/bp/phase15-canary-telegram-approval",
    ):
        assert marker in text
    assert "BP_TELEGRAM_HANDOFF_COMMAND=" not in text
    assert "POLYMARKET_PRIVATE_KEY=" not in text


def test_telegram_runner_requires_zero_money_runtime_and_explicit_handoff_enable() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    for marker in (
        'os.environ.get("MODE") != "research"',
        'os.environ.get("LIVE_TRADING_ENABLED") != "false"',
        'os.environ.get("MAX_TRADE_SIZE_USD") != "0"',
        'os.environ.get("MAX_DAILY_LOSS_USD") != "0"',
        '"POLYMARKET_PRIVATE_KEY", "POLYMARKET_WALLET_ADDRESS"',
        'os.environ.get("BP_TELEGRAM_HANDOFF_ENABLED", "no")',
        "Telegram handoff command configured without explicit enable",
    ):
        assert marker in text
    ast.parse(text)


def test_telegram_install_is_listener_only_and_token_never_enters_git() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    for marker in (
        "PHASE15_ACCEPT_TELEGRAM_APPROVAL_INSTALL",
        "explicit_telegram_approval_install_authorization_required",
        "Telegram bot token (hidden):",
        "read -r -s BOT_TOKEN",
        "private_chat_id_must_match_user_id",
        'call("getMe", {})',
        'call("getChat", {"chat_id": chat_id})',
        "scripts/run_phase15_v3_canary_telegram_approval.py",
        "src/bp_engine/execution/telegram_approval.py",
        "deploy/bp-phase15-canary-telegram-approval.service",
        "install -o root -g bp -m 0640",
        "handoff_configuration_forbidden",
        "handoff_env_must_not_exist_for_listener_install",
        "handoff_env_created_or_present",
        'systemctl enable "$SERVICE"',
        'systemctl restart "$SERVICE"',
        "CORE_SERVICE_PIDS_PRESERVED=true",
        "BOT_TOKEN_STORED_IN_GIT=false",
        "HANDOFF_CONFIGURED=false",
        "NO_REAL_ORDER_SUBMITTED=true",
    ):
        assert marker in text
    for forbidden in (
        "phase15_v3_canary_arm_cloudshell.sh",
        "phase15_v3_canary_executor.py",
        "sudo /opt/bp-canary/executor.sh",
        "PHASE15_ACCEPT_REAL_MONEY",
        "post_order",
        "create_limit_order",
        "POLYMARKET_PRIVATE_KEY",
    ):
        assert forbidden not in text


def test_telegram_install_env_contains_only_identity_and_bot_token() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    block = re.search(
        r'cat > "\$ENV_LOCAL" <<EOF\n(.*?)\nEOF',
        text,
        flags=re.DOTALL,
    )
    assert block is not None
    env_text = block.group(1)
    keys = sorted(
        line.split("=", 1)[0]
        for line in env_text.splitlines()
        if "=" in line
    )
    assert keys == [
        "BP_TELEGRAM_BOT_TOKEN",
        "BP_TELEGRAM_CHAT_ID",
        "BP_TELEGRAM_USER_ID",
    ]
    assert "HANDOFF" not in env_text


def test_telegram_status_is_read_only_and_never_prints_secret_values() -> None:
    text = STATUS.read_text(encoding="utf-8")
    for marker in (
        "service_active",
        "service_enabled",
        "bot_token_set",
        "telegram_user_id_set",
        "telegram_chat_id_set",
        "handoff_configured",
        "handoff_env_present",
        "journal_error_line_count_last_15m",
        '"owner": pwd.getpwuid(info.st_uid).pw_name',
        '"group": grp.getgrgid(info.st_gid).gr_name',
        "LISTENER_BINDING_CURRENT=true",
        "HANDOFF_CONFIGURED=false",
        "NO_REAL_ORDER_SUBMITTED=true",
    ):
        assert marker in text
    for forbidden in (
        "systemctl restart",
        "systemctl start",
        "systemctl enable",
        "systemctl stop",
        "cat /etc/bp/telegram-approval.env",
        'payload["BP_TELEGRAM_BOT_TOKEN"]',
        "PHASE15_ACCEPT_REAL_MONEY",
    ):
        assert forbidden not in text


def test_telegram_install_and_status_shell_syntax_is_valid() -> None:
    for path in (INSTALL, STATUS):
        completed = subprocess.run(
            ["bash", "-n", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr


def test_telegram_install_embedded_python_is_syntax_valid() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PY'[^\n]*\n(.*?)\nPY(?:\n|$)", text, flags=re.DOTALL)
    assert len(blocks) >= 2
    for block in blocks:
        ast.parse(block)


def test_telegram_status_embedded_python_is_syntax_valid() -> None:
    text = STATUS.read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PY'[^\n]*\n(.*?)\nPY(?:\n|$)", text, flags=re.DOTALL)
    assert len(blocks) >= 3
    for block in blocks:
        ast.parse(block)


def test_telegram_activation_readiness_is_strictly_read_only() -> None:
    text = READINESS.read_text(encoding="utf-8")
    for marker in (
        '"second_order_authorized"',
        '"automated_real_money_submission"',
        '"telegram_one_tap_submission_authorized"',
        '"telegram_persistent_execution_transport_authorized"',
        '"telegram_pubsub_transport_authorized"',
        "second_order_not_authorized",
        "telegram_one_tap_not_authorized",
        "persistent_execution_transport_not_authorized",
        "telegram_pubsub_transport_not_authorized",
        'printf \'%s\' \'{"action":"health"}\'',
        "kill_switch_not_engaged",
        "unexpected_submission_ready",
        "TELEGRAM_ACTIVATION_READY=false",
        "NO_MUTATION_PERFORMED=true",
    ):
        assert marker in text
    for forbidden in (
        "PHASE15_ACCEPT_REAL_MONEY",
        "phase15_v3_canary_arm_cloudshell.sh",
        "post_order",
        "create_limit_order",
        "systemctl restart",
        "systemctl start",
        "systemctl enable",
        "rm -f /etc/bp-canary/KILL",
    ):
        assert forbidden not in text


def test_telegram_activation_readiness_shell_and_embedded_python_are_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(READINESS)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    text = READINESS.read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PY'[^\n]*\n(.*?)\nPY(?:\n|$)", text, flags=re.DOTALL)
    assert len(blocks) >= 3
    for block in blocks:
        ast.parse(block)


def test_telegram_disable_revokes_token_but_preserves_audit_state() -> None:
    text = DISABLE.read_text(encoding="utf-8")
    for marker in (
        "PHASE15_ACCEPT_TELEGRAM_APPROVAL_DISABLE",
        "explicit_telegram_approval_disable_authorization_required",
        'systemctl stop "$SERVICE"',
        'systemctl disable "$SERVICE"',
        'rm -f "$ENV_PATH" "$HANDOFF_ENV_PATH"',
        "BOT_TOKEN_FILE_PRESENT=false",
        "HANDOFF_ENV_PRESENT=false",
        "APPROVAL_AUDIT_STATE_PRESERVED=true",
        "CORE_SERVICE_PIDS_PRESERVED=true",
        "NO_REAL_ORDER_SUBMITTED=true",
    ):
        assert marker in text
    for forbidden in (
        'rm -rf "$STATE_ROOT"',
        "PHASE15_ACCEPT_REAL_MONEY",
        "post_order",
        "create_limit_order",
        "sudo /opt/bp-canary/executor.sh",
    ):
        assert forbidden not in text


def test_telegram_disable_shell_syntax_is_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(DISABLE)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_telegram_transport_protocol_and_adapters_are_carrierless() -> None:
    transport = TRANSPORT.read_text(encoding="utf-8")
    outbox = OUTBOX.read_text(encoding="utf-8")
    intake = INTAKE.read_text(encoding="utf-8")

    for marker in (
        "TRANSPORT_MAX_LIFETIME_SECONDS = 15",
        "hmac.compare_digest",
        "transport envelope already claimed",
        "retry_allowed",
        "claim_key = hashlib.sha256",
        "load_transport_key_file",
        '"key_id"',
    ):
        assert marker in transport

    for marker in (
        "BP_TELEGRAM_TRANSPORT_OUTBOX_ENABLED",
        "BP_TELEGRAM_TRANSPORT_KEY_FILE",
        "BP_TELEGRAM_TRANSPORT_KEY_ID",
        "network_send_attempted",
        "real_order_submitted",
    ):
        assert marker in outbox

    for marker in (
        "BP_TELEGRAM_TRANSPORT_INTAKE_ENABLED",
        "BP_TELEGRAM_TRANSPORT_KEY_FILE",
        "BP_TELEGRAM_TRANSPORT_KEY_ID",
        "executor_invoked",
        "real_order_submitted",
        'claimed["claim_id"]',
    ):
        assert marker in intake

    assert "BP_TELEGRAM_TRANSPORT_HMAC_KEY" not in outbox
    assert "BP_TELEGRAM_TRANSPORT_HMAC_KEY" not in intake

    combined = "\n".join((transport, outbox, intake))
    for forbidden in (
        "gcloud",
        "urlopen",
        "httpx",
        "requests.",
        "post_order",
        "create_limit_order",
        "polymarket",
        "PHASE15_ACCEPT_REAL_MONEY",
        "sudo /opt/bp-canary/executor.sh",
    ):
        assert forbidden not in combined


def test_telegram_transport_python_sources_are_syntax_valid() -> None:
    for path in (TRANSPORT, OUTBOX, INTAKE):
        ast.parse(path.read_text(encoding="utf-8"))
