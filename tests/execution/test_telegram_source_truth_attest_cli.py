from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

from bp_engine.execution.telegram_approval import approval_record, new_pending
from bp_engine.execution.telegram_origin_attestation import (
    create_origin_attestation,
    encode_origin_key,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_phase15_v3_telegram_source_truth_attest.py"
STATE = ROOT / "PROJECT_STATE.json"
ORIGIN_KEY_ID = "phase15-telegram-origin-v1"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "telegram_source_truth_attest_cli_test",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prepared(now: datetime) -> dict[str, object]:
    return {
        "status": "prepared",
        "action": "submit",
        "intent_id": "live-intent-source-truth-cli",
        "prediction_id": "prediction-source-truth-cli",
        "paper_order_id": "paper-source-truth-cli",
        "market_end_at": (now + timedelta(seconds=55)).isoformat(),
        "request": {
            "selected_side": "down",
            "limit_price": "0.72",
            "requested_shares": "6.81",
            "target_notional_usd": "5",
            "token_id": "token-source-truth-cli",
            "action": "BUY",
        },
        "policy": {
            "policy_version": "v3-live-canary-v1",
            "max_submission_attempts": 1,
        },
    }


def _approval(prepared: dict[str, object], now: datetime) -> dict[str, object]:
    pending = new_pending(
        prepared,
        telegram_user_id=111,
        telegram_chat_id=111,
        created_at=now,
        nonce="source-truth-cli-nonce",
    )
    return approval_record(
        action="approve",
        pending=pending,
        callback_query_id="source-truth-cli-callback",
        approved_at=now + timedelta(seconds=1),
    )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)


def _clear_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
        "BP_TELEGRAM_BOT_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_source_truth_attest_cli_writes_current_blocked_proof_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load()
    _clear_secrets(monkeypatch)
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    prepared = _prepared(now)
    approval = _approval(prepared, now)
    key = bytes(range(32, 64))
    origin = create_origin_attestation(
        prepared,
        approval=approval,
        key=key,
        key_id=ORIGIN_KEY_ID,
        attested_at=now + timedelta(seconds=2),
    )

    prepared_path = tmp_path / "prepared.json"
    approval_path = tmp_path / "approval.json"
    origin_path = tmp_path / "origin.json"
    key_path = tmp_path / "origin.key"
    output_dir = tmp_path / "out"
    output_dir.mkdir(mode=0o700)
    output = output_dir / "source-truth.json"
    _write_json(prepared_path, prepared)
    _write_json(approval_path, approval)
    _write_json(origin_path, origin)
    key_path.write_text(encode_origin_key(key) + "\n", encoding="utf-8")
    key_path.chmod(0o600)

    monkeypatch.setattr(module, "_utc_now", lambda: now + timedelta(seconds=3))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--project-state",
            str(STATE),
            "--prepared",
            str(prepared_path),
            "--approval",
            str(approval_path),
            "--origin-attestation",
            str(origin_path),
            "--origin-key-file",
            str(key_path),
            "--origin-key-id",
            ORIGIN_KEY_ID,
            "--output",
            str(output),
        ],
    )

    assert module.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "source_truth_authorization_written"
    assert result["authorized"] is False
    assert "second_order_not_authorized" in result["blockers"]
    assert result["network_action_performed"] is False
    assert result["executor_invoked"] is False
    assert result["real_order_submitted"] is False
    assert output.is_file()
    assert (os.stat(output).st_mode & 0o777) == 0o600

    assert module.main() == 1
    replay = json.loads(capsys.readouterr().out)
    assert replay["status"] == "failed_closed"
    assert "already exists" in replay["error"]


def test_source_truth_attest_cli_rejects_weak_or_symlink_inputs(
    tmp_path: Path,
) -> None:
    module = _load()
    payload = tmp_path / "payload.json"
    _write_json(payload, {"a": 1})
    payload.chmod(0o644)
    with pytest.raises(
        module.SourceTruthAuthorizationError,
        match="mode must be one of",
    ):
        module._load_json(
            payload,
            label="prepared payload",
            modes=(0o600, 0o640),
        )

    payload.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(payload)
    with pytest.raises(
        module.SourceTruthAuthorizationError,
        match="non-symlink",
    ):
        module._load_json(
            link,
            label="prepared payload",
            modes=(0o600, 0o640),
        )


def test_source_truth_attest_cli_rejects_secret_bearing_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load()
    _clear_secrets(monkeypatch)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "must-not-be-here")
    with pytest.raises(SystemExit, match="POLYMARKET_PRIVATE_KEY"):
        module._require_safe_runtime()


def test_source_truth_attest_cli_has_no_network_or_execution_path() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    compile(text, str(SCRIPT), "exec")
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
    ):
        assert forbidden not in text
