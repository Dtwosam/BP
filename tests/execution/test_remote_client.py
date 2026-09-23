from __future__ import annotations

import json
import subprocess
from decimal import Decimal

from bp_engine.execution.remote_client import RemoteSshPolymarketTradingClient


def test_remote_client_uses_fixed_ssh_transport_and_normalizes_order() -> None:
    seen: dict[str, object] = {}

    def runner(command, **kwargs):
        seen["command"] = command
        seen["input"] = kwargs["input"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "accepted": True,
                    "external_order_id": "order-1",
                    "status": "live",
                    "code": "accepted",
                    "message": "",
                }
            )
            + "\n",
            stderr="",
        )

    client = RemoteSshPolymarketTradingClient(
        host="10.0.0.2",
        user="bp-exec",
        key_path="/var/lib/bp/live-canary/ssh/id_ed25519",
        known_hosts_path="/var/lib/bp/live-canary/ssh/known_hosts",
        runner=runner,
    )
    result = client.submit_limit_buy(
        token_id="token-1",
        price=Decimal("0.50"),
        size=Decimal("9"),
    )
    assert result.accepted is True
    assert result.external_order_id == "order-1"
    assert seen["command"][0] == "ssh"
    assert "StrictHostKeyChecking=yes" in seen["command"]
    payload = json.loads(str(seen["input"]))
    assert payload == {
        "operation": "submit_limit_buy",
        "price": "0.50",
        "size": "9",
        "token_id": "token-1",
        "ttl_ms": 2000,
    }
