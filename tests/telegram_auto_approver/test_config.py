from __future__ import annotations

import json
from pathlib import Path

import pytest
from bp_telegram_auto_approver.cli import main
from bp_telegram_auto_approver.config import (
    ENV_API_HASH,
    ENV_API_ID,
    ENV_BOT_USER_ID,
    ENV_BOT_USERNAME,
    ENV_LIVE,
    ENV_OPERATOR_USER_ID,
    ENV_SESSION_PATH,
    ENV_STATE_PATH,
    ConfigError,
    load_config,
)
from bp_telegram_auto_approver.contract import ContractMismatch
from bp_telegram_auto_approver.log import JsonLogger


def _env(root: Path, *, live: str | None = None) -> dict[str, str]:
    outside = root.parent / "outside"
    outside.mkdir(exist_ok=True)
    (root / "repo").mkdir(exist_ok=True)
    values = {
        ENV_API_ID: "123456",
        ENV_API_HASH: "0123456789abcdef0123456789abcdef",
        ENV_SESSION_PATH: str(outside / "operator.session"),
        ENV_STATE_PATH: str(outside / "approvals.sqlite"),
        ENV_BOT_USERNAME: "ExampleApprovalBot",
        ENV_BOT_USER_ID: "2000000002",
        ENV_OPERATOR_USER_ID: "1000000001",
    }
    if live is not None:
        values[ENV_LIVE] = live
    return values


def test_only_exact_true_enables_live_mode(tmp_path) -> None:
    root = tmp_path / "repo"
    dry = load_config(_env(tmp_path), root)
    assert dry.live is False
    assert dry.bot_username == "exampleapprovalbot"
    assert load_config(_env(tmp_path, live="true"), root).live is True
    for value in ("True", "TRUE", "1", "yes", " true", "true ", " true "):
        assert load_config(_env(tmp_path, live=value), root).live is False


def test_paths_inside_the_repo_and_bad_identity_are_rejected(tmp_path) -> None:
    root = tmp_path / "repo"
    env = _env(tmp_path)
    env[ENV_SESSION_PATH] = str(root / "operator.session")
    with pytest.raises(ConfigError, match="session_path_invalid"):
        load_config(env, root)
    env = _env(tmp_path)
    env[ENV_SESSION_PATH] = "operator.session"
    with pytest.raises(ConfigError, match="session_path_invalid"):
        load_config(env, root)
    env = _env(tmp_path)
    env[ENV_BOT_USER_ID] = env[ENV_OPERATOR_USER_ID]
    with pytest.raises(ConfigError, match="operator_user_id_invalid"):
        load_config(env, root)
    env = _env(tmp_path)
    env[ENV_BOT_USERNAME] = "not_a_bot_name"
    with pytest.raises(ConfigError, match="bot_username_invalid"):
        load_config(env, root)


def test_contract_mismatch_exits_before_telegram_even_in_live_mode(
    tmp_path, monkeypatch, capsys
) -> None:
    import sys

    def fail(path=None):
        raise ContractMismatch(actual_blob="unreviewed")

    monkeypatch.setattr(
        "bp_telegram_auto_approver.cli.verify_approval_contract",
        fail,
    )
    code = main(["--check-config"], environ=_env(tmp_path, live="true"))
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["event"] == "APPROVAL_CONTRACT_MISMATCH"
    assert payload["actual_blob"] == "unreviewed"
    assert "telethon" not in sys.modules


def test_check_config_does_not_connect_or_print_secrets(tmp_path, capsys) -> None:
    import sys

    code = main(["--check-config"], environ=_env(tmp_path, live="false"))
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["event"] == "CONFIG_OK"
    assert payload["mode"] == "dry-run"
    assert "api_hash" not in payload
    assert "0123456789abcdef0123456789abcdef" not in json.dumps(payload)
    assert "telethon" not in sys.modules

    live = main(["--check-config"], environ=_env(tmp_path, live="true"))
    assert live == 0
    live_payload = json.loads(capsys.readouterr().out)
    assert live_payload["mode"] == "live-auto-approve"
    assert "telethon" not in sys.modules

    logger = JsonLogger()
    logger.emit("CONFIG_OK", api_hash="0123456789abcdef0123456789abcdef", mode="dry-run")
    redacted = json.loads(capsys.readouterr().out)
    assert "api_hash" not in redacted
    assert redacted["mode"] == "dry-run"
