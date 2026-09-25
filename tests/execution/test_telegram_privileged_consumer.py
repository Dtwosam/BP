from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from bp_engine.execution import telegram_privileged_consumer as consumer


def _completed(payload: dict[str, object]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(
        args=["executor"],
        returncode=0,
        stdout=(json.dumps(payload) + "\n").encode(),
        stderr=b"",
    )


def test_privileged_consumer_invokes_executor_once_and_reengages_kill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package = tmp_path / ("a" * 64)
    package.mkdir()
    prepared = {
        "action": "submit",
        "intent_id": "intent-1",
        "prediction_id": "prediction-1",
        "paper_order_id": "paper-1",
        "market_end_at": "2026-09-25T12:01:00+00:00",
        "request": {
            "token_id": "token-1",
            "action": "BUY",
            "limit_price": "0.50",
            "requested_shares": "10",
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
    release.write_text(
        json.dumps({"commit_sha": "a" * 40}),
        encoding="utf-8",
    )
    etc = tmp_path / "etc"
    etc.mkdir()
    activation = etc / "activation.json"
    kill = etc / "KILL"
    kill.write_text("engaged\n", encoding="utf-8")
    state = tmp_path / "state"
    executor_sha = hashlib.sha256(executor.read_bytes()).hexdigest()

    contract = {
        "intent_id": "intent-1",
        "prediction_id": "prediction-1",
        "paper_order_id": "paper-1",
        "request_sha256": "1" * 64,
        "source_truth_sha256": "2" * 64,
        "dispatch_claim_sha256": "3" * 64,
        "package_manifest_sha256": "4" * 64,
        "expires_at": "2026-09-25T12:00:20+00:00",
        "executor_sha256": executor_sha,
    }
    monkeypatch.setattr(
        consumer,
        "verify_privileged_handoff_contract",
        lambda **_: dict(contract),
    )

    calls: list[dict[str, object]] = []
    health_count = 0

    def runner(args, *, input, stdout, stderr, check, timeout):
        nonlocal health_count
        payload = json.loads(input)
        calls.append(payload)
        if payload["action"] == "health":
            health_count += 1
            return _completed(
                {
                    "status": "ok",
                    "geoblock": {"blocked": False, "country": "ZA", "region": "GP"},
                    "account": {
                        "clean_for_canary": True,
                        "open_order_count": 0,
                        "collateral_balance_usd": "25.00",
                    },
                    "activation_valid": health_count == 2,
                    "kill_switch_engaged": health_count == 1,
                    "submission_ready": health_count == 2,
                    "executor_sha256": executor_sha,
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
            datetime(2026, 9, 25, 12, 0, 5, tzinfo=UTC),
            datetime(2026, 9, 25, 12, 0, 6, tzinfo=UTC),
            datetime(2026, 9, 25, 12, 0, 7, tzinfo=UTC),
            datetime(2026, 9, 25, 12, 0, 8, tzinfo=UTC),
        ]
    )
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
        observed_at=datetime(2026, 9, 25, 12, 0, 4, tzinfo=UTC),
        expected_owner_uid=os.getuid(),
        runner=runner,
        now_fn=lambda: next(moments),
    )

    assert result["status"] == "executor_result_recorded"
    assert result["accepted"] is True
    assert result["network_submission_attempt_consumed"] is True
    assert result["retry_allowed"] is False
    assert result["official_reconciliation_required"] is True
    assert [call["action"] for call in calls] == ["health", "health", "submit"]
    assert kill.exists()
    assert activation.exists()
    assert (state / consumer.SECOND_CANARY_ATTEMPT_BASENAME).is_file()
    assert len(list(state.glob("*.attempt.json"))) == 2
    assert len(list(state.glob("*.result.json"))) == 1


def test_privileged_consumer_never_retries_after_attempt_marker(
    tmp_path: Path,
) -> None:
    package = tmp_path / ("b" * 64)
    package.mkdir()
    processed = tmp_path / "processed.json"
    processed.write_text("{}\n", encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    (state / f"{package.name}.attempt.json").write_text("{}\n", encoding="utf-8")

    result = consumer.execute_authorized_package_once(
        package_dir=package,
        processed_receipt_path=processed,
        executor_path=tmp_path / "executor.py",
        executor_wrapper_path=tmp_path / "executor.sh",
        release_manifest_path=tmp_path / "manifest.json",
        activation_path=tmp_path / "activation.json",
        kill_switch_path=tmp_path / "KILL",
        state_root=state,
        expected_executor_sha256="0" * 64,
        observed_at=datetime(2026, 9, 25, 12, 0, tzinfo=UTC),
        expected_owner_uid=os.getuid(),
    )

    assert result["status"] == "already_terminal"
    assert result["retry_allowed"] is False
    assert result["executor_invoked"] is True


def test_privileged_consumer_blocks_different_package_after_global_slot_consumed(
    tmp_path: Path,
) -> None:
    package = tmp_path / ("c" * 64)
    package.mkdir()
    processed = tmp_path / "processed.json"
    processed.write_text("{}\n", encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    (state / consumer.SECOND_CANARY_ATTEMPT_BASENAME).write_text(
        "{}\n",
        encoding="utf-8",
    )

    result = consumer.execute_authorized_package_once(
        package_dir=package,
        processed_receipt_path=processed,
        executor_path=tmp_path / "executor.py",
        executor_wrapper_path=tmp_path / "executor.sh",
        release_manifest_path=tmp_path / "manifest.json",
        activation_path=tmp_path / "activation.json",
        kill_switch_path=tmp_path / "KILL",
        state_root=state,
        expected_executor_sha256="0" * 64,
        observed_at=datetime(2026, 9, 25, 12, 0, tzinfo=UTC),
        expected_owner_uid=os.getuid(),
    )

    assert result["status"] == "already_terminal"
    assert result["terminal_reason"] == "second_canary_authorization_consumed"
    assert result["authorization_slot_consumed"] is True
    assert result["executor_invoked"] is False
    assert result["retry_allowed"] is False


def test_privileged_consumer_source_has_no_second_trading_implementation() -> None:
    source = Path(consumer.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "polymarket",
        "create_limit_order",
        "post_order",
        "cancel_order",
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
    ):
        assert forbidden not in source
    assert "/opt/bp-canary/executor.py" not in source
