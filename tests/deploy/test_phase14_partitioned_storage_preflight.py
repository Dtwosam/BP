import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = ROOT / "scripts" / "deploy" / "phase14_partitioned_storage_preflight_cloudshell.sh"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def test_storage_preflight_is_exact_head_and_production_read_only() -> None:
    assert PREFLIGHT.is_file(), PREFLIGHT
    content = PREFLIGHT.read_text(encoding="utf-8")

    for marker in (
        "set -Eeuo pipefail",
        "PHASE14_PARTITIONED_STORAGE_HEAD",
        "PHASE14_PARTITIONED_STORAGE_FROM_HEAD",
        "exact 40-character verified candidate SHA",
        "gcloud auth list",
        "gcloud compute ssh",
        "git -C \"$REPO\" ls-remote",
        '[[ "$REMOTE_HEAD" == "$SHA" ]]',
        '[[ "$OLD_HEAD" == "$EXPECTED_FROM_HEAD" ]]',
        "PHASE14_PARTITIONED_STORAGE_PREFLIGHT=PASS",
    ):
        assert marker in content

    lowered = content.lower()
    for forbidden in (
        "systemd-run",
        "git fetch",
        "git checkout",
        "git reset",
        "systemctl stop ",
        "systemctl start ",
        "systemctl restart ",
        "systemctl enable ",
        "migrate_partitioned_raw_storage.py",
        "storage_maintenance.py run",
        "mktemp",
        "live_trading_enabled=true",
        "place_order",
        "submit_order",
        "private_key",
        "wallet_address",
    ):
        assert forbidden not in lowered


def test_storage_preflight_requires_stopped_recorder_and_protected_filesystem() -> None:
    content = PREFLIGHT.read_text(encoding="utf-8")

    for marker in (
        "bp-recorder.service",
        "recorder_must_be_stopped",
        "mountpoint -q /mnt/bp-data",
        "/mnt/bp-data/archive/raw",
        "/mnt/bp-data/evidence",
        "MIN_FREE_GIB",
        "/var/lib/postgresql/data",
        "postgres_data_not_on_dedicated_filesystem",
        "archive_not_on_dedicated_filesystem",
        "phase14-storage-recovery-24-48h-",
        "hours",
        "intervals",
    ):
        assert marker in content


def test_storage_preflight_checks_research_zero_money_and_reports_database_shape() -> None:
    content = PREFLIGHT.read_text(encoding="utf-8")

    for marker in (
        '"$mode" != "research"',
        '"$live_trading_enabled" != "false"',
        '"$max_trade_size_usd" != "0"',
        '"$max_daily_loss_usd" != "0"',
        "research_zero_money_boundary_not_satisfied",
        "pg_total_relation_size",
        "pg_partitioned_table",
        "raw_market_events_legacy",
        "raw_event_dedupe",
        "POSTGRES_DATA_SOURCE",
        "MAINTENANCE_TIMER_STATE",
        "DISK_HEALTH_TIMER_STATE",
    ):
        assert marker in content


def test_storage_preflight_has_clean_syntax_and_ci_validation() -> None:
    shell = subprocess.run(
        ["bash", "-n", str(PREFLIGHT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert shell.returncode == 0, shell.stderr

    ci = CI.read_text(encoding="utf-8")
    assert "bash -n scripts/deploy/phase14_partitioned_storage_preflight_cloudshell.sh" in ci
