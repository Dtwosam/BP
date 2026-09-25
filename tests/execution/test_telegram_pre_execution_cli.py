from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_phase15_v3_telegram_pre_execution_gate.py"
STATE = ROOT / "PROJECT_STATE.json"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("telegram_pre_execution_gate_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ready() -> dict[str, object]:
    return {
        "status": "execution_ready_origin_verified",
        "transport_key_id": "phase15-telegram-transport-v1",
        "origin_key_id": "phase15-telegram-origin-v1",
        "intent_id": "live-intent-pre-exec-cli",
        "prediction_id": "prediction-pre-exec-cli",
        "paper_order_id": "paper-pre-exec-cli",
        "request_sha256": "1" * 64,
        "prepared_sha256": "2" * 64,
        "approval_sha256": "3" * 64,
        "approval_source_sha256": "4" * 64,
        "origin_attestation_sha256": "5" * 64,
        "origin_attested_at": "2026-09-24T21:00:02+00:00",
        "origin_expires_at": "2026-09-24T21:00:15+00:00",
        "retry_allowed": False,
        "network_action_performed": False,
        "executor_invoked": False,
        "real_order_submitted": False,
    }


def _authorized_state() -> dict[str, object]:
    return {
        "live_trading_enabled": False,
        "phase_15_v3_live_canary": {
            "live_trading_enabled": False,
            "phase15_canary_authorized": True,
            "canary_order_submitted": True,
            "pending_unsubmitted_intent": None,
            "v3_strategy_mutation_performed": False,
            "second_order_authorized": True,
            "automated_real_money_submission": True,
            "manual_real_money_submission_required": False,
            "telegram_one_tap_submission_authorized": True,
            "telegram_persistent_execution_transport_authorized": True,
            "telegram_pubsub_transport_authorized": True,
            "first_live_canary": {
                "official_reconciliation_complete": True,
            },
        },
    }


def test_pre_execution_gate_main_reports_current_source_truth_authorized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load()
    origin_key = tmp_path / "origin.key"
    origin_key.write_text("not-read-because-verifier-is-stubbed\n", encoding="utf-8")
    monkeypatch.setattr(module, "verify_ready_bundle", lambda **_: _ready())
    monkeypatch.setattr(
        module,
        "_utc_now",
        lambda: datetime(2026, 9, 24, 21, 0, tzinfo=UTC),
    )
    monkeypatch.delenv("BP_TELEGRAM_TRANSPORT_KEY_FILE", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            str(tmp_path / "ready"),
            "--origin-key-file",
            str(origin_key),
            "--origin-key-id",
            "phase15-telegram-origin-v1",
            "--project-state",
            str(STATE),
        ],
    )

    assert module.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "pre_execution_authorized"
    assert result["authorized"] is True
    assert result["blockers"] == []
    assert result["mutation_performed"] is False
    assert result["executor_invoked"] is False
    assert result["real_order_submitted"] is False


def test_pre_execution_gate_main_can_only_report_synthetic_authorized_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load()
    origin_key = tmp_path / "origin.key"
    origin_key.write_text("not-read-because-verifier-is-stubbed\n", encoding="utf-8")
    project_state = tmp_path / "PROJECT_STATE.json"
    project_state.write_text(json.dumps(_authorized_state()), encoding="utf-8")
    monkeypatch.setattr(module, "verify_ready_bundle", lambda **_: _ready())
    monkeypatch.delenv("BP_TELEGRAM_TRANSPORT_KEY_FILE", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            str(tmp_path / "ready"),
            "--origin-key-file",
            str(origin_key),
            "--origin-key-id",
            "phase15-telegram-origin-v1",
            "--project-state",
            str(project_state),
        ],
    )

    assert module.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "pre_execution_authorized"
    assert result["authorized"] is True
    assert result["blockers"] == []
    assert result["transport_key_id"] == "phase15-telegram-transport-v1"
    assert result["origin_key_id"] == "phase15-telegram-origin-v1"
    assert len(result["authorization_report_sha256"]) == 64
    assert result["mutation_performed"] is False
    assert result["executor_invoked"] is False
    assert result["real_order_submitted"] is False


def test_project_state_loader_rejects_symlink(tmp_path: Path) -> None:
    module = _load()
    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")
    link = tmp_path / "state-link.json"
    link.symlink_to(state)

    with pytest.raises(module.PreExecutionError, match="regular non-symlink"):
        module._load_project_state(link)


def test_pre_execution_gate_source_has_no_execution_or_network_path() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    compile(text, str(SCRIPT), "exec")
    for marker in (
        "verify_ready_bundle",
        "evaluate_pre_execution_authorization",
        "--project-state",
        "authorized",
        "mutation_performed",
        "executor_invoked",
        "real_order_submitted",
    ):
        assert marker in text
    for forbidden in (
        "httpx",
        "urllib",
        "requests",
        "subprocess",
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
