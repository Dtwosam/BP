from __future__ import annotations

import argparse
import json
import os
import pwd
import re
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from bp_engine.config import Settings

DEFAULT_LONG_TRANSACTION_SECONDS = 60.0
POSTGRES_PORT = 5432
_SYSTEMD_UNIT_RE = re.compile(r"(?:^|/)([^/]+\.service)(?:/|$)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only V4 PostgreSQL long-session host owner report"
    )
    parser.add_argument("--env-file", default="/etc/bp/bp.env")
    parser.add_argument(
        "--long-transaction-seconds",
        type=float,
        default=DEFAULT_LONG_TRANSACTION_SECONDS,
    )
    return parser.parse_args()


def _hex_ipv4(value: str) -> str:
    raw = bytes.fromhex(value)
    return ".".join(str(part) for part in raw[::-1])


def _parse_proc_net(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        fields = line.split()
        if len(fields) < 10:
            continue
        local_hex, remote_hex = fields[1], fields[2]
        state = fields[3]
        if state != "01":  # ESTABLISHED
            continue
        local_addr_hex, local_port_hex = local_hex.split(":")
        remote_addr_hex, remote_port_hex = remote_hex.split(":")
        family = "ipv6" if len(local_addr_hex) > 8 else "ipv4"
        local_addr = (
            _hex_ipv4(local_addr_hex)
            if family == "ipv4"
            else local_addr_hex
        )
        remote_addr = (
            _hex_ipv4(remote_addr_hex)
            if family == "ipv4"
            else remote_addr_hex
        )
        rows.append(
            {
                "family": family,
                "local_addr": local_addr,
                "local_port": int(local_port_hex, 16),
                "remote_addr": remote_addr,
                "remote_port": int(remote_port_hex, 16),
                "inode": fields[9],
            }
        )
    return rows


def _socket_inode_owners() -> dict[str, list[int]]:
    owners: dict[str, list[int]] = {}
    for proc_dir in Path("/proc").iterdir():
        if not proc_dir.name.isdigit():
            continue
        pid = int(proc_dir.name)
        fd_dir = proc_dir / "fd"
        try:
            entries = list(fd_dir.iterdir())
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        for entry in entries:
            try:
                target = os.readlink(entry)
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
            if not target.startswith("socket:[") or not target.endswith("]"):
                continue
            inode = target[8:-1]
            owners.setdefault(inode, []).append(pid)
    return owners


def _systemd_unit(pid: int) -> str | None:
    try:
        text_value = (Path("/proc") / str(pid) / "cgroup").read_text(
            encoding="utf-8"
        )
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    for line in text_value.splitlines():
        match = _SYSTEMD_UNIT_RE.search(line)
        if match:
            return match.group(1)
    return None


def _uid_name(pid: int) -> str | None:
    try:
        status = (Path("/proc") / str(pid) / "status").read_text(
            encoding="utf-8"
        )
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    for line in status.splitlines():
        if line.startswith("Uid:"):
            uid = int(line.split()[1])
            try:
                return pwd.getpwuid(uid).pw_name
            except KeyError:
                return str(uid)
    return None


def _process_name(pid: int) -> str | None:
    try:
        return (Path("/proc") / str(pid) / "comm").read_text(
            encoding="utf-8"
        ).strip()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None


def _owner_record(
    *,
    pid: int,
    socket: dict[str, Any],
) -> dict[str, Any]:
    return {
        "host_pid": pid,
        "systemd_unit": _systemd_unit(pid),
        "process_name": _process_name(pid),
        "user": _uid_name(pid),
        "socket_family": socket["family"],
        "local_addr": socket["local_addr"],
        "local_port": int(socket["local_port"]),
        "remote_addr": socket["remote_addr"],
        "remote_port": int(socket["remote_port"]),
    }


def _postgres_client_socket_owners() -> dict[int, list[dict[str, Any]]]:
    sockets = _parse_proc_net(Path("/proc/net/tcp"))
    sockets.extend(_parse_proc_net(Path("/proc/net/tcp6")))
    inode_owners = _socket_inode_owners()
    by_client_port: dict[int, list[dict[str, Any]]] = {}
    for socket in sockets:
        if int(socket["remote_port"]) != POSTGRES_PORT:
            continue
        client_port = int(socket["local_port"])
        for pid in sorted(set(inode_owners.get(str(socket["inode"]), []))):
            by_client_port.setdefault(client_port, []).append(
                _owner_record(pid=pid, socket=socket)
            )
    return by_client_port


def _host_postgres_client_connections() -> list[dict[str, Any]]:
    sockets = _parse_proc_net(Path("/proc/net/tcp"))
    sockets.extend(_parse_proc_net(Path("/proc/net/tcp6")))
    inode_owners = _socket_inode_owners()
    connections: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for socket in sockets:
        if int(socket["remote_port"]) != POSTGRES_PORT:
            continue
        inode = str(socket["inode"])
        for pid in sorted(set(inode_owners.get(inode, []))):
            process_name = _process_name(pid)
            if process_name == "docker-proxy":
                continue
            key = (pid, inode)
            if key in seen:
                continue
            seen.add(key)
            connections.append(_owner_record(pid=pid, socket=socket))
    return sorted(
        connections,
        key=lambda row: (
            str(row.get("systemd_unit") or ""),
            int(row["host_pid"]),
            int(row["local_port"]),
        ),
    )


def _long_transactions(
    connection,
    threshold_seconds: float,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        text(
            """
            SELECT
                pid,
                usename,
                application_name,
                state,
                client_addr::text AS client_addr,
                client_port,
                EXTRACT(EPOCH FROM (clock_timestamp() - xact_start))
                    AS xact_age_seconds,
                wait_event_type,
                wait_event
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND backend_type = 'client backend'
              AND pid <> pg_backend_pid()
              AND xact_start IS NOT NULL
              AND clock_timestamp() - xact_start
                    >= (:threshold_seconds * interval '1 second')
            ORDER BY xact_start, pid
            """
        ),
        {"threshold_seconds": threshold_seconds},
    ).mappings()
    return [dict(row) for row in rows]


def build_report(
    connection,
    *,
    long_transaction_seconds: float,
) -> dict[str, Any]:
    if long_transaction_seconds <= 0:
        raise ValueError("long_transaction_seconds must be greater than zero")
    readonly = str(
        connection.execute(text("SHOW default_transaction_read_only")).scalar_one()
    ).lower()
    if readonly != "on":
        raise RuntimeError("database connection is not read-only")

    socket_owners = _postgres_client_socket_owners()
    host_clients = _host_postgres_client_connections()
    rows = _long_transactions(connection, long_transaction_seconds)
    sessions: list[dict[str, Any]] = []
    matched_units: set[str] = set()
    host_client_units = sorted(
        {
            str(row["systemd_unit"])
            for row in host_clients
            if row.get("systemd_unit")
        }
    )
    unmatched_count = 0
    proxy_obscured_count = 0
    for row in rows:
        client_port = (
            int(row["client_port"])
            if row["client_port"] is not None
            else None
        )
        owners = socket_owners.get(client_port, []) if client_port else []
        if not owners:
            unmatched_count += 1
        if owners and all(
            owner.get("process_name") == "docker-proxy"
            for owner in owners
        ):
            proxy_obscured_count += 1
        for owner in owners:
            unit = owner.get("systemd_unit")
            if unit:
                matched_units.add(str(unit))
        sessions.append(
            {
                "postgres_pid": int(row["pid"]),
                "database_user": str(row["usename"] or ""),
                "application_name": str(row["application_name"] or ""),
                "state": str(row["state"] or ""),
                "client_addr": (
                    str(row["client_addr"])
                    if row["client_addr"] is not None
                    else None
                ),
                "client_port": client_port,
                "xact_age_seconds": float(row["xact_age_seconds"] or 0.0),
                "wait_event_type": (
                    str(row["wait_event_type"])
                    if row["wait_event_type"] is not None
                    else None
                ),
                "wait_event": (
                    str(row["wait_event"])
                    if row["wait_event"] is not None
                    else None
                ),
                "host_socket_owners": owners,
            }
        )

    return {
        "report": "v4_db_session_owner_v2",
        "long_transaction_seconds": long_transaction_seconds,
        "long_transaction_count": len(sessions),
        "sessions": sessions,
        "host_postgres_client_connections": host_clients,
        "host_client_summary": {
            "connection_count": len(host_clients),
            "systemd_units": host_client_units,
        },
        "owner_summary": {
            "matched_systemd_units": sorted(matched_units),
            "unmatched_session_count": unmatched_count,
            "proxy_obscured_session_count": proxy_obscured_count,
            "all_sessions_attributed": unmatched_count == 0,
            "all_sessions_exactly_attributed": (
                unmatched_count == 0 and proxy_obscured_count == 0
            ),
        },
        "safety": {
            "database_read_only_required": True,
            "database_writes_performed": False,
            "service_mutation_performed": False,
            "process_mutation_performed": False,
            "order_submission_performed": False,
            "command_arguments_emitted": False,
            "environment_values_emitted": False,
        },
    }


def main() -> int:
    args = parse_args()
    if os.geteuid() != 0:
        raise SystemExit("host socket ownership probe requires root read access")
    settings = Settings(_env_file=args.env_file)
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={
            "options": (
                "-c default_transaction_read_only=on "
                "-c statement_timeout=5000 "
                "-c application_name=bp-v4-db-session-owner"
            )
        },
    )
    try:
        with engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            report = build_report(
                connection,
                long_transaction_seconds=args.long_transaction_seconds,
            )
    finally:
        engine.dispose()

    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    print("PHASE14_V4_DB_SESSION_OWNER_STATUS=PASS")
    print("DATABASE_ACCESS=read_only")
    print("DATABASE_WRITES_PERFORMED=false")
    print("SERVICE_MUTATION_PERFORMED=false")
    print("PROCESS_MUTATION_PERFORMED=false")
    print("ORDER_SUBMISSION_PERFORMED=false")
    print("COMMAND_ARGUMENTS_EMITTED=false")
    print("ENVIRONMENT_VALUES_EMITTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
