from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from bp_engine.execution.live_client import (
    LiveClientCancelResult,
    LiveClientOrderResult,
)


@dataclass(frozen=True)
class SshExecutorConfig:
    host: str
    user: str
    identity_file: str
    known_hosts_file: str
    remote_command: str
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        for name in (
            "host",
            "user",
            "identity_file",
            "known_hosts_file",
            "remote_command",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must not be blank")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


class SshPolymarketTradingClient:
    """Private-network SSH transport to the execution-only canary host."""

    def __init__(self, config: SshExecutorConfig) -> None:
        self._config = config

    def _call(self, payload: dict[str, Any]) -> dict[str, Any]:
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={self._config.known_hosts_file}",
            "-i",
            self._config.identity_file,
            f"{self._config.user}@{self._config.host}",
            self._config.remote_command,
        ]
        completed = subprocess.run(
            command,
            input=json.dumps(payload, sort_keys=True, separators=(",", ":")),
            text=True,
            capture_output=True,
            timeout=self._config.timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("remote canary executor call failed")
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("remote canary executor returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise RuntimeError("remote canary executor returned invalid payload")
        return result

    def health(self) -> dict[str, Any]:
        return self._call({"action": "health"})

    def submit_limit_buy(
        self,
        *,
        token_id: str,
        price: Decimal,
        size: Decimal,
    ) -> LiveClientOrderResult:
        result = self._call(
            {
                "action": "submit",
                "token_id": str(token_id),
                "price": str(price),
                "size": str(size),
            }
        )
        return LiveClientOrderResult(
            accepted=result.get("accepted") is True,
            external_order_id=(
                str(result["external_order_id"])
                if result.get("external_order_id")
                else None
            ),
            status=str(result.get("status") or "error"),
            code=str(result.get("code") or "remote_error"),
            message=str(result.get("message") or ""),
        )

    def cancel(self, *, external_order_id: str) -> LiveClientCancelResult:
        result = self._call(
            {
                "action": "cancel",
                "external_order_id": str(external_order_id),
            }
        )
        return LiveClientCancelResult(
            cancelled=result.get("cancelled") is True,
            external_order_id=str(external_order_id),
            status=str(result.get("status") or "error"),
            message=str(result.get("message") or ""),
        )


def assert_executor_files(config: SshExecutorConfig) -> None:
    for path in (config.identity_file, config.known_hosts_file):
        source = Path(path)
        if not source.is_file():
            raise RuntimeError("remote canary executor SSH material is missing")
