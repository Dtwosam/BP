from pathlib import Path

BOOTSTRAP = Path("scripts/deploy/bootstrap_ubuntu.sh")
ENV_EXAMPLE = Path("deploy/bp.env.example")
INDEX_INSTALLER = Path("scripts/deploy/ensure_storage_indexes.py")
PHASE12_INDEX_INSTALLER = Path("scripts/deploy/ensure_phase12_replay_indexes.py")
RECORDER_SERVICE = Path("src/bp_engine/recorder/service.py")
RECORDER_UNIT = Path("deploy/systemd/bp-recorder.service")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_recorder_startup_provisions_partitioned_mode_without_migrating_legacy() -> None:
    source = read(RECORDER_SERVICE)

    assert "raw_storage_mode" in source
    assert "RawStorageMode.PARTITIONED" in source
    assert "ensure_partitioned_raw_storage" in source
    assert "migrate_existing=False" in source
    assert source.index("ensure_partitioned_raw_storage") < source.index(
        "repository = RecorderRepository()"
    )


def test_storage_index_installer_partitions_only_empty_or_existing_partitioned_storage() -> None:
    source = read(INDEX_INSTALLER)

    assert "ensure_partitioned_raw_storage" in source
    assert "migrate_existing=False" in source
    assert "RawStorageMode.LEGACY" in source
    assert "populated legacy raw storage left unchanged" in source.lower()


def test_partitioned_index_installers_do_not_use_concurrent_parent_index_builds() -> None:
    storage = read(INDEX_INSTALLER)
    replay = read(PHASE12_INDEX_INSTALLER)

    assert "partitioned" in storage.lower()
    assert "CREATE INDEX CONCURRENTLY" in storage
    assert "if storage_mode is RawStorageMode.PARTITIONED" in storage
    assert "CREATE INDEX CONCURRENTLY" in replay
    assert "RawStorageMode.PARTITIONED" in replay


def test_storage_runtime_env_stays_portable_and_keeps_existing_safety_gates() -> None:
    bootstrap = read(BOOTSTRAP)
    env_example = read(ENV_EXAMPLE)

    assert "STORAGE_MAINTENANCE_MAX_AGE_HOURS=2" in bootstrap
    assert "STORAGE_MAINTENANCE_MAX_AGE_HOURS=2" in env_example
    assert "/mnt/bp-data" not in bootstrap
    assert "/mnt/bp-data" not in env_example
    assert "MODE=research" in bootstrap
    assert "LIVE_TRADING_ENABLED=false" in bootstrap
    assert "MAX_TRADE_SIZE_USD=0" in bootstrap
    assert "MAX_DAILY_LOSS_USD=0" in bootstrap


def test_recorder_unit_runs_composite_health_before_recorder_process() -> None:
    unit = read(RECORDER_UNIT)

    condition = (
        "ExecCondition=/opt/bp/.venv/bin/python "
        "/opt/bp/scripts/storage_maintenance.py disk-health"
    )
    start = "ExecStart=/opt/bp/.venv/bin/python -m bp_engine.recorder"
    assert condition in unit
    assert start in unit
    assert unit.index(condition) < unit.index(start)
