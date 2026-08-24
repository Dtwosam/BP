import importlib.util
from pathlib import Path
from types import SimpleNamespace


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "storage_maintenance.py"
SCRIPT_SPEC = importlib.util.spec_from_file_location("storage_maintenance_script", SCRIPT_PATH)
assert SCRIPT_SPEC is not None
assert SCRIPT_SPEC.loader is not None
storage_maintenance = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(storage_maintenance)


def test_run_counts_archive_retention_after_hot_storage(monkeypatch, tmp_path, capsys) -> None:
    settings = SimpleNamespace(
        storage_hot_raw_hours=24,
        storage_archive_retention_hours=24,
        storage_state_retention_days=90,
        storage_delete_batch_size=50_000,
        storage_archive_dir=str(tmp_path / "archive"),
        storage_warning_free_gib=25,
        storage_critical_free_gib=15,
        database_url="sqlite://",
    )
    captured: dict[str, int] = {}

    monkeypatch.setattr(storage_maintenance, "_settings", lambda args: settings)
    monkeypatch.setattr(storage_maintenance, "create_engine", lambda url: object())
    monkeypatch.setattr(storage_maintenance.metadata, "create_all", lambda engine: None)

    def capture_prune(engine, archive_dir, *, now, retention_hours):
        captured["retention_hours"] = retention_hours
        return []

    monkeypatch.setattr(storage_maintenance, "prune_expired_archives", capture_prune)
    monkeypatch.setattr(
        storage_maintenance,
        "disk_health",
        lambda *args, **kwargs: {
            "status": "critical",
            "path": str(tmp_path),
            "total_bytes": 1,
            "used_bytes": 1,
            "free_bytes": 0,
            "free_percent": 0.0,
            "warning_free_bytes": 1,
            "critical_free_bytes": 1,
        },
    )

    assert storage_maintenance._run_command(SimpleNamespace()) == 1
    capsys.readouterr()
    assert captured["retention_hours"] == 48
