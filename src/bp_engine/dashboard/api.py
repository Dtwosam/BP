from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from bp_engine.config import get_settings
from bp_engine.dashboard.repository import PostgresDashboardRepository
from bp_engine.dashboard.service import build_dashboard_snapshot

SnapshotProvider = Callable[[], dict[str, Any]]
Response = tuple[int, dict[str, str], str]
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _json_response(status: int, payload: dict[str, Any], **headers: str) -> Response:
    response_headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
        **headers,
    }
    return status, response_headers, json.dumps(payload, separators=(",", ":"), sort_keys=True)


def route_request(method: str, path: str, snapshot_provider: SnapshotProvider) -> Response:
    if method.upper() != "GET":
        return _json_response(
            405,
            {"error": "method_not_allowed"},
            Allow="GET",
        )

    clean_path = urlsplit(path).path
    if clean_path == "/api/v1/snapshot":
        return _json_response(200, snapshot_provider())
    if clean_path == "/health":
        snapshot = snapshot_provider()
        mode = snapshot.get("mode", {})
        return _json_response(
            200,
            {
                "status": "ok",
                "mode": mode.get("trading_mode"),
                "live_trading_enabled": bool(mode.get("live_trading_enabled", False)),
            },
        )
    return _json_response(404, {"error": "not_found"})


def make_snapshot_provider(database_url: str) -> SnapshotProvider:
    repository = PostgresDashboardRepository(database_url)

    def provide() -> dict[str, Any]:
        return build_dashboard_snapshot(repository, now=datetime.now(UTC))

    return provide


def build_handler(snapshot_provider: SnapshotProvider) -> type[BaseHTTPRequestHandler]:
    class DashboardRequestHandler(BaseHTTPRequestHandler):
        server_version = "BPDashboard/1"

        def _respond(self) -> None:
            status, headers, body = route_request(self.command, self.path, snapshot_provider)
            encoded = body.encode("utf-8")
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802
            self._respond()

        def do_POST(self) -> None:  # noqa: N802
            self._respond()

        def do_PUT(self) -> None:  # noqa: N802
            self._respond()

        def do_PATCH(self) -> None:  # noqa: N802
            self._respond()

        def do_DELETE(self) -> None:  # noqa: N802
            self._respond()

        def log_message(self, format: str, *args: object) -> None:
            return

    return DashboardRequestHandler


def run_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    database_url: str | None = None,
) -> None:
    if host not in _LOOPBACK_HOSTS:
        raise ValueError("dashboard API must bind to loopback only")
    if not 1 <= int(port) <= 65535:
        raise ValueError("dashboard API port must be between 1 and 65535")

    settings = get_settings()
    provider = make_snapshot_provider(database_url or settings.database_url)
    server = ThreadingHTTPServer((host, int(port)), build_handler(provider))
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the read-only BP dashboard snapshot API")
    parser.add_argument(
        "--host",
        default=os.getenv("BP_DASHBOARD_API_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("BP_DASHBOARD_API_PORT", "8787")),
    )
    args = parser.parse_args()
    run_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
