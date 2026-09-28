from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACTIVATE = ROOT / "scripts" / "deploy" / "phase15_v3_fast_live_activate_cloudshell.sh"
SOURCE = ROOT / "scripts" / "run_phase15_v3_fast_live_source.py"
RECEIVER = ROOT / "scripts" / "run_phase15_v3_fast_live_receiver.py"


def test_fast_live_activation_helper_is_shell_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(ACTIVATE)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_fast_live_activation_requires_fresh_one_shot_authorization() -> None:
    text = ACTIVATE.read_text(encoding="utf-8")
    for marker in (
        "I_ACCEPT_ONE_REAL_MONEY_ATTEMPT",
        "verify_source_authorization",
        "verify_runtime_authorization",
        "AUTHORIZED_NOT_CONSUMED",
        "max_network_submission_attempts",
        "runtime_window_exceeds_one_hour",
        '[[ "$HEAD" == "$REMOTE_MAIN" ]]',
        "working_tree_not_clean",
    ):
        assert marker in text
    assert "PROJECT_STATE.json" in text
    assert "jq" not in text
    assert "update_ref" not in text
    assert "git commit" not in text


def test_fast_live_activation_uses_fresh_per_authorization_transport() -> None:
    text = ACTIVATE.read_text(encoding="utf-8")
    for marker in (
        'AUTH_SUFFIX="$(python3 - "$AUTH_ID"',
        'ORDER_TOPIC="bp-phase15-fast-live-orders-$AUTH_SUFFIX"',
        'ORDER_SUB="bp-phase15-fast-live-orders-$AUTH_SUFFIX-jhb"',
        'RESULT_TOPIC="bp-phase15-fast-live-results-$AUTH_SUFFIX"',
        'RESULT_SUB="bp-phase15-fast-live-results-$AUTH_SUFFIX-us"',
        'roles/pubsub.publisher',
        'roles/pubsub.subscriber',
    ):
        assert marker in text
    assert text.count("gcloud pubsub topics add-iam-policy-binding") == 2
    assert text.count("gcloud pubsub subscriptions add-iam-policy-binding") == 2


def test_fast_live_activation_starts_receiver_armed_then_releases_and_starts_source() -> None:
    text = ACTIVATE.read_text(encoding="utf-8")
    receiver_start = text.index(
        "sudo systemctl start bp-phase15-fast-live-receiver.service"
    )
    kill_release = text.index(
        "sudo rm -f /var/lib/bp-canary/fast-live/KILL"
    )
    source_start = text.index(
        "sudo systemctl start bp-phase15-fast-live-source.service"
    )
    assert receiver_start < kill_release < source_start

    for marker in (
        "safe_stop()",
        "fast-live-activation-safe-stop",
        "sudo systemctl stop bp-phase15-fast-live-receiver.service",
        "sudo systemctl stop bp-phase15-fast-live-source.service",
        "sudo test ! -e /var/lib/bp-canary/fast-live/attempt.json",
        "sudo test ! -e /var/lib/bp-canary/fast-live/result.json",
        "clean_for_canary",
        'geo.get("country") != "ZA"',
        "open_orders != 0",
        'collateral < Decimal("5")',
    ):
        assert marker in text

    assert "systemctl enable bp-phase15-fast-live" not in text
    assert "systemctl enable --now bp-phase15-fast-live" not in text


def test_fast_live_services_self_expire_after_authorization_or_attempt() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    receiver = RECEIVER.read_text(encoding="utf-8")

    assert "runtime_expires_at" in source
    assert "fast_live_authorization_expired" in source
    assert "if observed >= runtime_expires_at:" in source

    assert "runtime_expires_at" in receiver
    assert "terminal_event = threading.Event()" in receiver
    assert 'result.get("network_submission_attempt_consumed") is True' in receiver
    assert "terminal_event.set()" in receiver
    assert "if _utc_now() >= runtime_expires_at:" in receiver
    assert "future.cancel()" in receiver
