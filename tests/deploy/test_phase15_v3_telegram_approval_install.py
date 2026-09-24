from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "scripts/deploy/phase15_v3_telegram_approval_install_cloudshell.sh"
STATUS = ROOT / "scripts/deploy/phase15_v3_telegram_approval_status_cloudshell.sh"
RUNNER = ROOT / "scripts/run_phase15_v3_canary_telegram_approval.py"
UNIT = ROOT / "deploy/bp-phase15-canary-telegram-approval.service"


def test_telegram_listener_unit_is_research_zero_money_and_secret_limited() -> None:
    text = UNIT.read_text(encoding="utf-8")
    for marker in (
        "User=bp",
        "Group=bp",
        "EnvironmentFile=/etc/bp/telegram-approval.env",
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
    assert "BP_TELEGRAM_HANDOFF_ENABLED=" not in text
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
