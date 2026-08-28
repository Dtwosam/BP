from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SYSTEMD = ROOT / "deploy" / "systemd"


def _read(name: str) -> str:
    return (SYSTEMD / name).read_text(encoding="utf-8")


def test_dashboard_api_service_is_unprivileged_loopback_and_read_only() -> None:
    unit = _read("bp-dashboard-api.service")

    assert "User=bp" in unit
    assert "Group=bp" in unit
    assert "Requires=bp-postgres.service" in unit
    assert "EnvironmentFile=/etc/bp/bp.env" in unit
    assert "ExecStart=/opt/bp/.venv/bin/python -m bp_engine.dashboard --host 127.0.0.1" in unit
    assert "NoNewPrivileges=true" in unit
    assert "ProtectSystem=full" in unit
    assert "ProtectHome=true" in unit
    assert "IPAddressDeny=any" in unit
    assert "IPAddressAllow=localhost" in unit
    assert "wallet" not in unit.lower()
    assert "order" not in unit.lower()


def test_dashboard_web_service_is_unprivileged_loopback_and_api_only() -> None:
    unit = _read("bp-dashboard-web.service")

    assert "User=bp" in unit
    assert "Group=bp" in unit
    assert "Requires=bp-dashboard-api.service" in unit
    assert "WorkingDirectory=/opt/bp/apps/dashboard" in unit
    assert "Environment=BP_DASHBOARD_API_URL=http://127.0.0.1:8787/api/v1/snapshot" in unit
    assert "ExecStart=/opt/bp/.node/bin/node" in unit
    assert "next/dist/bin/next start -H 127.0.0.1 -p 3000" in unit
    assert "NoNewPrivileges=true" in unit
    assert "ProtectSystem=full" in unit
    assert "ProtectHome=true" in unit
    assert "ReadWritePaths=/opt/bp/apps/dashboard/.next/cache" in unit
    assert "IPAddressDeny=any" in unit
    assert "IPAddressAllow=localhost" in unit
    assert "wallet" not in unit.lower()
    assert "order" not in unit.lower()
