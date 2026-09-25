from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_telegram_transport_configuration_plan.py"
)
STATE = ROOT / "PROJECT_STATE.json"
HEAD = "1" * 40


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "telegram_transport_configuration_plan_test",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _clear_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
        "BP_TELEGRAM_BOT_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_configuration_plan_cli_renders_current_blocked_plan(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load()
    _clear_secrets(monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--project-state",
            str(STATE),
            "--release-head",
            HEAD,
        ],
    )

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["release_head"] == HEAD
    assert payload["source_truth_authorized_for_activation"] is False
    assert "second_order_not_authorized" in payload["source_truth_blockers"]
    assert payload["activation_contract"]["plan_authorizes_activation"] is False
    assert payload["secrets_included"] is False
    assert payload["network_action_performed"] is False
    assert payload["mutation_performed"] is False
    assert payload["real_order_submitted"] is False


def test_configuration_plan_cli_rejects_secret_bearing_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load()
    _clear_secrets(monkeypatch)
    monkeypatch.setenv("BP_TELEGRAM_BOT_TOKEN", "must-not-be-here")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--project-state",
            str(STATE),
            "--release-head",
            HEAD,
        ],
    )

    with pytest.raises(SystemExit, match="BP_TELEGRAM_BOT_TOKEN"):
        module.main()


def test_configuration_plan_cli_rejects_symlink_project_state(
    tmp_path: Path,
) -> None:
    module = _load()
    link = tmp_path / "state.json"
    link.symlink_to(STATE)

    with pytest.raises(
        module.TransportConfigurationError,
        match="non-symlink",
    ):
        module._load_project_state(link)


def test_configuration_plan_cli_is_offline_and_non_mutating() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    compile(text, str(SCRIPT), "exec")

    for marker in (
        "create_configuration_plan",
        "source_truth_authorized_for_activation",
        "mutation_performed",
        "network_action_performed",
        "secret_provisioning_performed",
        "service_started",
        "service_enabled",
        "executor_invoked",
        "real_order_submitted",
    ):
        assert marker in text

    for forbidden in (
        "subprocess",
        "httpx",
        "urllib",
        "requests",
        "gcloud",
        "google.cloud",
        "systemctl",
        "scp",
        "add-iam-policy-binding",
        "set-iam-policy",
        "post_order",
        "create_limit_order",
        "cancel_order",
        "phase15_v3_canary_arm",
        "phase15_v3_canary_executor",
        "PHASE15_ACCEPT_REAL_MONEY",
        "write_text(",
        "os.open(",
        "mkdir(",
    ):
        assert forbidden not in text


def test_configuration_plan_cli_contains_no_embedded_secret_values() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    for forbidden in (
        "sk-proj-",
        "POLYMARKET_PRIVATE_KEY=",
        "POLYMARKET_WALLET_ADDRESS=",
        "BP_TELEGRAM_BOT_TOKEN=",
    ):
        assert forbidden not in text

    assert os.path.basename(str(SCRIPT)) == (
        "phase15_v3_telegram_transport_configuration_plan.py"
    )
