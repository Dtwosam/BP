from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

from bp_engine.execution.telegram_approval import approval_record, new_pending

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_phase15_v3_canary_telegram_approval.py"


def _load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("phase15_telegram_runner", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prepared(now: datetime) -> dict[str, object]:
    return {
        "status": "prepared",
        "action": "submit",
        "intent_id": "live-intent-123",
        "prediction_id": "prediction-123",
        "paper_order_id": "paper-123",
        "market_end_at": (now + timedelta(seconds=50)).isoformat(),
        "request": {
            "selected_side": "down",
            "limit_price": "0.72",
            "requested_shares": "6.81",
            "target_notional_usd": "5",
            "token_id": "token-123",
            "action": "BUY",
        },
        "policy": {
            "policy_version": "v3-live-canary-v1",
            "max_submission_attempts": 1,
        },
    }


def _write_bound_files(state_dir: Path, now: datetime) -> tuple[Path, Path]:
    prepared = _prepared(now)
    pending = new_pending(
        prepared,
        telegram_user_id=111,
        telegram_chat_id=222,
        created_at=now,
        nonce="nonce123",
    )
    approval = approval_record(
        action="approve",
        pending=pending,
        callback_query_id="cb-1",
        approved_at=now + timedelta(seconds=1),
    )
    prepared_path = state_dir / "prepared.json"
    approval_path = state_dir / "approval.json"
    state_dir.mkdir(parents=True)
    prepared_path.write_text(json.dumps(prepared), encoding="utf-8")
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    return prepared_path, approval_path


def test_approved_handoff_runs_once_and_restart_does_not_retry(tmp_path, monkeypatch) -> None:
    runner = _load_runner()
    now = datetime(2026, 9, 24, 20, 0, tzinfo=UTC)
    monkeypatch.setattr(runner, "_utc_now", lambda: now + timedelta(seconds=2))
    state_dir = tmp_path / "state"
    prepared_path, approval_path = _write_bound_files(state_dir, now)

    marker = tmp_path / "invocations.txt"
    environment_marker = tmp_path / "environment.json"
    monkeypatch.setenv("BP_TELEGRAM_BOT_TOKEN", "test-token-must-not-propagate")
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "test-key-must-not-propagate")
    command = tmp_path / "handoff.py"
    command.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "from pathlib import Path\n"
        f"path = Path({str(marker)!r})\n"
        f"env_path = Path({str(environment_marker)!r})\n"
        "with path.open('a', encoding='utf-8') as handle:\n"
        "    handle.write('run\\n')\n"
        "env_path.write_text(json.dumps({\n"
        "    'bot_token_present': 'BP_TELEGRAM_BOT_TOKEN' in os.environ,\n"
        "    'private_key_present': 'POLYMARKET_PRIVATE_KEY' in os.environ,\n"
        "    'approved_intent': os.environ.get('BP_APPROVED_INTENT_ID'),\n"
        "}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    command.chmod(0o755)

    first = runner._dispatch_approved_handoff(
        command=command,
        prepared_path=prepared_path,
        approval_path=approval_path,
        state_dir=state_dir,
    )
    second = runner._dispatch_approved_handoff(
        command=command,
        prepared_path=prepared_path,
        approval_path=approval_path,
        state_dir=state_dir,
    )

    assert first["status"] == "handoff_completed"
    assert second == first
    assert marker.read_text(encoding="utf-8").splitlines() == ["run"]
    child_environment = json.loads(environment_marker.read_text(encoding="utf-8"))
    assert child_environment["bot_token_present"] is False
    assert child_environment["private_key_present"] is False
    assert child_environment["approved_intent"] == "live-intent-123"
    handoff_prepared = state_dir / "handoff-prepared.json"
    assert handoff_prepared.is_file()
    handoff_payload = json.loads(handoff_prepared.read_text(encoding="utf-8"))
    assert handoff_payload["intent_id"] == "live-intent-123"
    assert (state_dir / "handoff-attempt.json").is_file()
    assert (state_dir / "handoff-result.json").is_file()


def test_existing_attempt_without_result_fails_closed_without_retry(tmp_path, monkeypatch) -> None:
    runner = _load_runner()
    now = datetime(2026, 9, 24, 20, 0, tzinfo=UTC)
    monkeypatch.setattr(runner, "_utc_now", lambda: now + timedelta(seconds=2))
    state_dir = tmp_path / "state"
    prepared_path, approval_path = _write_bound_files(state_dir, now)
    (state_dir / "handoff-attempt.json").write_text("{}", encoding="utf-8")

    marker = tmp_path / "should-not-run.txt"
    command = tmp_path / "handoff.py"
    command.write_text(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n",
        encoding="utf-8",
    )
    command.chmod(0o755)

    result = runner._dispatch_approved_handoff(
        command=command,
        prepared_path=prepared_path,
        approval_path=approval_path,
        state_dir=state_dir,
    )

    assert result["status"] == "handoff_ambiguous_no_retry"
    assert result["retry_allowed"] is False
    assert not marker.exists()


def test_approved_handoff_stays_inert_without_configured_command(tmp_path, monkeypatch) -> None:
    runner = _load_runner()
    now = datetime(2026, 9, 24, 20, 0, tzinfo=UTC)
    monkeypatch.setattr(runner, "_utc_now", lambda: now + timedelta(seconds=2))
    state_dir = tmp_path / "state"
    prepared_path, approval_path = _write_bound_files(state_dir, now)

    result = runner._dispatch_approved_handoff(
        command=None,
        prepared_path=prepared_path,
        approval_path=approval_path,
        state_dir=state_dir,
    )

    assert result["status"] == "approved_handoff_not_configured"
    assert result["real_order_submitted"] is False
    assert not (state_dir / "handoff-attempt.json").exists()
    assert (state_dir / "handoff-result.json").is_file()

    command = tmp_path / "late-handoff.py"
    marker = tmp_path / "late-invocation.txt"
    command.write_text(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n",
        encoding="utf-8",
    )
    command.chmod(0o755)
    second = runner._dispatch_approved_handoff(
        command=command,
        prepared_path=prepared_path,
        approval_path=approval_path,
        state_dir=state_dir,
    )
    assert second == result
    assert not marker.exists()
