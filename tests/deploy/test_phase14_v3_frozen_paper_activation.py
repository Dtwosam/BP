import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "deploy" / "phase14_v3_frozen_paper_install.sh"
CLOUDSHELL = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v3_frozen_paper_rollout_cloudshell.sh"
)
LEGACY = ROOT / "deploy" / "bp-paper-execution-v1-isolated.service"
PREDICTOR = ROOT / "deploy" / "bp-v3-frozen-predictor.service"
EXECUTOR = ROOT / "deploy" / "bp-v3-paper-execution.service"


def test_v3_paper_units_are_research_zero_money_and_local_only() -> None:
    for path in (LEGACY, PREDICTOR, EXECUTOR):
        content = path.read_text(encoding="utf-8")
        for marker in (
            "User=bp",
            "Group=bp",
            "Environment=MODE=research",
            "Environment=LIVE_TRADING_ENABLED=false",
            "Environment=MAX_TRADE_SIZE_USD=0",
            "Environment=MAX_DAILY_LOSS_USD=0",
            "IPAddressDeny=any",
            "IPAddressAllow=localhost",
            "NoNewPrivileges=true",
            "/var/lib/bp/runtime/v3-paper-current",
        ):
            assert marker in content
        lowered = content.lower()
        for forbidden in (
            "private_key",
            "wallet",
            "live_trading_enabled=true",
            "submit_order",
            "order-placement",
        ):
            assert forbidden not in lowered


def test_installer_is_exact_model_exact_head_and_recorder_safe() -> None:
    content = INSTALLER.read_text(encoding="utf-8")
    required = (
        "PHASE14_V3_PAPER_HEAD",
        "exact 40-character verified main SHA",
        "124627e15cab3997b8abe54ec5237450d976ab5682953f45a1399a76b6dae0e7",
        "prefetched_remote_branch_head_mismatch",
        'git -c safe.directory="$REPO"',
        "existing_activation_manifest_mismatch",
        "ACTIVATION_SOURCE",
        '--activation "$ACTIVATION_SOURCE"',
        "paper_starting_cash_usd",
        '"5.00"',
        "real_money_usd",
        "bp-paper-execution.service",
        "bp-v3-frozen-predictor.service",
        "bp-v3-paper-execution.service",
        "bp-prospective-outcomes.service",
        "bp-v4-forward-coverage.timer",
        "RECORDER_PID_BEFORE",
        "RECORDER_PID_AFTER",
        "recorder_pid_changed",
        "deployed_checkout_changed",
        "pre-activation V3 paper prediction found",
        "invalid_v3_order_source_count",
        "model_refit_performed",
        "threshold_tuning_performed",
        "live_order_path_enabled",
        "PHASE14_V3_FROZEN_PAPER_ROLLOUT=PASS",
    )
    for marker in required:
        assert marker in content

    lowered = content.lower()
    for forbidden in (
        "systemctl restart bp-recorder",
        "systemctl stop bp-recorder",
        "live_trading_enabled=true",
        "delete from live_predictions",
        "delete from paper_",
        "truncate ",
        "git -c $repo checkout",
        "git -c \"$repo\" checkout",
    ):
        assert forbidden not in lowered


def test_installer_replaces_legacy_filter_before_starting_v3_services() -> None:
    content = INSTALLER.read_text(encoding="utf-8")
    legacy_install = content.index("bp-paper-execution-v1-isolated.service")
    legacy_restart = content.index('systemctl restart "$LEGACY_UNIT"')
    v3_start = content.index('systemctl enable --now "$PREDICTOR_UNIT"')
    assert legacy_install < legacy_restart < v3_start


def test_installer_has_clean_bash_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(INSTALLER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_installer_runtime_requires_v3_reporting_assets() -> None:
    content = INSTALLER.read_text(encoding="utf-8")
    for marker in (
        "src/bp_engine/v3_paper/report.py",
        "src/bp_engine/v3_paper/report_cli.py",
        "scripts/report_v3_paper.py",
    ):
        assert marker in content


def test_cloudshell_launcher_is_exact_head_safe_directory_and_checkout_preserving() -> None:
    content = CLOUDSHELL.read_text(encoding="utf-8")
    for marker in (
        "PHASE14_V3_PAPER_HEAD",
        "exact 40-character verified main SHA",
        "project-4397f2c0-7098-4c1c-abb",
        "us-east1-c",
        "bp-recorder",
        'git -c safe.directory="$REPO"',
        'git_repo fetch --quiet origin "refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"',
        '[[ "$REMOTE_HEAD" == "$SHA" ]]',
        'git_repo show "$SHA:$INSTALLER_PATH"',
        "phase14_v3_frozen_paper_install.sh",
        'DEPLOYED_HEAD_BEFORE=$(git_repo rev-parse HEAD)',
        'DEPLOYED_HEAD_AFTER=$(git_repo rev-parse HEAD)',
        "deployed_checkout_changed_by_launcher",
        "gcloud compute ssh",
        "--project",
        "--zone",
        "sudo env",
        "PHASE14_V3_PAPER_CLOUDSHELL=PASS",
    ):
        assert marker in content

    lowered = content.lower()
    for forbidden in (
        "git checkout",
        "git reset --hard",
        "systemctl restart bp-recorder",
        "live_trading_enabled=true",
    ):
        assert forbidden not in lowered


def test_cloudshell_launcher_has_clean_bash_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(CLOUDSHELL)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_installer_verifies_new_activation_before_persisting_it() -> None:
    content = INSTALLER.read_text(encoding="utf-8")
    verify = content.index('--activation "$ACTIVATION_SOURCE"')
    persist = content.index(
        'install -o bp -g bp -m 0440 "$ACTIVATION_TMP" "$ACTIVATION_TARGET"'
    )
    assert verify < persist
    assert 'ACTIVATION_SOURCE="$ACTIVATION_TARGET"' in content
    assert 'ACTIVATION_SOURCE="$ACTIVATION_TMP"' in content


def test_new_activation_temp_is_readable_by_bp_before_verification() -> None:
    content = INSTALLER.read_text(encoding="utf-8")
    chown = content.index('chown bp:bp "$ACTIVATION_TMP"')
    chmod = content.index('chmod 0440 "$ACTIVATION_TMP"')
    verify = content.index('--activation "$ACTIVATION_SOURCE"')
    assert chown < verify
    assert chmod < verify


def test_db_verify_temp_is_writable_by_bp_before_db_verification() -> None:
    content = INSTALLER.read_text(encoding="utf-8")
    make = content.index(
        "VERIFY_JSON=$(mktemp /var/tmp/bp-v3-paper-db-verify.XXXXXX.json)"
    )
    chown = content.index('chown bp:bp "$VERIFY_JSON"')
    chmod = content.index('chmod 0640 "$VERIFY_JSON"')
    verify = content.index('"$ENV_FILE" "$ACTIVATION_TARGET" "$VERIFY_JSON"')
    assert make < chown < verify
    assert make < chmod < verify


def test_activation_epoch_is_preserved_after_predictor_has_started() -> None:
    content = INSTALLER.read_text(encoding="utf-8")
    assert "SERVICES_STARTED=0" in content
    assert 'SERVICES_STARTED=1' in content
    assert "ACTIVATION_INSTALLED && SERVICES_STARTED == 0" in content
    assert "activation manifest preserved because V3 predictor started" in content


def test_missing_manifest_recovers_conservative_epoch_from_preserved_v3_rows() -> None:
    content = INSTALLER.read_text(encoding="utf-8")
    required = (
        "recovered_activated_at",
        "recovered_from_orphaned_rows",
        "recovered_prediction_count",
        "func.min(schema.live_predictions.c.market_start_at)",
        'schema.live_predictions.c.prediction_version == "v3-frozen-paper-v1"',
        "orphaned V3 paper orders exist without V3 predictions",
    )
    for marker in required:
        assert marker in content


def test_existing_activation_manifest_allows_runtime_hotfix_head() -> None:
    content = INSTALLER.read_text(encoding="utf-8")
    existing_block = content.split('if [[ -f "$ACTIVATION_TARGET" ]]', 1)[1].split(
        "else", 1
    )[0]
    assert '"candidate_head": candidate_head' not in existing_block
    assert "existing activation candidate head is invalid" in existing_block
