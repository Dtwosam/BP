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
        "[[ ! -e /etc/bp-fast-live/authorization.json ]]",
        "[[ ! -e /etc/bp-fast-live/PROJECT_STATE.json ]]",
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
    ):
        assert marker in text
