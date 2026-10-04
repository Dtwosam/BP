from __future__ import annotations

import runpy
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "scripts" / "report_v4_db_session_owner.py"
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_db_session_owner_cloudshell.sh"
)


def test_v4_db_session_owner_helper_has_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(HELPER)], check=True)


def test_v4_db_session_owner_report_is_strictly_read_only() -> None:
    source = REPORT.read_text(encoding="utf-8")
    for marker in (
        "default_transaction_read_only=on",
        "statement_timeout=5000",
        'isolation_level="AUTOCOMMIT"',
        "pg_stat_activity",
        "/proc/net/tcp",
        "/proc/net/tcp6",
        "/proc",
        "DATABASE_WRITES_PERFORMED=false",
        "SERVICE_MUTATION_PERFORMED=false",
        "PROCESS_MUTATION_PERFORMED=false",
        "ORDER_SUBMISSION_PERFORMED=false",
        "COMMAND_ARGUMENTS_EMITTED=false",
        "ENVIRONMENT_VALUES_EMITTED=false",
    ):
        assert marker in source

    for forbidden in (
        "REINDEX INDEX",
        "VACUUM ",
        "CREATE INDEX",
        "DROP INDEX",
        "ALTER TABLE",
        "DROP TABLE",
        "DELETE FROM",
        "UPDATE ",
        "INSERT INTO",
        "pg_terminate_backend",
        "os.kill(",
        "subprocess.run(",
        "/proc/{pid}/environ",
        "cmdline",
    ):
        assert forbidden not in source


def test_v4_db_session_owner_parses_ipv4_proc_address() -> None:
    namespace = runpy.run_path(str(REPORT))
    parse = namespace["_hex_ipv4"]

    assert parse("0100007F") == "127.0.0.1"
    assert parse("00000000") == "0.0.0.0"


def test_v4_db_session_owner_only_matches_postgres_destination_port() -> None:
    source = REPORT.read_text(encoding="utf-8")
    for marker in (
        "POSTGRES_PORT = 5432",
        'if int(socket["remote_port"]) != POSTGRES_PORT',
        'client_port = int(socket["local_port"])',
        "host_socket_owners",
        "matched_systemd_units",
        "all_sessions_attributed",
    ):
        assert marker in source


def test_v4_db_session_owner_emits_sanitized_process_identity_only() -> None:
    source = REPORT.read_text(encoding="utf-8")
    for marker in (
        "systemd_unit",
        "process_name",
        "user",
        "host_pid",
        "socket_family",
        "local_port",
        "remote_port",
    ):
        assert marker in source
    for forbidden in (
        "command_line",
        "/proc/self/environ",
        "/proc/{pid}/environ",
        "DATABASE_URL",
        "POSTGRES_PASSWORD",
    ):
        assert forbidden not in source


def test_v4_db_session_owner_helper_requires_accepted_runtime() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "52b4355d6f077373b873f7a6f42bc37a20ddbc7b",
        "bp-postgres.service",
        "bp-recorder.service",
        "bp-v3-frozen-predictor.service",
        "bp-v3-paper-execution.service",
        "bp-storage-maintenance.timer",
        "bp-storage-disk-health.timer",
        "bp-v2-forward-coverage.timer",
        "bp-v4-forward-coverage.timer",
        "expected recorder queue maxsize 50000",
        "expected recorder batch size 500",
        "expected 4 recorder writer workers",
        "expected recorder flush interval 0.25",
        "automatic_promotion must remain false",
    ):
        assert marker in source


def test_v4_db_session_owner_helper_is_read_only() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "REPORT_READ_ONLY=true",
        "REPORT_PURPOSE=postgres_long_transaction_host_owner",
        "PRODUCTION_MUTATION=false",
        "timeout --signal=TERM --kill-after=5s 30s env",
    ):
        assert marker in source

    for forbidden in (
        "systemctl stop",
        "systemctl start",
        "systemctl restart",
        'git -C "$REPO" checkout',
        "REINDEX INDEX",
        "VACUUM ",
        "CREATE INDEX",
        "DROP INDEX",
        "pg_terminate_backend",
        "kill -",
    ):
        assert forbidden not in source


def test_v4_db_session_owner_report_requires_root_for_proc_fd_attribution() -> None:
    source = REPORT.read_text(encoding="utf-8")
    assert "os.geteuid() != 0" in source
    assert "host socket ownership probe requires root read access" in source
