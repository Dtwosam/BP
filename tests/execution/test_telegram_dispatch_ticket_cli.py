from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

from bp_engine.execution.telegram_pre_execution import (
    evaluate_pre_execution_authorization,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_phase15_v3_telegram_dispatch_ticket.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("telegram_dispatch_ticket_cli_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ready(now: datetime) -> dict[str, object]:
    return {
        "status": "execution_ready_origin_verified",
        "transport_key_id": "phase15-telegram-transport-v1",
        "origin_key_id": "phase15-telegram-origin-v1",
        "intent_id": "live-intent-dispatch-cli",
        "prediction_id": "prediction-dispatch-cli",
        "paper_order_id": "paper-dispatch-cli",
        "request_sha256": "1" * 64,
        "prepared_sha256": "2" * 64,
        "approval_sha256": "3" * 64,
        "approval_source_sha256": "4" * 64,
        "origin_attestation_sha256": "5" * 64,
        "origin_attested_at": now.isoformat(),
        "origin_expires_at": (now + timedelta(seconds=15)).isoformat(),
        "retry_allowed": False,
        "network_action_performed": False,
        "executor_invoked": False,
        "real_order_submitted": False,
    }


def _state() -> dict[str, object]:
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


def _report(now: datetime) -> dict[str, object]:
    return evaluate_pre_execution_authorization(
        ready_verification=_ready(now),
        project_state=_state(),
    )


def _safe_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BP_TELEGRAM_DISPATCH_TICKET_ENABLED", "yes")
    monkeypatch.setenv("MODE", "research")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("MAX_TRADE_SIZE_USD", "0")
    monkeypatch.setenv("MAX_DAILY_LOSS_USD", "0")
    for name in (
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
        "BP_TELEGRAM_BOT_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        monkeypatch.delenv(name, raising=False)


def _write_private(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)


def test_dispatch_cli_create_then_claim_is_offline_one_shot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load()
    now = datetime(2026, 9, 24, 21, 0, 2, tzinfo=UTC)
    report_path = tmp_path / "pre-execution.json"
    ready_path = tmp_path / "ready-verification.json"
    state_path = tmp_path / "PROJECT_STATE.json"
    _write_private(report_path, _report(now))
    _write_private(ready_path, _ready(now))
    state_path.write_text(json.dumps(_state()), encoding="utf-8")
    state_path.chmod(0o644)
    output_dir = tmp_path / "tickets"
    output_dir.mkdir(mode=0o700)
    ticket_path = output_dir / "dispatch.json"
    state_dir = tmp_path / "claims"
    _safe_env(monkeypatch)
    monkeypatch.setattr(module, "_utc_now", lambda: now + timedelta(seconds=1))

    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "create",
            "--pre-execution-report",
            str(report_path),
            "--output",
            str(ticket_path),
        ],
    )
    assert module.main() == 0
    created = json.loads(capsys.readouterr().out)
    assert created["status"] == "dispatch_ticket_written"
    assert created["executor_invoked"] is False
    assert created["real_order_submitted"] is False
    assert (os.stat(ticket_path).st_mode & 0o777) == 0o600

    monkeypatch.setattr(module, "_utc_now", lambda: now + timedelta(seconds=2))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "claim",
            "--ticket",
            str(ticket_path),
            "--pre-execution-report",
            str(report_path),
            "--ready-verification",
            str(ready_path),
            "--project-state",
            str(state_path),
            "--state-dir",
            str(state_dir),
        ],
    )
    assert module.main() == 0
    claimed = json.loads(capsys.readouterr().out)
    assert claimed["status"] == "dispatch_claimed"
    assert claimed["retry_allowed"] is False
    assert claimed["executor_invoked"] is False
    assert claimed["real_order_submitted"] is False
    assert (os.stat(state_dir).st_mode & 0o777) == 0o700
    assert (os.stat(Path(claimed["claim_path"])).st_mode & 0o777) == 0o600

    assert module.main() == 1
    replay = json.loads(capsys.readouterr().out)
    assert replay["status"] == "failed_closed"
    assert "already claimed" in replay["error"]


def test_dispatch_cli_rejects_disabled_secret_or_weak_file_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load()
    now = datetime(2026, 9, 24, 21, 0, 2, tzinfo=UTC)
    report_path = tmp_path / "pre-execution.json"
    _write_private(report_path, _report(now))
    output_dir = tmp_path / "tickets"
    output_dir.mkdir(mode=0o700)
    ticket_path = output_dir / "dispatch.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "create",
            "--pre-execution-report",
            str(report_path),
            "--output",
            str(ticket_path),
        ],
    )
    with pytest.raises(SystemExit, match="not enabled"):
        module.main()

    _safe_env(monkeypatch)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "must-not-be-here")
    with pytest.raises(SystemExit, match="POLYMARKET_PRIVATE_KEY"):
        module.main()

    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    report_path.chmod(0o644)
    assert module.main() == 1


def test_dispatch_cli_rejects_symlink_input_and_non_private_output_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load()
    now = datetime(2026, 9, 24, 21, 0, 2, tzinfo=UTC)
    report = tmp_path / "report.json"
    _write_private(report, _report(now))
    link = tmp_path / "report-link.json"
    link.symlink_to(report)
    output_dir = tmp_path / "tickets"
    output_dir.mkdir(mode=0o700)
    _safe_env(monkeypatch)

    with pytest.raises(module.DispatchTicketError, match="non-symlink"):
        module._load_private_json(link, label="pre-execution report")

    output_dir.chmod(0o755)
    with pytest.raises(module.DispatchTicketError, match="mode must be 0700"):
        module._write_once(output_dir / "ticket.json", {"a": 1})


def test_dispatch_cli_source_has_no_network_wallet_or_execution_path() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    compile(text, str(SCRIPT), "exec")
    for marker in (
        "create_dispatch_ticket",
        "claim_dispatch_ticket",
        "BP_TELEGRAM_DISPATCH_TICKET_ENABLED",
        "retry_allowed",
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


def test_dispatch_cli_rejects_current_source_truth_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load()
    now = datetime(2026, 9, 24, 21, 0, 2, tzinfo=UTC)
    ready = _ready(now)
    state = _state()
    report = evaluate_pre_execution_authorization(
        ready_verification=ready,
        project_state=state,
    )
    ticket = module.create_dispatch_ticket(
        report,
        created_at=now + timedelta(seconds=1),
    )
    report_path = tmp_path / "pre-execution.json"
    ready_path = tmp_path / "ready-verification.json"
    state_path = tmp_path / "PROJECT_STATE.json"
    ticket_path = tmp_path / "dispatch.json"
    _write_private(report_path, report)
    _write_private(ready_path, ready)
    _write_private(ticket_path, ticket)
    changed_state = json.loads(json.dumps(state))
    phase = changed_state["phase_15_v3_live_canary"]
    assert isinstance(phase, dict)
    phase["second_order_authorized"] = False
    state_path.write_text(json.dumps(changed_state), encoding="utf-8")
    state_path.chmod(0o644)
    _safe_env(monkeypatch)
    monkeypatch.setattr(module, "_utc_now", lambda: now + timedelta(seconds=2))
    state_dir = tmp_path / "claims"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "claim",
            "--ticket",
            str(ticket_path),
            "--pre-execution-report",
            str(report_path),
            "--ready-verification",
            str(ready_path),
            "--project-state",
            str(state_path),
            "--state-dir",
            str(state_dir),
        ],
    )

    assert module.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "failed_closed"
    assert "stale or modified" in result["error"]
    assert not state_dir.exists()
