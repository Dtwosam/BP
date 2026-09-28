from __future__ import annotations

from bp_engine.execution.fast_live_book import StreamingBookCache


def test_stream_book_requires_full_snapshot_before_deltas() -> None:
    cache = StreamingBookCache()
    cache.subscribe(["token-1"])
    cache._mark_connected()

    cache._apply_price_changes(
        {
            "price_changes": [
                {
                    "asset_id": "token-1",
                    "side": "SELL",
                    "price": "0.58",
                    "size": "20",
                }
            ]
        }
    )
    assert cache.snapshot("token-1") is None

    cache._replace_book(
        {
            "asset_id": "token-1",
            "asks": [
                {"price": "0.59", "size": "8"},
                {"price": "0.60", "size": "10"},
            ],
        }
    )
    assert cache.snapshot("token-1") == (("0.59", "8"), ("0.60", "10"))

    cache._apply_price_changes(
        {
            "price_changes": [
                {
                    "asset_id": "token-1",
                    "side": "SELL",
                    "price": "0.59",
                    "size": "0",
                },
                {
                    "asset_id": "token-1",
                    "side": "SELL",
                    "price": "0.58",
                    "size": "4",
                },
            ]
        }
    )
    assert cache.snapshot("token-1") == (("0.58", "4"), ("0.60", "10"))

    cache._set_disconnected()
    assert cache.snapshot("token-1") is None


def test_buy_side_change_does_not_mutate_asks() -> None:
    cache = StreamingBookCache()
    cache.subscribe(["token-1"])
    cache._mark_connected()
    cache._replace_book(
        {
            "asset_id": "token-1",
            "asks": [{"price": "0.59", "size": "8"}],
        }
    )

    cache._apply_price_changes(
        {
            "price_changes": [
                {
                    "asset_id": "token-1",
                    "side": "BUY",
                    "price": "0.58",
                    "size": "100",
                }
            ]
        }
    )
    assert cache.snapshot("token-1") == (("0.59", "8"),)

def test_stream_book_falls_back_when_exact_token_quote_is_stale(monkeypatch) -> None:
    clock = {"now": 100.0}
    monkeypatch.setattr(
        "bp_engine.execution.fast_live_book.time.monotonic",
        lambda: clock["now"],
    )
    cache = StreamingBookCache(quote_fresh_seconds=0.5)
    cache.subscribe(["token-1"])
    cache._mark_connected()
    cache._replace_book(
        {
            "asset_id": "token-1",
            "asks": [{"price": "0.59", "size": "8"}],
        }
    )
    assert cache.snapshot("token-1") == (("0.59", "8"),)

    clock["now"] += 0.26
    cache._mark_activity()
    assert cache.snapshot("token-1") is None

