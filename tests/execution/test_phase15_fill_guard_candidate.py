from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from bp_engine.execution import (
    telegram_privileged_consumer_fill_guard_candidate as consumer,
)

ROOT = Path(__file__).resolve().parents[2]
EXECUTOR = ROOT / "scripts/deploy/phase15_v3_canary_executor_fill_guard_candidate.py"


def _completed(payload: dict[str, object]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(
        args=["executor"],
        returncode=0,
        stdout=(json.dumps(payload) + "\n").encode(),
        stderr=b"",
    )


def _load_executor_module():
    spec = importlib.util.spec_from_file_location("phase15_fill_guard_executor_candidate", EXECUTOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prepared_package(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path, Path, str]:
    package = tmp_path / ("a" * 64)
    package.mkdir()
    prepared = {
        "action": "submit",
        "intent_id": "intent-1",
        "prediction_id": "prediction-1",
        "paper_order_id": "paper-1",
        "market_end_at": "2026-09-28T14:01:00+00:00",
        "request": {
            "token_id": "token-1",
            "action": "BUY",
            "limit_price": "0.59",
            "requested_shares": "8.238141",
            "target_notional_usd": "5",
            "selected_side": "up",
        },
    }
    (package / "prepared.json").write_text(json.dumps(prepared), encoding="utf-8")
    processed = tmp_path / "processed.json"
    processed.write_text("{}\n", encoding="utf-8")
    executor = tmp_path / "executor.py"
    executor.write_text("print('fixture')\n", encoding="utf-8")
    wrapper = tmp_path / "executor.sh"
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    wrapper.chmod(0o755)
    release = tmp_path / "RELEASE-MANIFEST.json"
    release.write_text(json.dumps({"commit_sha": "a" * 40}), encoding="utf-8")
    etc = tmp_path / "etc"
    etc.mkdir()
    activation = etc / "activation.json"
    kill = etc / "KILL"
    kill.write_text("engaged\n", encoding="utf-8")
    executor_sha = hashlib.sha256(executor.read_bytes()).hexdigest()
    return package, processed, executor, wrapper, release, activation, kill, executor_sha


def _contract(executor_sha: str) -> dict[str, object]:
    return {
        "intent_id": "intent-1",
        "prediction_id": "prediction-1",
        "paper_order_id": "paper-1",
        "request_sha256": "1" * 64,
        "source_truth_sha256": "2" * 64,
        "dispatch_claim_sha256": "3" * 64,
        "package_manifest_sha256": "4" * 64,
        "expires_at": "2026-09-28T14:00:20+00:00",
        "executor_sha256": executor_sha,
    }


def _safe_health(executor_sha: str, *, armed: bool) -> dict[str, object]:
    return {
        "status": "ok",
        "geoblock": {"blocked": False, "country": "ZA", "region": "GP"},
        "account": {
            "clean_for_canary": True,
            "open_order_count": 0,
            "collateral_balance_usd": "25.00",
        },
        "activation_valid": armed,
        "kill_switch_engaged": not armed,
        "submission_ready": armed,
        "live_order_submitted": False,
        "executor_sha256": executor_sha,
    }


def test_fill_guard_rejects_stale_price_before_one_shot_attempt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, processed, executor, wrapper, release, activation, kill, executor_sha = (
        _prepared_package(tmp_path)
    )
    monkeypatch.setattr(
        consumer,
        "verify_privileged_handoff_contract",
        lambda **_: dict(_contract(executor_sha)),
    )
    calls: list[dict[str, object]] = []

    def runner(args, *, input, stdout, stderr, check, timeout):
        payload = json.loads(input)
        calls.append(payload)
        if payload["action"] == "health":
            return _completed(_safe_health(executor_sha, armed=False))
        if payload["action"] == "quote":
            return _completed(
                {
                    "status": "ok",
                    "fill_guard_version": "fresh_marketable_depth_v1",
                    "marketable": False,
                    "reason": "fresh_best_ask_above_frozen_limit",
                    "token_id": "token-1",
                    "limit_price": "0.59",
                    "requested_shares": "8.238141",
                    "best_ask": "0.61",
                    "marketable_depth": "0",
                    "network_submission_attempt_consumed": False,
                    "real_order_submitted": False,
                }
            )
        raise AssertionError("submit must not be invoked for stale quote")

    state = tmp_path / "state"
    result = consumer.execute_authorized_package_once(
        package_dir=package,
        processed_receipt_path=processed,
        executor_path=executor,
        executor_wrapper_path=wrapper,
        release_manifest_path=release,
        activation_path=activation,
        kill_switch_path=kill,
        state_root=state,
        expected_executor_sha256=executor_sha,
        observed_at=datetime(2026, 9, 28, 14, 0, 4, tzinfo=UTC),
        expected_owner_uid=os.getuid(),
        runner=runner,
    )

    assert result["status"] == "fill_guard_rejected"
    assert result["fill_guard_reason"] == "fresh_best_ask_above_frozen_limit"
    assert result["network_submission_attempt_consumed"] is False
    assert result["authorization_slot_consumed"] is False
    assert result["real_order_submitted"] is False
    assert [call["action"] for call in calls] == ["health", "quote"]
    assert kill.exists()
    assert not activation.exists()
    assert not (state / consumer.SECOND_CANARY_ATTEMPT_BASENAME).exists()
    assert len(list(state.glob("*.attempt.json"))) == 0
    assert len(list(state.glob("*.result.json"))) == 0
    assert len(list(state.glob("*.failure.json"))) == 0
    assert len(list(state.glob("*.fill_guard.json"))) == 1

    again = consumer.execute_authorized_package_once(
        package_dir=package,
        processed_receipt_path=processed,
        executor_path=executor,
        executor_wrapper_path=wrapper,
        release_manifest_path=release,
        activation_path=activation,
        kill_switch_path=kill,
        state_root=state,
        expected_executor_sha256=executor_sha,
        observed_at=datetime(2026, 9, 28, 14, 0, 5, tzinfo=UTC),
        expected_owner_uid=os.getuid(),
        runner=runner,
    )
    assert again["status"] == "already_terminal"
    assert again["terminal_reason"] == "package_fill_guard_rejected"


def test_fill_guard_marketability_pass_preserves_existing_one_shot_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, processed, executor, wrapper, release, activation, kill, executor_sha = (
        _prepared_package(tmp_path)
    )
    monkeypatch.setattr(
        consumer,
        "verify_privileged_handoff_contract",
        lambda **_: dict(_contract(executor_sha)),
    )
    calls: list[dict[str, object]] = []
    health_count = 0

    def runner(args, *, input, stdout, stderr, check, timeout):
        nonlocal health_count
        payload = json.loads(input)
        calls.append(payload)
        if payload["action"] == "health":
            health_count += 1
            return _completed(_safe_health(executor_sha, armed=health_count == 2))
        if payload["action"] == "quote":
            return _completed(
                {
                    "status": "ok",
                    "fill_guard_version": "fresh_marketable_depth_v1",
                    "marketable": True,
                    "reason": "fresh_limit_fully_marketable",
                    "token_id": "token-1",
                    "limit_price": "0.59",
                    "requested_shares": "8.238141",
                    "best_ask": "0.58",
                    "marketable_depth": "20",
                    "network_submission_attempt_consumed": False,
                    "real_order_submitted": False,
                }
            )
        return _completed(
            {
                "accepted": True,
                "external_order_id": "order-1",
                "status": "accepted",
                "code": "accepted",
                "geoblock": {"blocked": False},
                "account_preflight": {"open_order_count": 0},
                "intent_id": "intent-1",
                "prediction_id": "prediction-1",
                "paper_order_id": "paper-1",
                "authorization_id": payload["authorization_id"],
                "request_sha256": "1" * 64,
                "executor_sha256": executor_sha,
                "cancellation": {"cancelled": True, "status": "cancelled"},
            }
        )

    moments = iter(
        [
            datetime(2026, 9, 28, 14, 0, 5, tzinfo=UTC),
            datetime(2026, 9, 28, 14, 0, 6, tzinfo=UTC),
            datetime(2026, 9, 28, 14, 0, 7, tzinfo=UTC),
            datetime(2026, 9, 28, 14, 0, 8, tzinfo=UTC),
        ]
    )
    state = tmp_path / "state"
    result = consumer.execute_authorized_package_once(
        package_dir=package,
        processed_receipt_path=processed,
        executor_path=executor,
        executor_wrapper_path=wrapper,
        release_manifest_path=release,
        activation_path=activation,
        kill_switch_path=kill,
        state_root=state,
        expected_executor_sha256=executor_sha,
        observed_at=datetime(2026, 9, 28, 14, 0, 4, tzinfo=UTC),
        expected_owner_uid=os.getuid(),
        runner=runner,
        now_fn=lambda: next(moments),
    )

    assert result["status"] == "executor_result_recorded"
    assert result["accepted"] is True
    assert result["fill_guard_version"] == "fresh_marketable_depth_v1"
    assert result["fill_guard_best_ask"] == "0.58"
    assert result["network_submission_attempt_consumed"] is True
    assert [call["action"] for call in calls] == ["health", "quote", "health", "submit"]
    assert (state / consumer.SECOND_CANARY_ATTEMPT_BASENAME).is_file()
    assert len(list(state.glob("*.result.json"))) == 1


def test_executor_quote_requires_full_marketable_depth_without_repricing(
    monkeypatch,
) -> None:
    module = _load_executor_module()
    monkeypatch.setattr(
        module,
        "_geoblock",
        lambda: {"blocked": False, "country": "ZA", "region": "GP", "direct_url": ""},
    )

    class Client:
        def __init__(self, asks):
            self.asks = asks
            self.calls = 0

        def get_order_book(self, *, token_id: str):
            assert token_id == "token-1"
            self.calls += 1
            return SimpleNamespace(asks=tuple(self.asks))

    request = {
        "request": {
            "token_id": "token-1",
            "action": "BUY",
            "limit_price": "0.59",
            "requested_shares": "8.238141",
            "target_notional_usd": "5",
        }
    }

    client = Client(
        [
            SimpleNamespace(price=Decimal("0.61"), size=Decimal("100")),
            SimpleNamespace(price=Decimal("0.58"), size=Decimal("9")),
        ]
    )
    monkeypatch.setattr(module, "_client", lambda: client)
    quote = module._quote(request)
    assert quote["marketable"] is True
    assert quote["best_ask"] == "0.58"
    assert quote["marketable_depth"] == "9"
    assert client.calls == 1

    stale = Client([SimpleNamespace(price=Decimal("0.60"), size=Decimal("100"))])
    monkeypatch.setattr(module, "_client", lambda: stale)
    quote = module._quote(request)
    assert quote["marketable"] is False
    assert quote["reason"] == "fresh_best_ask_above_frozen_limit"

    thin = Client([SimpleNamespace(price=Decimal("0.58"), size=Decimal("2"))])
    monkeypatch.setattr(module, "_client", lambda: thin)
    quote = module._quote(request)
    assert quote["marketable"] is False
    assert quote["reason"] == "fresh_marketable_depth_below_requested_shares"


def test_candidate_sources_never_reprice_above_frozen_limit() -> None:
    executor_text = EXECUTOR.read_text(encoding="utf-8")
    consumer_text = Path(consumer.__file__).read_text(encoding="utf-8")
    for marker in (
        'FILL_GUARD_VERSION = "fresh_marketable_depth_v1"',
        'client.get_order_book(token_id=token_id)',
        'level_price <= limit_price',
        'marketable_depth >= requested_shares',
        '"fresh_best_ask_above_frozen_limit"',
        '"fresh_marketable_depth_below_requested_shares"',
        'elif action == "quote":',
    ):
        assert marker in executor_text
    for marker in (
        '{"action": "quote", "request": dict(request)}',
        '"fill_guard_rejected"',
        '"authorization_slot_consumed": False',
        '"network_submission_attempt_consumed": False',
        '"real_order_submitted": False',
    ):
        assert marker in consumer_text
    assert "limit_price +" not in executor_text
    assert "max(limit_price" not in executor_text
