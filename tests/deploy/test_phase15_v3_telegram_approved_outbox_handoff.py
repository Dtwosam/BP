from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HANDOFF = ROOT / "deploy" / "phase15-telegram-approved-outbox-handoff.sh"


def test_approved_outbox_handoff_shell_syntax_is_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(HANDOFF)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_approved_outbox_handoff_is_transport_local_and_fail_closed() -> None:
    text = HANDOFF.read_text(encoding="utf-8")
    for marker in (
        "/opt/bp-telegram-transport/.venv/bin/python",
        "/opt/bp-telegram-transport/current/scripts/"
        "run_phase15_v3_telegram_approved_outbox.py",
        "/opt/bp-telegram-transport/current/src",
        "BP_TELEGRAM_HANDOFF_ENABLED",
        "BP_TELEGRAM_APPROVED_OUTBOX_ENABLED",
        "BP_TELEGRAM_ORIGIN_KEY_FILE",
        "BP_TELEGRAM_ORIGIN_KEY_ID",
        "BP_TELEGRAM_TRANSPORT_KEY_FILE",
        "BP_TELEGRAM_TRANSPORT_KEY_ID",
        "BP_APPROVED_INTENT_ID",
        "BP_APPROVED_REQUEST_SHA256",
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
        "BP_TELEGRAM_BOT_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        assert marker in text

    for forbidden in (
        "gcloud",
        "curl",
        "wget",
        "httpx",
        "requests",
        "executor.sh",
        "phase15_v3_canary_arm",
        "post_order",
        "create_limit_order",
        "cancel_order",
        "PHASE15_ACCEPT_REAL_MONEY",
    ):
        assert forbidden not in text


def test_approved_outbox_handoff_executes_only_verified_transport_python() -> None:
    text = HANDOFF.read_text(encoding="utf-8")
    assert 'exec "$PYTHON" "$SCRIPT" "$1" "$2"' in text
    assert "eval " not in text
    assert "bash -c" not in text
    assert "sh -c" not in text
