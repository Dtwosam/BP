from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / "scripts" / "deploy" / "phase15_v3_fast_live_stage_cloudshell.sh"


def test_fast_live_stage_installer_cannot_activate_or_authorize() -> None:
    text = STAGE.read_text(encoding="utf-8")
    for marker in (
        "AUTHORIZATION_CREATED=false",
        "PROJECT_STATE_STAGED=false",
        "SERVICES_STARTED=false",
        "SERVICES_ENABLED=false",
        "PUBSUB_RESOURCES_MUTATED=false",
        "KILL_SWITCH_REMOVED=false",
        "REAL_ORDER_SUBMITTED=false",
        "EXECUTOR_KILL_SWITCH_ENGAGED=true",
        "TELEGRAM_APPROVAL_RELEASE_STAGED=true",
        "TELEGRAM_APPROVAL_RESTARTED=false",
        "RECORDER_TELEGRAM_APPROVAL_RELEASE_STAGED=true",
        "RECORDER_TELEGRAM_APPROVAL_RESTARTED=false",
        "[[ ! -e /etc/bp-fast-live/authorization.json ]]",
        "[[ ! -e /etc/bp-fast-live/PROJECT_STATE.json ]]",
        "[[ ! -e /etc/bp-fast-live/transport.key ]]",
    ):
        assert marker in text

    forbidden = (
        "systemctl start bp-phase15-fast-live",
        "systemctl enable bp-phase15-fast-live",
        "rm -f /var/lib/bp-canary/fast-live/KILL",
        "gcloud pubsub topics create",
        "gcloud pubsub subscriptions create",
        "post_order(",
        "create_limit_order(",
    )
    for marker in forbidden:
        assert marker not in text


def test_fast_live_stage_installs_exact_release_and_full_runtime() -> None:
    text = STAGE.read_text(encoding="utf-8")
    for marker in (
        "phase15-v3-fast-live-release-v1",
        "phase15-fast-live-executor-requirements.txt",
        '"$venv/bin/pip" install --disable-pip-version-check --no-input "$release"',
        'version("polymarket-client") == "0.7.1"',
        'version("google-cloud-pubsub") == "2.41.0"',
        'version("websockets") == "15.0.1"',
        "import bp_engine.execution.fast_live_executor",
        'rm -rf "$venv"',
        'uv_version="0.12.19"',
        'python_version="3.12.14"',
        'UV_PYTHON_INSTALL_DIR="$managed_python_dir"',
        'UV_MANAGED_PYTHON=1',
        '"$bootstrap_venv/bin/uv" venv',
        '--seed',
        "sys.version_info[:3] == (3, 12, 14)",
        "RECORDER_FAST_LIVE_PYTHON=3.12.14",
        'runuser -u bp -- env PYTHONPATH="$release/src" "$venv/bin/python"',
        'telegram_root=/opt/bp-phase15-telegram-approval',
        'telegram_release="$telegram_root/releases/$head"',
        "run_phase15_v3_canary_telegram_approval.py",
        "bp-phase15-canary-telegram-approval.service",
        'PYTHONPATH="$telegram_release/src"',
    ):
        assert marker in text


def test_fast_live_stage_does_not_restart_telegram_listener() -> None:
    text = STAGE.read_text(encoding="utf-8")
    assert "systemctl restart bp-phase15-canary-telegram-approval.service" not in text
    assert "systemctl start bp-phase15-canary-telegram-approval.service" not in text
