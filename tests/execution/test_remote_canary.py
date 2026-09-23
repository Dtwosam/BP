from __future__ import annotations

import json
from decimal import Decimal

import bp_engine.execution.remote_canary as module
from bp_engine.execution.remote_canary import (
    SshExecutorConfig,
    SshPolymarketTradingClient,
)


class Completed:
    returncode = 0
    stdout = json.dumps(
        {
            "accepted": True,
            "external_order_id": "order-1",
            "status": "live",
            "code": "accepted",
            "message": "",
        }
    )
    stderr = ""


def test_ssh_client_sends_no_wallet_material(monkeypatch, tmp_path) -> None:
    key = tmp_path / "key"
    known = tmp_path / "known"
    key.write_text("private-ssh-key", encoding="utf-8")
    known.write_text("host-key", encoding="utf-8")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs["input"]
        return Completed()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    client = SshPolymarketTradingClient(
        SshExecutorConfig(
            host="10.0.0.2",
            user="bp-exec",
            identity_file=str(key),
            known_hosts_file=str(known),
            remote_command="/opt/bp-exec/venv/bin/python /opt/bp-exec/canary_executor.py",
        )
    )
    result = client.submit_limit_buy(
        token_id="token",
        price=Decimal("0.50"),
        size=Decimal("2"),
    )

    assert result.accepted is True
    assert "POLYMARKET_PRIVATE_KEY" not in captured["input"]
    assert "POLYMARKET_WALLET_ADDRESS" not in captured["input"]
    payload = json.loads(captured["input"])
    assert payload == {
        "action": "submit",
        "price": "0.50",
        "size": "2",
        "token_id": "token",
    }
    assert "StrictHostKeyChecking=yes" in captured["command"]
