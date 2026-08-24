from pathlib import Path

SYSTEMD = Path("deploy/systemd")
BOOTSTRAP = Path("scripts/deploy/bootstrap_ubuntu.sh")
RUNBOOK = Path("docs/PHASE-3-DEPLOYMENT.md")
SCHEMA = Path("src/bp_engine/storage/schema.py")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_storage_maintenance_service_is_unprivileged_and_fail_closed() -> None:
    service = read(SYSTEMD / "bp-storage-maintenance.service")

    assert "Type=oneshot" in service
    assert "User=bp" in service
    assert "Group=bp" in service
    assert "EnvironmentFile=/etc/bp/bp.env" in service
    assert "WorkingDirectory=/opt/bp" in service
    assert (
        "ExecStart=/opt/bp/.venv/bin/python /opt/bp/scripts/storage_maintenance.py run"
        in service
    )
    assert "NoNewPrivileges=true" in service
    assert "Restart=always" not in service


def test_storage_maintenance_timer_runs_hourly_and_persists_missed_runs() -> None:
    timer = read(SYSTEMD / "bp-storage-maintenance.timer")

    assert "OnCalendar=hourly" in timer
    assert "Persistent=true" in timer
    assert "Unit=bp-storage-maintenance.service" in timer


def test_disk_health_timer_checks_every_five_minutes_without_restart_loop() -> None:
    service = read(SYSTEMD / "bp-storage-disk-health.service")
    timer = read(SYSTEMD / "bp-storage-disk-health.timer")

    assert "Type=oneshot" in service
    assert "User=bp" in service
    assert (
        "ExecStart=/opt/bp/.venv/bin/python /opt/bp/scripts/storage_maintenance.py disk-health"
        in service
    )
    assert "Restart=always" not in service
    assert "OnBootSec=5min" in timer
    assert "OnUnitActiveSec=5min" in timer
    assert "Unit=bp-storage-disk-health.service" in timer


def test_bootstrap_installs_storage_environment_archive_dir_and_timer_units() -> None:
    bootstrap = read(BOOTSTRAP)

    assert "STORAGE_HOT_RAW_HOURS=24" in bootstrap
    assert "STORAGE_ARCHIVE_RETENTION_HOURS=24" in bootstrap
    assert "STORAGE_STATE_RETENTION_DAYS=90" in bootstrap
    assert "STORAGE_ARCHIVE_DIR=/var/lib/bp/archive/raw" in bootstrap
    assert "STORAGE_WARNING_FREE_GIB=25" in bootstrap
    assert "STORAGE_CRITICAL_FREE_GIB=15" in bootstrap
    assert "STORAGE_DELETE_BATCH_SIZE=50000" in bootstrap
    assert "/var/lib/bp/archive/raw" in bootstrap
    assert "bp-storage-maintenance.timer" in bootstrap
    assert "bp-storage-disk-health.timer" in bootstrap
    assert "systemctl enable --now bp-storage-maintenance.timer" not in bootstrap
    assert "systemctl enable --now bp-storage-disk-health.timer" not in bootstrap


def test_existing_hosts_install_ordered_raw_retention_index() -> None:
    bootstrap = read(BOOTSTRAP)
    schema = read(SCHEMA)

    assert "ensure_storage_indexes.py" in bootstrap
    assert "ix_raw_market_events_received_at_id" in schema
    assert "raw_market_events.c.received_at" in schema
    assert "raw_market_events.c.id" in schema


def test_phase3_runbook_keeps_trading_disabled_and_requires_manual_archive_verification() -> None:
    runbook = read(RUNBOOK)

    assert "LIVE_TRADING_ENABLED=false" in runbook
    assert "MAX_TRADE_SIZE_USD=0" in runbook
    assert "MAX_DAILY_LOSS_USD=0" in runbook
    assert "storage_maintenance.py report" in runbook
    assert "storage_maintenance.py disk-health" in runbook
    assert "storage_maintenance.py run" in runbook
    assert "sha256sum" in runbook
    assert "bp-storage-maintenance.timer" in runbook
    assert "bp-storage-disk-health.timer" in runbook
    assert "Do not enable the timers until" in runbook
