from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_phase15_v3_telegram_execution_package_verify.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "telegram_execution_package_cli_test",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_package_verifier_cli_requires_root_owned_verification(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    module = _load()
    package_dir = tmp_path / "package"
    receipt = tmp_path / "processed.json"
    captured: dict[str, object] = {}

    def fake_verify(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "status": "execution_authorization_package_verified",
            "retry_allowed": False,
            "mutation_performed": False,
            "network_action_performed": False,
            "handoff_invoked": False,
            "executor_invoked": False,
            "real_order_submitted": False,
        }

    monkeypatch.setattr(
        module,
        "verify_execution_authorization_package",
        fake_verify,
    )
    for name in (
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
        "BP_TELEGRAM_BOT_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--package-dir",
            str(package_dir),
            "--processed-receipt",
            str(receipt),
        ],
    )

    assert module.main() == 0
    assert captured["package_dir"] == package_dir
    assert captured["processed_receipt_path"] == receipt
    assert captured["expected_owner_uid"] == 0
    assert "execution_authorization_package_verified" in capsys.readouterr().out


def test_package_verifier_cli_rejects_secret_bearing_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load()
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "must-not-be-here")

    with pytest.raises(SystemExit, match="POLYMARKET_PRIVATE_KEY"):
        module._require_safe_runtime()


def test_package_verifier_cli_has_no_network_or_execution_path() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    compile(text, str(SCRIPT), "exec")
    for marker in (
        "verify_execution_authorization_package",
        "expected_owner_uid=0",
        "mutation_performed",
        "network_action_performed",
        "handoff_invoked",
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
        "post_order",
        "create_limit_order",
        "cancel_order",
        "phase15_v3_canary_arm",
        "phase15_v3_canary_executor",
        "PHASE15_ACCEPT_REAL_MONEY",
        "POLYMARKET_PRIVATE_KEY=",
        "POLYMARKET_WALLET_ADDRESS=",
    ):
        assert forbidden not in text
