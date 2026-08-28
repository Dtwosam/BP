from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UNIT = ROOT / "deploy" / "systemd" / "bp-paper-execution.service"
HOST = ROOT / "scripts" / "deploy" / "phase12_host_acceptance.sh"
CLOUD = ROOT / "scripts" / "deploy" / "phase12_cloudshell_accept.sh"
INSTALL = ROOT / "scripts" / "deploy" / "phase12_install.sh"
ROTATE = ROOT / "scripts" / "deploy" / "phase12_rotate_postgres_password.sh"
REPLAY_INDEX_HELPER = ROOT / "scripts" / "deploy" / "ensure_phase12_replay_indexes.py"
REPLAY_INDEX_MIGRATION = ROOT / "migrations" / "0012_paper_replay_indexes.sql"
RUNBOOK = ROOT / "docs" / "PHASE-12-DEPLOYMENT.md"
CLI = ROOT / "src" / "bp_engine" / "execution" / "cli.py"
MAIN = ROOT / "src" / "bp_engine" / "execution" / "__main__.py"
RUNNER = ROOT / "scripts" / "run_paper_execution.py"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _text(path: Path) -> str:
    assert path.exists(), f"missing Phase 12 deployment asset: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def test_phase12_worker_entrypoint_exists_and_is_paper_only() -> None:
    cli = _text(CLI)
    main = _text(MAIN)
    runner = _text(RUNNER)

    assert "PaperExecutionService" in cli
    assert "--once" in cli
    assert "get_settings" in cli
    assert "LIVE_TRADING_ENABLED" in cli or "live_trading_enabled" in cli
    assert "MAX_TRADE_SIZE_USD" in cli or "max_trade_size_usd" in cli
    assert "MAX_DAILY_LOSS_USD" in cli or "max_daily_loss_usd" in cli
    assert "from bp_engine.execution.cli import main" in main
    assert "from bp_engine.execution.cli import main" in runner
    combined = "\n".join((cli, main, runner))
    for forbidden in ("LiveExecutionGateway", "private_key", "wallet", "signer"):
        assert forbidden not in combined


def test_phase12_systemd_worker_is_hardened_and_money_disabled() -> None:
    unit = _text(UNIT)

    for required in (
        "User=bp",
        "Group=bp",
        "WorkingDirectory=/opt/bp",
        "EnvironmentFile=/etc/bp/bp.env",
        "Environment=MODE=research",
        "Environment=LIVE_TRADING_ENABLED=false",
        "Environment=MAX_TRADE_SIZE_USD=0",
        "Environment=MAX_DAILY_LOSS_USD=0",
        "ExecStart=/opt/bp/.venv/bin/python -m bp_engine.execution",
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "ProtectHome=true",
        "ProtectSystem=full",
    ):
        assert required in unit
    assert "--host" not in unit
    assert "--port" not in unit
    for forbidden in ("PRIVATE_KEY", "WALLET", "FUNDER", "CLOB_API"):
        assert forbidden not in unit.upper()


def test_phase12_acceptance_and_install_fail_closed_and_reconcile() -> None:
    host = _text(HOST)
    install = _text(INSTALL)

    for script, pass_token in (
        (host, "PHASE12_HOST_ACCEPTANCE=PASS"),
        (install, "PHASE12_INSTALL=PASS"),
    ):
        for required in (
            "EXPECTED_HEAD",
            "rev-parse HEAD",
            "MODE",
            "LIVE_TRADING_ENABLED",
            "MAX_TRADE_SIZE_USD",
            "MAX_DAILY_LOSS_USD",
            "bp-recorder.service",
            "bp-postgres.service",
            "bp-dashboard-api.service",
            "bp-dashboard-web.service",
            "paper_orders",
            "paper_fills",
            "paper_settlements",
            "reconciliation",
            pass_token,
        ):
            assert required in script
        for forbidden in ("PRIVATE_KEY", "WALLET_PRIVATE", "LiveExecutionGateway"):
            assert forbidden not in script

    assert "phase12-paper-execution" in host
    assert "paper_once() {" in host
    before = host.index("before_fingerprint=$(fingerprint_target)")
    rerun = host.index("paper_once", before)
    after = host.index("after_fingerprint=$(fingerprint_target)", rerun)
    assert before < rerun < after
    assert "IDEMPOTENT_RERUN=PASS" in host
    assert "systemctl restart bp-recorder.service" not in host
    assert "systemctl stop bp-recorder.service" not in host
    assert "systemctl restart bp-recorder.service" not in install
    assert "systemctl stop bp-recorder.service" not in install


def test_phase12_host_acceptance_starts_exact_candidate_predictor() -> None:
    host = _text(HOST)

    for required in (
        "bp_engine.live_prediction",
        "run",
        "--source-calibration-run-id",
        "phase9-300-c9f0e00eb7836af08008c66909f8f179",
        "phase9-900-15c234f25588b23cce73a12f87a2e2ea",
        "PREDICTOR_PID",
        "predictor.log",
    ):
        assert required in host
    assert 'kill "$PREDICTOR_PID"' in host
    assert 'wait "$PREDICTOR_PID"' in host


def test_phase12_replay_indexes_are_production_safe_and_query_shaped() -> None:
    migration = _text(REPLAY_INDEX_MIGRATION)
    helper = _text(REPLAY_INDEX_HELPER)

    for required in (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS",
        "ix_raw_market_events_pm_book_replay_anchor",
        "(instrument, asset_id, received_at DESC, id DESC)",
        "source = 'polymarket'",
        "stream = 'market'",
        "event_type = 'book'",
        "ix_raw_market_events_pm_price_change_replay",
        "(instrument, received_at, id)",
        "event_type = 'price_change'",
    ):
        assert required in migration

    for required in (
        "get_settings",
        "AUTOCOMMIT",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS",
        "PHASE12_REPLAY_INDEXES=READY",
    ):
        assert required in helper
    assert "DATABASE_URL" not in helper


def test_phase12_paper_once_is_hard_bounded_on_host_and_install() -> None:
    host = _text(HOST)
    install = _text(INSTALL)

    for script in (host, install):
        assert "PAPER_ONCE_TIMEOUT_SECONDS" in script
        assert "timeout --signal=TERM --kill-after=10s" in script
        assert "bp_engine.execution --once" in script
    assert "REASON=paper_once_timeout" in host


def test_phase12_deployment_applies_replay_indexes_before_paper_once() -> None:
    host = _text(HOST)
    install = _text(INSTALL)

    helper_name = "ensure_phase12_replay_indexes.py"
    for script in (host, install):
        assert helper_name in script
        assert script.index(helper_name) < script.index("bp_engine.execution --once")


def test_phase12_deployment_keeps_database_url_out_of_process_arguments() -> None:
    host = _text(HOST)
    install = _text(INSTALL)

    for script in (host, install):
        assert 'DATABASE_URL="$DATABASE_URL"' not in script
        assert '--database-url "$DATABASE_URL"' not in script
        assert "BP_ENV_FILE" in script
        assert "DATABASE_URL=$(read_env DATABASE_URL)" not in script


def test_phase12_credential_rotation_is_atomic_and_does_not_echo_secret() -> None:
    rotate = _text(ROTATE)

    for required in (
        "openssl rand -hex",
        "ALTER ROLE",
        "POSTGRES_PASSWORD",
        "DATABASE_URL",
        "os.replace",
        "systemctl restart bp-recorder.service",
        "systemctl restart bp-dashboard-api.service",
        "systemctl is-active bp-recorder.service",
        "systemctl is-active bp-dashboard-api.service",
        "PHASE12_POSTGRES_PASSWORD_ROTATION=PASS",
        'encoded_username = quote(parsed.username, safe="")',
    ):
        assert required in rotate
    for forbidden in (
        "echo $NEW_PASSWORD",
        "echo \"$NEW_PASSWORD\"",
        "printf '%s\\n' \"$NEW_PASSWORD\"",
        "psql -c \"ALTER ROLE",
        "systemctl restart bp-postgres.service",
        "systemctl stop bp-postgres.service",
        "quote(parsed.username, safe='')",
    ):
        assert forbidden not in rotate


def test_phase12_cloudshell_helper_pins_exact_candidate_head() -> None:
    helper = _text(CLOUD)

    for required in (
        "PHASE12_HEAD",
        "phase-12-paper-execution-v1",
        "phase12_host_acceptance.sh",
        "PHASE12_HOST_ACCEPTANCE=PASS",
    ):
        assert required in helper


def test_phase12_runbook_preserves_real_money_boundary() -> None:
    runbook = _text(RUNBOOK)

    for required in (
        "RESEARCH",
        "LIVE_TRADING_ENABLED=false",
        "MAX_TRADE_SIZE_USD=0",
        "MAX_DAILY_LOSS_USD=0",
        "PHASE12_HOST_ACCEPTANCE=PASS",
        "PHASE12_INSTALL=PASS",
        "reconciliation",
        "paper",
    ):
        assert required in runbook


def test_ci_validates_phase12_deployment_assets() -> None:
    workflow = _text(CI)

    for command in (
        "bash -n scripts/deploy/phase12_host_acceptance.sh",
        "bash -n scripts/deploy/phase12_cloudshell_accept.sh",
        "bash -n scripts/deploy/phase12_install.sh",
        "bash -n scripts/deploy/phase12_rotate_postgres_password.sh",
        "python -m py_compile scripts/deploy/ensure_phase12_replay_indexes.py",
        "python -m py_compile scripts/run_paper_execution.py",
    ):
        assert command in workflow
