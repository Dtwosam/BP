from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from bp_engine.execution.live_client import (
    LiveClientCancelResult,
    LiveClientOrderResult,
)


class RemoteExecutorError(RuntimeError):
    """Raised when the execution-only host cannot return a trusted result."""


class RemoteSshPolymarketTradingClient:
    """Narrow SSH transport to the execution-only Johannesburg host."""

    def __init__(
        self,
        *,
        host: str,
        user: str,
        key_path: str,
        known_hosts_path: str,
        timeout_seconds: float = 12.0,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        if not host.strip() or not user.strip():
            raise ValueError("remote host and user must not be blank")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._host = host.strip()
        self._user = user.strip()
        self._key_path = str(Path(key_path))
        self._known_hosts_path = str(Path(known_hosts_path))
        self._timeout_seconds = timeout_seconds
        self._runner = runner
        self._order_deadline: datetime | None = None

    def _call(self, payload: dict[str, object]) -> dict[str, Any]:
        command = [
            "ssh",
            "-i",
            self._key_path,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={self._known_hosts_path}",
            f"{self._user}@{self._host}",
        ]
        try:
            completed = self._runner(
                command,
                input=json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
                text=True,
                capture_output=True,
                timeout=self._timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RemoteExecutorError("remote executor transport failed") from exc
        if completed.returncode != 0:
            raise RemoteExecutorError("remote executor returned nonzero status")
        lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        if len(lines) != 1:
            raise RemoteExecutorError("remote executor output was not one JSON object")
        try:
            result = json.loads(lines[0])
        except json.JSONDecodeError as exc:
            raise RemoteExecutorError("remote executor returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise RemoteExecutorError("remote executor returned invalid payload")
        return result

    def health(self) -> bool:
        try:
            result = self._call({"operation": "health"})
        except RemoteExecutorError:
            return False
        return (
            result.get("ok") is True
            and result.get("blocked") is False
            and result.get("private_key_configured") is True
            and result.get("sdk_client_ready") is True
            and result.get("canary_attempted") is False
            and result.get("kill_switch_engaged") is False
        )

    def set_order_deadline(self, expires_at: datetime) -> None:
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("order deadline must be timezone-aware")
        self._order_deadline = expires_at.astimezone(UTC)

    def submit_limit_buy(
        self,
        *,
        token_id: str,
        price: Decimal,
        size: Decimal,
    ) -> LiveClientOrderResult:
        deadline = self._order_deadline
        if deadline is None:
            raise RemoteExecutorError("remote order deadline is not configured")
        self._order_deadline = None
        result = self._call(
            {
                "operation": "submit_limit_buy",
                "token_id": token_id,
                "price": str(price),
                "size": str(size),
                "ttl_ms": 2000,
                "expires_at": deadline.isoformat(),
            }
        )
        return LiveClientOrderResult(
            accepted=result.get("accepted") is True,
            external_order_id=(
                None
                if result.get("external_order_id") in (None, "")
                else str(result["external_order_id"])
            ),
            status=str(result.get("status") or "error"),
            code=str(result.get("code") or "remote_executor_error"),
            message=str(result.get("message") or ""),
        )

    def cancel(self, *, external_order_id: str) -> LiveClientCancelResult:
        result = self._call(
            {
                "operation": "cancel",
                "external_order_id": external_order_id,
            }
        )
        return LiveClientCancelResult(
            cancelled=result.get("cancelled") is True,
            external_order_id=external_order_id,
            status=str(result.get("status") or "error"),
            message=str(result.get("message") or ""),
        )
