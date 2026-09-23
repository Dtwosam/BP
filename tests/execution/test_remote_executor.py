from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from bp_engine.config import Settings
from bp_engine.execution.live_client import (
    LiveClientCancelResult,
    LiveClientOrderResult,
)
from bp_engine.execution import remote_executor


class _FakeClient:
    def __init__(self) -> None:
        self.submits = 0
        self.cancels = 0

    def submit_limit_buy(self, *, token_id, price, size):
        self.submits += 1
        return LiveClientOrderResult(
            accepted=True,
            external_order_id="external-1",
            status="live",
            code="accepted",
            message="",
        )

    def cancel(self, *, external_order_id):
        self.cancels += 1
        return LiveClientCancelResult(
            cancelled=True,
            external_order_id=external_order_id,
            status="cancelled",
            message="",
        )


def _settings(monkeypatch, tmp_path: Path) -> Settings:
    expected_sha = "a" * 40
    activation = tmp_path / "activation.json"
    now = datetime.now(UTC)
    activation.write_text(
        json.dumps(
            {
                "authorized": True,
                "git_sha": expected_sha,
                "authorization_id": "test-canary",
                "issued_at": (now - timedelta(minutes=1)).isoformat(),
                "expires_at": (now + timedelta(hours=1)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    values = {
        "MODE": "live",
        "LIVE_TRADING_ENABLED": "true",
        "MAX_TRADE_SIZE_USD": "5",
        "MAX_TOTAL_EXPOSURE_USD": "5",
        "MAX_DAILY_LOSS_USD": "5",
        "MAX_CONSECUTIVE_LOSSES": "1",
        "LIVE_MIN_EDGE": "0.075",
        "LIVE_ACTIVATION_MANIFEST_PATH": str(activation),
        "LIVE_KILL_SWITCH_PATH": str(tmp_path / "KILL"),
        "POLYMARKET_PRIVATE_KEY": "test-private-key",
        "BP_CANARY_EXPECTED_GIT_SHA": expected_sha,
        "BP_CANARY_ATTEMPTED_PATH": str(tmp_path / "ATTEMPTED.json"),
        "BP_CANARY_EXECUTOR_LOCK_PATH": str(tmp_path / "executor.lock"),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_remote_executor_consumes_canary_before_external_submission(
    monkeypatch,
    tmp_path,
) -> None:
    settings = _settings(monkeypatch, tmp_path)
    fake = _FakeClient()
    monkeypatch.setattr(
        remote_executor.GeoblockClient,
        "check",
        lambda self: SimpleNamespace(blocked=False, country="ZA", region="GP"),
    )
    monkeypatch.setattr(
        remote_executor.OfficialPolymarketTradingClient,
        "create_from_environment",
        lambda **kwargs: fake,
    )
    monkeypatch.setattr(remote_executor.time, "sleep", lambda _seconds: None)

    result = remote_executor._submit(
        settings,
        {
            "token_id": "token-1",
            "price": "0.50",
            "size": "9",
            "ttl_ms": 2000,
        },
    )
    assert result["accepted"] is True
    assert fake.submits == 1
    assert fake.cancels == 1
    assert (tmp_path / "ATTEMPTED.json").exists()
    assert (tmp_path / "KILL").exists()

    with pytest.raises(RuntimeError, match="kill_switch_engaged"):
        remote_executor._submit(
            settings,
            {
                "token_id": "token-2",
                "price": "0.50",
                "size": "9",
                "ttl_ms": 2000,
            },
        )
    assert fake.submits == 1


def test_remote_executor_rejects_more_than_five_dollars(monkeypatch, tmp_path) -> None:
    settings = _settings(monkeypatch, tmp_path)
    fake = _FakeClient()
    monkeypatch.setattr(
        remote_executor.GeoblockClient,
        "check",
        lambda self: SimpleNamespace(blocked=False, country="ZA", region="GP"),
    )
    monkeypatch.setattr(
        remote_executor.OfficialPolymarketTradingClient,
        "create_from_environment",
        lambda **kwargs: fake,
    )

    with pytest.raises(ValueError, match="exceeds"):
        remote_executor._submit(
            settings,
            {
                "token_id": "token-1",
                "price": "0.80",
                "size": "7",
                "ttl_ms": 2000,
            },
        )
    assert fake.submits == 0
