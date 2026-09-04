import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "deploy" / "phase14_partitioned_storage_rollout_cloudshell.sh"
MIGRATOR = ROOT / "scripts" / "deploy" / "migrate_partitioned_raw_storage.py"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def test_partitioned_storage_rollout_is_exact_head_detached_and_fail_closed() -> None:
    assert HELPER.is_file(), HELPER
    content = HELPER.read_text(encoding="utf-8")

    required = (
        "set -Eeuo pipefail",
        "PHASE14_PARTITIONED_STORAGE_HEAD",
        "PHASE14_PARTITIONED_STORAGE_FROM_HEAD",
        "exact 40-character verified candidate SHA",
        "gcloud auth list",
        "refs/remotes/origin/$BRANCH",
        '[[ "$REMOTE_HEAD" == "$SHA" ]]',
        '[[ "$OLD_HEAD" == "$EXPECTED_FROM_HEAD" ]]',
        "systemd-run",
        "bp-phase14-partitioned-storage-",
        "Type=oneshot",
        "RECORDER_RESTARTED=false",
        "ROLLBACK_MATERIAL_RETAINED=true",
    )
    for marker in required:
        assert marker in content

    lowered = content.lower()
    for forbidden in (
        "live_trading_enabled=true",
        "place_order",
        "submit_order",
        "private_key",
        "wallet_address",
        "geoblock bypass",
        "phase 15",
    ):
        assert forbidden not in lowered


def test_partitioned_storage_rollout_requires_stopped_recorder_and_dedicated_data_disk() -> None:
    content = HELPER.read_text(encoding="utf-8")

    required = (
        "recorder_must_be_stopped",
        "bp-recorder.service",
        "mountpoint -q /mnt/bp-data",
        "STORAGE_HEALTH_PATH=/mnt/bp-data",
        "STORAGE_ARCHIVE_DIR=/mnt/bp-data/archive/raw",
        "MIN_FREE_GIB",
        "/var/lib/postgresql/data",
        "docker compose",
        "postgres_data_not_on_dedicated_filesystem",
        "phase14-storage-recovery-24-48h-",
        "hours",
        "intervals",
    )
    for marker in required:
        assert marker in content

    assert 'systemctl restart "$RECORDER_UNIT"' not in content
    assert 'systemctl start "$RECORDER_UNIT"' not in content


def test_partitioned_storage_rollout_preserves_research_zero_money_and_rollback() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for expected in (
        '"$mode" != "research"',
        '"$live_trading_enabled" != "false"',
        '"$max_trade_size_usd" != "0"',
        '"$max_daily_loss_usd" != "0"',
        "research_zero_money_boundary_not_satisfied",
        "ROLLBACK_ARMED=1",
        "rollback_partitioned_storage",
        "migrate_partitioned_raw_storage.py rollback",
        "migrate_partitioned_raw_storage.py apply",
        "migrate_partitioned_raw_storage.py verify",
        "raw_market_events_legacy",
        "synthetic",
        "ROLLBACK",
        "EVIDENCE_DIR=/mnt/bp-data/evidence",
        "phase14-partitioned-storage-rollout-",
    ):
        assert expected in content


def test_partitioned_storage_migrator_has_apply_verify_and_rollback_commands() -> None:
    assert MIGRATOR.is_file(), MIGRATOR
    content = MIGRATOR.read_text(encoding="utf-8")

    for marker in (
        'add_parser("apply"',
        'add_parser("verify"',
        'add_parser("rollback"',
        "ensure_partitioned_raw_storage",
        "migrate_existing=True",
        "rollback_partitioned_raw_storage",
        "non_raw_table_counts",
        "synthetic_duplicate_suppressed",
        "synthetic_partition_routing_verified",
    ):
        assert marker in content


def test_partitioned_storage_rollout_assets_have_clean_syntax_and_ci_validation() -> None:
    shell = subprocess.run(
        ["bash", "-n", str(HELPER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert shell.returncode == 0, shell.stderr

    python = subprocess.run(
        ["python", "-m", "py_compile", str(MIGRATOR)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert python.returncode == 0, python.stderr

    ci = CI.read_text(encoding="utf-8")
    assert "bash -n scripts/deploy/phase14_partitioned_storage_rollout_cloudshell.sh" in ci
    assert "python -m py_compile scripts/deploy/migrate_partitioned_raw_storage.py" in ci
