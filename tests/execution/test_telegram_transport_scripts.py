from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

from bp_engine.execution.telegram_approval import approval_record, new_pending
from bp_engine.execution.telegram_transport import (
    TransportError,
    encode_transport_key,
)


ROOT = Path(__file__).resolve().parents[2]
OUTBOX_SCRIPT = ROOT / "scripts" / "run_phase15_v3_telegram_transport_outbox.py"
INTAKE_SCRIPT = ROOT / "scripts" / "run_phase15_v3_telegram_transport_intake.py"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prepared(now: datetime) -> dict[str, object]:
    return {
        "status": "prepared",
        "action": "submit",
        "intent_id": "live-intent-loopback",
        "prediction_id": "prediction-loopback",
        "paper_order_id": "paper-loopback",
        "market_end_at": (now + timedelta(seconds=50)).isoformat(),
        "request": {
            "selected_side": "down",
            "limit_price": "0.72",
            "requested_shares": "6.81",
            "target_notional_usd": "5",
            "token_id": "token-loopback",
            "action": "BUY",
        },
        "policy": {
            "policy_version": "v3-live-canary-v1",
            "max_submission_attempts": 1,
        },
    }


def _write_inputs(tmp_path: Path, now: datetime) -> tuple[Path, Path]:
    prepared = _prepared(now)
    pending = new_pending(
        prepared,
        telegram_user_id=111,
        telegram_chat_id=111,
        created_at=now,
        nonce="approval-nonce",
    )
    approval = approval_record(
        action="approve",
        pending=pending,
        callback_query_id="callback-id",
        approved_at=now + timedelta(seconds=1),
    )
    prepared_path = tmp_path / "prepared.json"
    approval_path = tmp_path / "approval.json"
    prepared_path.write_text(json.dumps(prepared), encoding="utf-8")
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    return prepared_path, approval_path


def _zero_money_env(monkeypatch, key: str) -> None:
    monkeypatch.setenv("MODE", "research")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("MAX_TRADE_SIZE_USD", "0")
    monkeypatch.setenv("MAX_DAILY_LOSS_USD", "0")
    monkeypatch.setenv("BP_TELEGRAM_TRANSPORT_HMAC_KEY", key)
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("POLYMARKET_WALLET_ADDRESS", raising=False)
    monkeypatch.delenv("BP_TELEGRAM_BOT_TOKEN", raising=False)


def test_transport_scripts_loopback_without_network_or_executor(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    prepared_path, approval_path = _write_inputs(tmp_path, now)
    key = encode_transport_key(bytes(range(32)))
    _zero_money_env(monkeypatch, key)

    outbox = _load(OUTBOX_SCRIPT, "telegram_transport_outbox_test")
    intake = _load(INTAKE_SCRIPT, "telegram_transport_intake_test")
    monkeypatch.setattr(outbox, "_utc_now", lambda: now + timedelta(seconds=2))
    monkeypatch.setattr(intake, "_utc_now", lambda: now + timedelta(seconds=3))

    outbox_dir = tmp_path / "outbox"
    monkeypatch.setenv("BP_TELEGRAM_TRANSPORT_OUTBOX_ENABLED", "yes")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(OUTBOX_SCRIPT),
            str(prepared_path),
            str(approval_path),
            "--outbox-dir",
            str(outbox_dir),
        ],
    )
    assert outbox.main() == 0
    outbox_result = json.loads(capsys.readouterr().out)
    assert outbox_result["network_send_attempted"] is False
    assert outbox_result["real_order_submitted"] is False

    envelope_path = Path(outbox_result["envelope_path"])
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    assert "telegram_user_id" not in envelope["approval"]
    assert "telegram_chat_id" not in envelope["approval"]
    assert "callback_query_id" not in envelope["approval"]

    claim_dir = tmp_path / "claims"
    monkeypatch.setenv("BP_TELEGRAM_TRANSPORT_INTAKE_ENABLED", "yes")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(INTAKE_SCRIPT),
            str(envelope_path),
            "--state-dir",
            str(claim_dir),
        ],
    )
    assert intake.main() == 0
    intake_result = json.loads(capsys.readouterr().out)
    assert intake_result["executor_invoked"] is False
    assert intake_result["real_order_submitted"] is False

    claimed_prepared = json.loads(
        Path(intake_result["prepared_path"]).read_text(encoding="utf-8")
    )
    claimed_approval = json.loads(
        Path(intake_result["approval_path"]).read_text(encoding="utf-8")
    )
    assert claimed_prepared["intent_id"] == "live-intent-loopback"
    assert claimed_approval["intent_id"] == "live-intent-loopback"
    assert "telegram_user_id" not in claimed_approval

    with pytest.raises(TransportError, match="already claimed"):
        intake.main()


def test_transport_outbox_rejects_disabled_or_secret_bearing_runtime(
    tmp_path,
    monkeypatch,
) -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    prepared_path, approval_path = _write_inputs(tmp_path, now)
    key = encode_transport_key(bytes(range(32)))
    _zero_money_env(monkeypatch, key)
    outbox = _load(OUTBOX_SCRIPT, "telegram_transport_outbox_guard_test")
    monkeypatch.setattr(outbox, "_utc_now", lambda: now + timedelta(seconds=2))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(OUTBOX_SCRIPT),
            str(prepared_path),
            str(approval_path),
            "--outbox-dir",
            str(tmp_path / "outbox"),
        ],
    )

    with pytest.raises(SystemExit, match="not enabled"):
        outbox.main()

    monkeypatch.setenv("BP_TELEGRAM_TRANSPORT_OUTBOX_ENABLED", "yes")
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "must-not-be-here")
    with pytest.raises(SystemExit, match="POLYMARKET_PRIVATE_KEY"):
        outbox.main()
