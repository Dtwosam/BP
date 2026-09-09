from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "deploy" / "bp-v2-gate-b-readiness-watch.service"
TIMER = ROOT / "deploy" / "bp-v2-gate-b-readiness-watch.timer"
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v2_gate_b_readiness_watch_install_cloudshell.sh"
)
SCRIPT = ROOT / "scripts" / "run_v2_gate_b_readiness_watch.py"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def test_readiness_watch_service_is_hardened_feature_only_and_local_db_only() -> None:
    content = SERVICE.read_text(encoding="utf-8")
    required = (
        "Type=oneshot",
        "User=bp",
        "Group=bp",
        "WorkingDirectory=/opt/bp-v2-gate-b-readiness-watch/current",
        "EnvironmentFile=/etc/bp/bp.env",
        "EnvironmentFile=/etc/bp/bp-prospective-runtime-safety.env",
        "EnvironmentFile=/opt/bp-v2-gate-b-readiness-watch/current/REVISION.env",
        "Environment=MODE=research",
        "Environment=LIVE_TRADING_ENABLED=false",
        "Environment=MAX_TRADE_SIZE_USD=0",
        "Environment=MAX_DAILY_LOSS_USD=0",
        "scripts/run_v2_gate_b_readiness_watch.py",
        "--evidence-dir /var/lib/bp/evidence",
        "--status-file /var/lib/bp/status/v2-gate-b-readiness.json",
        "--deployed-root /opt/bp",
        "--expected-helper-head ${BP_V2_GATE_B_READINESS_WATCH_HEAD}",
        "--expected-deployed-head ${BP_V2_GATE_B_READINESS_WATCH_DEPLOYED_HEAD}",
        "Requires=bp-postgres.service",
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "PrivateDevices=true",
        "ProtectHome=true",
        "ProtectSystem=full",
        "ReadOnlyPaths=/opt/bp-v2-gate-b-readiness-watch",
        "ReadOnlyPaths=/var/lib/bp/evidence",
        "ReadWritePaths=/var/lib/bp/status",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        "IPAddressDeny=any",
        "IPAddressAllow=localhost",
        "StandardOutput=journal",
        "StandardError=journal",
    )
    for marker in required:
        assert marker in content

    for forbidden in (
        "OnFailure=bp-storage-critical-stop.service",
        "run_v2_gate_b_research.py",
        "prepare",
        "evaluate-holdout",
        "wallet",
        "private_key",
        "signing",
        "LIVE_TRADING_ENABLED=true",
    ):
        assert forbidden not in content


def test_readiness_watch_timer_is_persistent_two_hour_schedule() -> None:
    content = TIMER.read_text(encoding="utf-8")
    for marker in (
        "OnCalendar=*-*-* 00,02,04,06,08,10,12,14,16,18,20,22:30:00 UTC",
        "AccuracySec=1min",
        "Persistent=true",
        "Unit=bp-v2-gate-b-readiness-watch.service",
        "WantedBy=timers.target",
    ):
        assert marker in content
    assert "OnUnitActiveSec=1min" not in content
    assert "OnBootSec=1min" not in content


def test_readiness_watch_installer_is_exact_main_guarded_and_sidecar_only() -> None:
    content = HELPER.read_text(encoding="utf-8")
    required = (
        "PHASE14_V2_GATE_B_READINESS_WATCH_HEAD",
        "PHASE14_V2_GATE_B_READINESS_WATCH_APPROVED_HEAD",
        "PHASE14_V2_GATE_B_READINESS_WATCH_APPROVED_DEPLOYED_HEAD",
        "watch_install_approval_missing_or_invalid",
        "watch_install_approval_head_mismatch",
        "watch_install_approval_deployed_head_mismatch",
        "watch_install_remote_approval_head_mismatch",
        "watch_install_remote_approval_deployed_head_mismatch",
        '"approval": {',
        '"approved_helper_head": approved_head',
        '"approved_deployed_head": approved_deployed_head',
        "local_helper_head_mismatch",
        "remote_main_changed",
        "unexpected_deployed_head",
        "candidate_archive_sha256_mismatch",
        "storage_evidence_sha256_mismatch",
        "recorder_config_worker_count_not_4",
        "require_no_gate_b_artifacts",
        "/opt/bp-v2-gate-b-readiness-watch/releases/",
        "bp-v2-gate-b-readiness-watch.service",
        "bp-v2-gate-b-readiness-watch.timer",
        "direct_readiness_watch_failed",
        "REVISION.env",
        "BP_V2_GATE_B_READINESS_WATCH_DEPLOYED_HEAD",
        "--expected-helper-head",
        "--expected-deployed-head",
        "watch_service_result_not_success",
        "watch_timer_not_enabled",
        "watch_timer_not_active",
        "PHASE14_V2_GATE_B_READINESS_WATCH_INSTALL=PASS",
        "HOLDOUT_TOUCHED=false",
        "Production installation requires separate explicit authorization.",
    )
    for marker in required:
        assert marker in content

    assert 'git -c safe.directory="$REPO" -C "$REPO" rev-parse HEAD' in content
    assert 'systemctl enable --now "$TIMER_UNIT"' in content
    assert 'systemctl start "$SERVICE_UNIT"' in content
    assert 'ln -sfn "$RELEASE_DIR" "$CURRENT_LINK"' in content

    approval = content.index("watch_install_approval_missing_or_invalid")
    local_repo = content.index("ROOT=$(git rev-parse --show-toplevel")
    gcloud_binary = content.index("command -v gcloud")
    gcloud_auth = content.index("gcloud auth list")
    gcloud_project = content.index('gcloud config set project "$PROJECT"')
    gcloud_scp = content.index('gcloud compute scp "$ARCHIVE"')
    assert approval < local_repo < gcloud_binary < gcloud_auth < gcloud_project < gcloud_scp

    forbidden_patterns = (
        r'git\s+-C\s+"\$REPO"\s+checkout',
        r'git\s+-C\s+"\$REPO"\s+reset',
        r'systemctl\s+(?:start|stop|restart)\s+bp-recorder\.service',
        r'systemctl\s+(?:start|stop|restart)\s+bp-paper-execution\.service',
        r'systemctl\s+(?:start|stop|restart)\s+bp-live-predictor\.service',
        r'LIVE_TRADING_ENABLED=true',
        r'MAX_TRADE_SIZE_USD=[1-9]',
        r'MAX_DAILY_LOSS_USD=[1-9]',
        r'evaluate-holdout',
    )
    for pattern in forbidden_patterns:
        assert re.search(pattern, content) is None


def test_readiness_watch_installer_rolls_back_only_sidecar_state() -> None:
    content = HELPER.read_text(encoding="utf-8")
    for marker in (
        "PHASE14_V2_GATE_B_READINESS_WATCH_ROLLBACK=START",
        "PHASE14_V2_GATE_B_READINESS_WATCH_ROLLBACK=COMPLETE",
        'systemctl disable --now "$TIMER_UNIT"',
        'rm -rf "$RELEASE_DIR"',
        'rm -f "$CURRENT_LINK"',
        'cp -a "$SERVICE_BACKUP" "$SERVICE_PATH"',
        'cp -a "$TIMER_BACKUP" "$TIMER_PATH"',
        "RECORDER_ACTIVE=",
    ):
        assert marker in content


def test_readiness_watch_scripts_have_clean_syntax() -> None:
    bash = subprocess.run(
        ["bash", "-n", str(HELPER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert bash.returncode == 0, bash.stderr

    python = subprocess.run(
        ["python", "-m", "py_compile", str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert python.returncode == 0, python.stderr


def test_ci_validates_readiness_watch_assets() -> None:
    content = CI.read_text(encoding="utf-8")
    assert (
        "bash -n scripts/deploy/phase14_v2_gate_b_readiness_watch_install_cloudshell.sh"
        in content
    )
    assert "python -m py_compile scripts/run_v2_gate_b_readiness_watch.py" in content
