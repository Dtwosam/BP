from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from bp_engine.execution import fast_live_executor as module
from bp_engine.execution.fast_live import FastLiveError, FastLiveRetryableError
from bp_engine.execution.fast_live_executor import (
    FastLiveExecutor,
    SafetyCache,
    SafetySnapshot,
    execute_with_bounded_pre_attempt_retry,
)


class FakeAccepted:
    def __init__(self, order_id: str, status: str = "matched") -> None:
        self.order_id = order_id
        self.status = status


class FakeRejected:
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message


class FakeCancelResponse:
    def __init__(self, order_id: str) -> None:
        self.canceled = (order_id,)
        self.not_canceled: dict[str, str] = {}


class FakeItems:
    def __init__(self, items=()) -> None:
        self.items = tuple(items)

    def iter_items(self):
        return iter(self.items)


class FakeBookCache:
    def __init__(self, levels: tuple[tuple[str, str], ...] | None) -> None:
        self.levels = levels
        self.calls: list[str] = []

    def snapshot(self, token_id: str):
        self.calls.append(token_id)
        return self.levels


class FakeClient:
    def __init__(self, asks: tuple[tuple[str, str], ...]) -> None:
        self.asks = asks
        self.calls: list[str] = []

    def get_order_book(self, *, token_id: str):
        self.calls.append("book")
        return SimpleNamespace(
            asks=tuple(SimpleNamespace(price=p, size=s) for p, s in self.asks)
        )

    def create_limit_order(self, *, token_id, price, size, side):
        self.calls.append("sign")
        return {
            "token_id": token_id,
            "price": price,
            "size": size,
            "side": side,
        }

    def post_order(self, signed_order):
        self.calls.append("post")
        return FakeAccepted("order-fast-1")

    def cancel_order(self, *, order_id: str):
        self.calls.append("cancel")
        return FakeCancelResponse(order_id)

    def list_open_orders(self):
        self.calls.append("open_orders")
        return FakeItems()

    def list_account_trades(self):
        self.calls.append("trades")
        return FakeItems()


def _verified(now: datetime) -> dict[str, object]:
    return {
        "authorization_id": "fast-auth-1",
        "intent_id": "intent-fast-1",
        "request_id": "request-fast-1",
        "prediction_id": "prediction-fast-1",
        "paper_order_id": "paper-fast-1",
        "request_sha256": "1" * 64,
        "prepared_sha256": "2" * 64,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=2)).isoformat(),
        "request": {
            "token_id": "token-fast-1",
            "action": "BUY",
            "selected_side": "up",
            "limit_price": "0.59",
            "requested_shares": "8.238141",
            "target_notional_usd": "5",
        },
    }


def _cache(now: datetime) -> SafetyCache:
    cache = SafetyCache()
    cache.replace(
        SafetySnapshot(
            observed_at=now,
            geoblock_blocked=False,
            geoblock_country="ZA",
            open_order_count=0,
            collateral_balance_usd=Decimal("25"),
        )
    )
    return cache


def test_marketable_fast_path_consumes_once_then_posts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(module.polymarket, "AcceptedOrder", FakeAccepted)
    monkeypatch.setattr(module.polymarket, "RejectedOrder", FakeRejected)
    monkeypatch.setattr(module.polymarket, "CancelOrdersResponse", FakeCancelResponse)
    now = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
    client = FakeClient((("0.57", "3"), ("0.58", "4"), ("0.59", "10")))
    kill = tmp_path / "etc" / "KILL"
    executor = FastLiveExecutor(
        client=client,
        safety_cache=_cache(now),
        state_root=tmp_path / "state",
        kill_switch_path=kill,
        order_ttl_seconds=Decimal("0"),
        official_stability_seconds=0,
        now_fn=lambda: now,
    )

    result = executor.execute(_verified(now))

    assert result["status"] == "accepted"
    assert result["accepted"] is True
    assert result["external_order_id"] == "order-fast-1"
    assert result["network_submission_attempt_consumed"] is True
    assert result["real_order_submitted"] is True
    assert client.calls == [
        "sign",
        "book",
        "post",
        "cancel",
        "open_orders",
        "trades",
        "open_orders",
        "trades",
    ]
    assert executor.attempt_path.is_file()
    assert kill.is_file()
    attempt = json.loads(executor.attempt_path.read_text(encoding="utf-8"))
    assert attempt["intent_id"] == "intent-fast-1"

    duplicate = executor.execute(_verified(now))
    assert duplicate["status"] == "accepted"
    assert duplicate["external_order_id"] == "order-fast-1"
    assert duplicate["replayed_result"] is True
    assert client.calls == [
        "sign",
        "book",
        "post",
        "cancel",
        "open_orders",
        "trades",
        "open_orders",
        "trades",
    ]


def test_replay_finishes_pending_cancellation_without_second_post(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(module.polymarket, "CancelOrdersResponse", FakeCancelResponse)
    now = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
    client = FakeClient((("0.58", "20"),))
    executor = FastLiveExecutor(
        client=client,
        safety_cache=_cache(now),
        state_root=tmp_path / "state",
        kill_switch_path=tmp_path / "KILL",
        order_ttl_seconds=Decimal("0"),
        official_stability_seconds=0,
        continuous_session=True,
        now_fn=lambda: now,
    )
    verified = _verified(now)
    attempt_path = executor.attempt_path_for(verified)
    result_path = executor.result_path_for(verified)
    attempt = {
        "intent_id": verified["intent_id"],
        "prediction_id": verified["prediction_id"],
        "paper_order_id": verified["paper_order_id"],
        "request_sha256": verified["request_sha256"],
        "network_submission_attempt_consumed": True,
        "real_order_submitted": True,
    }
    attempt_path.write_text(json.dumps(attempt), encoding="utf-8")
    preliminary = {
        **attempt,
        "status": "accepted",
        "accepted": True,
        "external_order_id": "order-fast-1",
        "cancellation_pending": True,
        "marketability": {
            "requested_shares": "8.238141",
        },
    }
    result_path.write_text(json.dumps(preliminary), encoding="utf-8")

    recovered = executor.execute(verified)

    assert recovered["status"] == "accepted"
    assert recovered["replayed_result"] is True
    assert recovered["cancellation_pending"] is False
    assert recovered["cancellation"]["cancelled"] is True
    assert (
        recovered["official_reconciliation"][
            "official_reconciliation_complete"
        ]
        is True
    )
    assert client.calls == [
        "cancel",
        "open_orders",
        "trades",
        "open_orders",
        "trades",
    ]
    assert "post" not in client.calls
    persisted = json.loads(result_path.read_text(encoding="utf-8"))
    assert persisted["cancellation_pending"] is False


def test_continuous_session_uses_one_attempt_marker_per_intent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(module.polymarket, "AcceptedOrder", FakeAccepted)
    monkeypatch.setattr(module.polymarket, "RejectedOrder", FakeRejected)
    monkeypatch.setattr(module.polymarket, "CancelOrdersResponse", FakeCancelResponse)
    now = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
    client = FakeClient((("0.58", "20"),))
    kill = tmp_path / "KILL"
    executor = FastLiveExecutor(
        client=client,
        safety_cache=_cache(now),
        state_root=tmp_path / "state",
        kill_switch_path=kill,
        order_ttl_seconds=Decimal("0"),
        official_stability_seconds=0,
        continuous_session=True,
        now_fn=lambda: now,
    )

    first = _verified(now)
    first_result = executor.execute(first)
    assert first_result["status"] == "accepted"
    assert executor.attempt_path_for(first).is_file()
    assert executor.result_path_for(first).is_file()
    assert not executor.attempt_path.exists()
    assert not executor.result_path.exists()
    assert not kill.exists()

    replay = executor.execute(first)
    assert replay["replayed_result"] is True
    assert client.calls.count("post") == 1

    second = _verified(now)
    second["intent_id"] = "intent-fast-2"
    second["request_id"] = "request-fast-2"
    second["prediction_id"] = "prediction-fast-2"
    second["paper_order_id"] = "paper-fast-2"
    second["request_sha256"] = "3" * 64
    second["prepared_sha256"] = "4" * 64
    second_request = dict(second["request"])
    second_request["token_id"] = "token-fast-2"
    second["request"] = second_request

    second_result = executor.execute(second)
    assert second_result["status"] == "accepted"
    assert executor.attempt_path_for(second).is_file()
    assert executor.result_path_for(second).is_file()
    assert executor.attempt_path_for(second) != executor.attempt_path_for(first)
    assert executor.result_path_for(second) != executor.result_path_for(first)
    assert not executor.result_path.exists()
    assert client.calls.count("post") == 2
    assert not kill.exists()


def test_streamed_book_skips_http_quote_round_trip(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(module.polymarket, "AcceptedOrder", FakeAccepted)
    monkeypatch.setattr(module.polymarket, "RejectedOrder", FakeRejected)
    monkeypatch.setattr(module.polymarket, "CancelOrdersResponse", FakeCancelResponse)
    now = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
    client = FakeClient((("0.99", "1"),))
    book_cache = FakeBookCache((("0.58", "20"),))
    executor = FastLiveExecutor(
        client=client,
        safety_cache=_cache(now),
        book_cache=book_cache,
        state_root=tmp_path / "state",
        kill_switch_path=tmp_path / "KILL",
        order_ttl_seconds=Decimal("0"),
        now_fn=lambda: now,
    )

    result = executor.execute(_verified(now))

    assert result["status"] == "accepted"
    assert result["quote_source"] == "stream"
    assert client.calls == [
        "sign",
        "post",
        "cancel",
        "open_orders",
        "trades",
        "open_orders",
        "trades",
    ]
    assert book_cache.calls == ["token-fast-1"]


def test_stale_or_thin_book_never_consumes_attempt(tmp_path: Path) -> None:
    now = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
    client = FakeClient((("0.60", "20"),))
    executor = FastLiveExecutor(
        client=client,
        safety_cache=_cache(now),
        state_root=tmp_path / "state",
        kill_switch_path=tmp_path / "KILL",
        order_ttl_seconds=Decimal("0"),
        now_fn=lambda: now,
    )

    result = executor.execute(_verified(now))

    assert result["status"] == "fresh_book_rejected"
    assert result["network_submission_attempt_consumed"] is False
    assert result["real_order_submitted"] is False
    assert client.calls == ["sign", "book"]
    assert not executor.attempt_path.exists()


def test_safety_must_be_fresh_before_quote(tmp_path: Path) -> None:
    now = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
    cache = SafetyCache()
    cache.replace(
        SafetySnapshot(
            observed_at=now - timedelta(seconds=2),
            geoblock_blocked=False,
            geoblock_country="ZA",
            open_order_count=0,
            collateral_balance_usd=Decimal("25"),
        )
    )
    client = FakeClient((("0.58", "20"),))
    executor = FastLiveExecutor(
        client=client,
        safety_cache=cache,
        state_root=tmp_path / "state",
        kill_switch_path=tmp_path / "KILL",
        now_fn=lambda: now,
    )

    with pytest.raises(FastLiveError, match="safety snapshot stale"):
        executor.execute(_verified(now))
    assert client.calls == []
    assert not executor.attempt_path.exists()


def test_existing_kill_switch_blocks_before_quote(tmp_path: Path) -> None:
    now = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
    kill = tmp_path / "KILL"
    kill.write_text("stop\n", encoding="utf-8")
    client = FakeClient((("0.58", "20"),))
    executor = FastLiveExecutor(
        client=client,
        safety_cache=_cache(now),
        state_root=tmp_path / "state",
        kill_switch_path=kill,
        now_fn=lambda: now,
    )

    with pytest.raises(FastLiveError, match="kill switch engaged"):
        executor.execute(_verified(now))
    assert client.calls == []

def test_http_quote_failure_is_retryable_before_attempt(tmp_path: Path) -> None:
    now = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)

    class QuoteFailClient(FakeClient):
        def get_order_book(self, *, token_id: str):
            self.calls.append("book")
            raise RuntimeError("temporary quote failure")

    client = QuoteFailClient(())
    executor = FastLiveExecutor(
        client=client,
        safety_cache=_cache(now),
        state_root=tmp_path / "state",
        kill_switch_path=tmp_path / "KILL",
        now_fn=lambda: now,
    )

    with pytest.raises(
        FastLiveRetryableError,
        match="fresh order book unavailable",
    ):
        executor.execute(_verified(now))

    assert client.calls == ["sign", "book"]
    assert not executor.attempt_path.exists()

def test_bounded_pre_attempt_retry_stays_local_and_unconsumed(tmp_path: Path) -> None:
    now = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
    clock = {"now": now}
    calls = {"count": 0}

    class RetryExecutor:
        attempt_path = tmp_path / "attempt.json"

        def execute(self, verified):
            calls["count"] += 1
            if calls["count"] < 3:
                raise FastLiveRetryableError("temporary pre-attempt condition")
            return {
                "status": "fresh_book_rejected",
                "intent_id": verified["intent_id"],
                "request_sha256": verified["request_sha256"],
                "network_submission_attempt_consumed": False,
                "real_order_submitted": False,
            }

    verified = _verified(now)
    verified["expires_at"] = (now + timedelta(seconds=1)).isoformat()

    def sleep_fn(seconds: float) -> None:
        clock["now"] += timedelta(seconds=seconds)

    result = execute_with_bounded_pre_attempt_retry(
        RetryExecutor(),
        verified,
        now_fn=lambda: clock["now"],
        sleep_fn=sleep_fn,
        retry_sleep_seconds=0.02,
    )

    assert calls["count"] == 3
    assert result["status"] == "fresh_book_rejected"
    assert result["pre_attempt_retry_count"] == 2
    assert result["network_submission_attempt_consumed"] is False
    assert not (tmp_path / "attempt.json").exists()

def test_attempt_without_result_recovers_as_submission_unknown_without_repost(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
    client = FakeClient((("0.58", "20"),))
    executor = FastLiveExecutor(
        client=client,
        safety_cache=_cache(now),
        state_root=tmp_path / "state",
        kill_switch_path=tmp_path / "KILL",
        now_fn=lambda: now,
    )
    attempt = {
        "schema_version": 1,
        "status": "network_submission_attempt_starting",
        "authorization_id": "fast-auth-1",
        "intent_id": "intent-fast-1",
        "prediction_id": "prediction-fast-1",
        "paper_order_id": "paper-fast-1",
        "request_sha256": "1" * 64,
        "started_at": now.isoformat(),
    }
    executor.attempt_path.write_text(
        json.dumps(attempt),
        encoding="utf-8",
    )

    result = executor.execute(_verified(now))

    assert result["status"] == "submission_unknown"
    assert result["network_submission_attempt_consumed"] is True
    assert result["real_order_submitted"] is True
    assert result["recovered_from_attempt_marker"] is True
    assert client.calls == []

def test_official_probe_confirms_partial_fill_without_assuming_settlement() -> None:
    trade = SimpleNamespace(
        id="trade-fast-1",
        taker_order_id="order-fast-1",
        size="4",
        price="0.58",
        status="CONFIRMED",
        maker_orders=(),
    )

    class FillClient(FakeClient):
        def list_account_trades(self):
            self.calls.append("trades")
            return FakeItems((trade,))

    client = FillClient(())
    official = module.probe_official_order_state(
        client,
        order_id="order-fast-1",
        requested_shares=Decimal("8.238141"),
        stability_seconds=0,
    )

    assert official["snapshot_stable"] is True
    assert official["order_still_open"] is False
    assert official["open_order_count"] == 0
    assert official["matching_trade_count"] == 1
    assert official["confirmed_filled_shares"] == "4"
    assert official["confirmed_filled_notional_usd"] == "2.32"
    assert official["fill_state"] == "confirmed_fill"
    assert official["official_reconciliation_complete"] is True



def test_presign_prepare_does_not_post_until_execute(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(module.polymarket, "AcceptedOrder", FakeAccepted)
    monkeypatch.setattr(module.polymarket, "RejectedOrder", FakeRejected)
    monkeypatch.setattr(module.polymarket, "CancelOrdersResponse", FakeCancelResponse)
    now = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
    client = FakeClient((("0.58", "20"),))
    executor = FastLiveExecutor(
        client=client,
        safety_cache=_cache(now),
        state_root=tmp_path / "state",
        kill_switch_path=tmp_path / "KILL",
        order_ttl_seconds=Decimal("0"),
        official_stability_seconds=0,
        now_fn=lambda: now,
    )

    prepared = executor.prepare_order(_verified(now))

    assert client.calls == ["sign"]
    assert not executor.attempt_path.exists()

    result = executor.execute(_verified(now), prepared_order=prepared)

    assert result["status"] == "accepted"
    assert client.calls[0] == "sign"
    assert client.calls.count("sign") == 1
    assert client.calls.index("post") > client.calls.index("book")
    assert executor.attempt_path.is_file()


def test_presigned_preview_order_can_join_same_request_final_intent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(module.polymarket, "AcceptedOrder", FakeAccepted)
    monkeypatch.setattr(module.polymarket, "RejectedOrder", FakeRejected)
    monkeypatch.setattr(module.polymarket, "CancelOrdersResponse", FakeCancelResponse)
    now = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
    client = FakeClient((("0.58", "20"),))
    executor = FastLiveExecutor(
        client=client,
        safety_cache=_cache(now),
        state_root=tmp_path / "state",
        kill_switch_path=tmp_path / "KILL",
        order_ttl_seconds=Decimal("0"),
        official_stability_seconds=0,
        continuous_session=True,
        now_fn=lambda: now,
    )

    preview = _verified(now)
    preview["intent_id"] = "candidate-fast-preview"
    prepared = executor.prepare_order(preview)

    final = _verified(now)
    final["intent_id"] = "intent-fast-final"
    final["risk_decision_id"] = "risk-fast-final"
    result = executor.execute(final, prepared_order=prepared)

    assert result["status"] == "accepted"
    assert result["intent_id"] == "intent-fast-final"
    assert client.calls.count("sign") == 1
    assert client.calls.count("post") == 1
    assert executor.attempt_path_for(final).is_file()
    assert not executor.attempt_path_for(preview).exists()


def test_presigned_order_cannot_be_reused_for_different_request(tmp_path: Path) -> None:
    now = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
    client = FakeClient((("0.58", "20"),))
    executor = FastLiveExecutor(
        client=client,
        safety_cache=_cache(now),
        state_root=tmp_path / "state",
        kill_switch_path=tmp_path / "KILL",
        now_fn=lambda: now,
    )
    verified = _verified(now)
    prepared = executor.prepare_order(verified)
    changed = _verified(now)
    changed["request_sha256"] = "9" * 64

    with pytest.raises(FastLiveError, match="prepared order request hash mismatch"):
        executor.execute(changed, prepared_order=prepared)

    assert "post" not in client.calls
    assert not executor.attempt_path.exists()
