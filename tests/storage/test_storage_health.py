import json
import subprocess
import sys
from collections import namedtuple

from bp_engine.storage.maintenance import disk_health

DiskUsage = namedtuple("DiskUsage", "total used free")
GIB = 1024**3


def test_disk_health_reports_ok_warning_and_critical(monkeypatch, tmp_path) -> None:
    cases = [
        (40 * GIB, "ok"),
        (20 * GIB, "warning"),
        (10 * GIB, "critical"),
    ]

    for free_bytes, expected in cases:
        monkeypatch.setattr(
            "bp_engine.storage.maintenance.shutil.disk_usage",
            lambda path, free=free_bytes: DiskUsage(100 * GIB, 100 * GIB - free, free),
        )
        report = disk_health(tmp_path, warning_free_gib=25, critical_free_gib=15)
        assert report["status"] == expected
        assert report["free_bytes"] == free_bytes
        assert report["warning_free_bytes"] == 25 * GIB
        assert report["critical_free_bytes"] == 15 * GIB


def test_disk_health_cli_returns_nonzero_only_for_critical(tmp_path) -> None:
    common = [
        sys.executable,
        "scripts/storage_maintenance.py",
        "disk-health",
        "--path",
        str(tmp_path),
    ]

    ok = subprocess.run(
        [*common, "--warning-free-gib", "0", "--critical-free-gib", "0"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["status"] == "ok"

    warning = subprocess.run(
        [*common, "--warning-free-gib", "1000000000", "--critical-free-gib", "0"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert warning.returncode == 0, warning.stderr
    assert json.loads(warning.stdout)["status"] == "warning"

    critical = subprocess.run(
        [
            *common,
            "--warning-free-gib",
            "1000000001",
            "--critical-free-gib",
            "1000000000",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert critical.returncode == 1, critical.stderr
    assert json.loads(critical.stdout)["status"] == "critical"
