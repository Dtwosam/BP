from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts/deploy/phase14_prospective_runtime_install.sh"
CLOUDSHELL = ROOT / "scripts/deploy/phase14_prospective_runtime_cloudshell.sh"
PREDICTOR_UNIT = ROOT / "deploy/bp-live-predictor.service"
OUTCOME_UNIT = ROOT / "deploy/bp-prospective-outcomes.service"
CI = ROOT / ".github/workflows/ci.yml"

BASE_ENV_FILE = "EnvironmentFile=/etc/bp/bp.env"
SAFETY_ENV_FILE = "EnvironmentFile=/etc/bp/bp-prospective-runtime-safety.env"
SAFETY_LINES = (
    "Environment=MODE=research",
    "Environment=LIVE_TRADING_ENABLED=false",
    "Environment=MAX_TRADE_SIZE_USD=0",
    "Environment=MAX_DAILY_LOSS_USD=0",
)
CORE_SERVICES = (
    "bp-recorder.service",
    "bp-postgres.service",
    "bp-dashboard-api.service",
    "bp-dashboard-web.service",
    "bp-paper-execution.service",
)


def test_predictor_unit_pins_research_zero_money_boundary() -> None:
    content = PREDICTOR_UNIT.read_text()
    for line in SAFETY_LINES:
        assert line in content
    assert "User=bp" in content
    assert "Group=bp" in content
    assert BASE_ENV_FILE in content
    assert "NoNewPrivileges=true" in content
    assert "ProtectHome=true" in content
    assert "ProtectSystem=full" in content
    assert "-m bp_engine.live_prediction run" in content


def test_units_load_root_controlled_safety_file_after_mutable_environment() -> None:
    for unit in (PREDICTOR_UNIT, OUTCOME_UNIT):
        content = unit.read_text()
        assert SAFETY_ENV_FILE in content
        assert content.index(SAFETY_ENV_FILE) > content.index(BASE_ENV_FILE)

    installer = INSTALLER.read_text()
    assert 'SAFETY_ENV_FILE="/etc/bp/bp-prospective-runtime-safety.env"' in installer
    assert "HAD_SAFETY_ENV_FILE" in installer
    assert "SAFETY_ENV_FILE_WAS_MODE" in installer
    assert "install -m 0644 \"$SAFETY_ENV_STAGE\" \"$SAFETY_ENV_FILE\"" in installer
    assert 'printf \'%s\\n\' \\' in installer
    for assignment in (
        "MODE=research",
        "LIVE_TRADING_ENABLED=false",
        "MAX_TRADE_SIZE_USD=0",
        "MAX_DAILY_LOSS_USD=0",
    ):
        assert assignment in installer


def test_permanent_installer_is_exact_head_rollback_capable_and_two_daemon_only() -> None:
    assert INSTALLER.is_file(), INSTALLER
    content = INSTALLER.read_text()

    assert "set -Eeuo pipefail" in content
    assert "EUID" in content
    assert "EXPECTED_HEAD" in content
    assert "^[0-9a-f]{40}$" in content
    assert "git -C \"$BP_ROOT\" status --porcelain" in content
    assert "git -C \"$CANDIDATE_ROOT\" rev-parse HEAD" in content
    assert "git -C \"$BP_ROOT\" checkout --detach --force \"$EXPECTED_HEAD\"" in content

    for key in ("MODE", "LIVE_TRADING_ENABLED", "MAX_TRADE_SIZE_USD", "MAX_DAILY_LOSS_USD"):
        assert key in content
    assert '"$MODE" != "research"' in content
    assert '"$LIVE_TRADING_ENABLED" != "false"' in content
    assert '"$MAX_TRADE_SIZE_USD" != "0"' in content
    assert '"$MAX_DAILY_LOSS_USD" != "0"' in content

    for service in CORE_SERVICES:
        assert service in content
        assert f"systemctl stop {service}" not in content
        assert f"systemctl restart {service}" not in content

    for unit in ("bp-live-predictor.service", "bp-prospective-outcomes.service"):
        assert unit in content
    assert "rollback_phase14_prospective_runtime" in content
    assert "trap cleanup EXIT" in content
    assert "PREDICTOR_WAS_ACTIVE" in content
    assert "PREDICTOR_WAS_ENABLED" in content
    assert "OUTCOME_WAS_ACTIVE" in content
    assert "OUTCOME_WAS_ENABLED" in content
    assert "OLD_HEAD" in content
    assert "OLD_REF" in content
    assert "systemctl daemon-reload" in content
    assert "systemctl enable --now \"$PREDICTOR_UNIT_NAME\"" in content
    assert "systemctl enable --now \"$OUTCOME_UNIT_NAME\"" in content

    assert "bp_engine.live_prediction" in content
    assert "bp_engine.prospective_outcomes" in content
    assert "http://127.0.0.1:8787/api/v1/snapshot" in content
    assert 'mode.get("trading_mode") != "RESEARCH"' in content
    assert 'mode.get("live_trading_enabled") is not False' in content
    assert 'mode.get("execution_available") is not False' in content
    assert 'mode.get("paper_execution_available") is not True' in content

    assert "/var/lib/bp/evidence/phase14-prospective-runtime-install-" in content
    assert "PHASE14_PROSPECTIVE_RUNTIME_INSTALL=PASS" in content

    lowered = content.lower()
    for forbidden in (
        "pip install",
        "alembic",
        "wallet",
        "private_key",
        "private-key",
        "place_order",
        "submit_order",
        "live_trading_enabled=true",
    ):
        assert forbidden not in lowered


def test_candidate_units_are_validated_before_install() -> None:
    assert INSTALLER.is_file(), INSTALLER
    content = INSTALLER.read_text()
    for line in SAFETY_LINES:
        assert line in content
    assert "PREDICTOR_UNIT_SRC" in content
    assert "OUTCOME_UNIT_SRC" in content
    assert "validate_unit" in content
    assert "User=bp" in content
    assert "Group=bp" in content
    assert "NoNewPrivileges=true" in content
    assert "ProtectHome=true" in content
    assert "ProtectSystem=full" in content

    outcome_content = OUTCOME_UNIT.read_text()
    for line in SAFETY_LINES:
        assert line in outcome_content


def test_cloudshell_wrapper_verifies_remote_branch_sha_and_runs_candidate_installer() -> None:
    assert CLOUDSHELL.is_file(), CLOUDSHELL
    content = CLOUDSHELL.read_text()

    assert "PHASE14_PROSPECTIVE_RUNTIME_HEAD" in content
    assert "phase14-prospective-outcome-sync" in content
    assert "^[0-9a-f]{40}$" in content
    assert "gcloud auth list" in content
    assert "git -C /opt/bp fetch --no-tags origin" in content
    assert "refs/remotes/origin/$BRANCH" in content
    assert '"$FETCHED" != "$SHA"' in content
    assert "git -C /opt/bp status --porcelain" in content
    assert "git -C /opt/bp worktree add --detach" in content
    assert "phase14_prospective_runtime_install.sh" in content
    assert "PHASE14_PROSPECTIVE_RUNTIME_INSTALL=PASS" in content

    for service in (*CORE_SERVICES, "bp-live-predictor.service", "bp-prospective-outcomes.service"):
        assert service in content
    assert "is-enabled" in content
    for key in ("MODE", "LIVE_TRADING_ENABLED", "MAX_TRADE_SIZE_USD", "MAX_DAILY_LOSS_USD"):
        assert key in content

    lowered = content.lower()
    for forbidden in (
        "wallet",
        "private_key",
        "private-key",
        "place_order",
        "submit_order",
        "live_trading_enabled=true",
    ):
        assert forbidden not in lowered


def test_ci_syntax_checks_permanent_runtime_scripts() -> None:
    content = CI.read_text()
    assert "bash -n scripts/deploy/phase14_prospective_runtime_install.sh" in content
    assert "bash -n scripts/deploy/phase14_prospective_runtime_cloudshell.sh" in content
