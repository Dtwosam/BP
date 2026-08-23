from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from websockets.asyncio.client import connect

from bp_engine.collectors.bybit_ws import build_bybit_subscription, parse_bybit_message
from bp_engine.collectors.coinbase_ws import (
    build_coinbase_subscriptions,
    parse_coinbase_message,
)
from bp_engine.collectors.polymarket_ws import build_market_subscription, parse_polymarket_message
from bp_engine.collectors.websocket_runner import WebSocketCollectorRunner
from bp_engine.polymarket.discovery import discover_btc_markets
from bp_engine.polymarket.gamma import GammaClient
from bp_engine.recorder.models import RawEvent

OUTPUT = Path("tests/fixtures/recorder/live/recorder-smoke-capture.json")
REPORT = Path("tests/fixtures/recorder/live/recorder-smoke-report.json")


async def capture_one(runner: WebSocketCollectorRunner, *, timeout: float = 30.0) -> RawEvent:
    captured: list[RawEvent] = []
    stop = asyncio.Event()

    original_sink = runner.event_sink

    async def sink(event: RawEvent) -> None:
        result = original_sink(event)
        if asyncio.iscoroutine(result):
            await result
        captured.append(event)
        stop.set()

    runner.event_sink = sink
    await asyncio.wait_for(runner.run(stop), timeout=timeout)
    if not captured:
        raise RuntimeError(f"{runner.source}/{runner.stream} produced no market event")
    return captured[0]


async def main() -> None:
    now = datetime.now(UTC)
    markets = await discover_btc_markets(
        GammaClient(), now, horizons=("5m", "15m"), offsets=(0, 1)
    )
    active = [market for market in markets if market.active and not market.closed]
    asset_ids = sorted(
        {
            token
            for market in active
            for token in (market.up_token_id, market.down_token_id)
        }
    )
    if not asset_ids:
        raise RuntimeError("Gamma discovery returned no active BTC Up/Down token IDs")

    incidents: list[object] = []
    polymarket = WebSocketCollectorRunner(
        source="polymarket",
        stream="market",
        url="wss://ws-subscriptions-clob.polymarket.com/ws/market",
        connector=connect,
        subscription=build_market_subscription(asset_ids),
        parser=lambda message, received_at: parse_polymarket_message(
            message, received_at=received_at
        ),
        event_sink=lambda event: None,
        incident_sink=incidents.append,
        heartbeat_message="PING",
        heartbeat_interval_seconds=10,
    )
    bybit_spot = WebSocketCollectorRunner(
        source="bybit",
        stream="spot",
        url="wss://stream.bybit.com/v5/public/spot",
        connector=connect,
        subscription=build_bybit_subscription(
            ["orderbook.50.BTCUSDT", "publicTrade.BTCUSDT"]
        ),
        parser=lambda message, received_at: parse_bybit_message(
            message, venue="spot", received_at=received_at
        ),
        event_sink=lambda event: None,
        incident_sink=incidents.append,
        heartbeat_message={"op": "ping"},
        heartbeat_interval_seconds=20,
    )
    bybit_linear = WebSocketCollectorRunner(
        source="bybit",
        stream="linear",
        url="wss://stream.bybit.com/v5/public/linear",
        connector=connect,
        subscription=build_bybit_subscription(
            [
                "orderbook.50.BTCUSDT",
                "publicTrade.BTCUSDT",
                "tickers.BTCUSDT",
                "allLiquidation.BTCUSDT",
            ]
        ),
        parser=lambda message, received_at: parse_bybit_message(
            message, venue="linear", received_at=received_at
        ),
        event_sink=lambda event: None,
        incident_sink=incidents.append,
        heartbeat_message={"op": "ping"},
        heartbeat_interval_seconds=20,
    )

    coinbase_spot = WebSocketCollectorRunner(
        source="coinbase",
        stream="spot",
        url="wss://advanced-trade-ws.coinbase.com",
        connector=connect,
        subscription=build_coinbase_subscriptions(["BTC-USD"]),
        parser=lambda message, received_at: parse_coinbase_message(
            message, received_at=received_at
        ),
        event_sink=lambda event: None,
        incident_sink=incidents.append,
        heartbeat_message=None,
        heartbeat_interval_seconds=None,
    )

    pm_event, spot_event, linear_event, coinbase_event = await asyncio.gather(
        capture_one(polymarket),
        capture_one(bybit_spot),
        capture_one(bybit_linear),
        capture_one(coinbase_spot),
    )
    payload = {
        "captured_at": datetime.now(UTC).isoformat(),
        "discovered_polymarket_slugs": [market.slug for market in active],
        "events": [
            pm_event.model_dump(mode="json"),
            spot_event.model_dump(mode="json"),
            linear_event.model_dump(mode="json"),
            coinbase_event.model_dump(mode="json"),
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "status": "ok",
                "sources": ["polymarket", "bybit_spot", "bybit_linear", "coinbase_spot"],
            }
        )
    )


def write_report(status: str, **details: object) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "recorded_at": datetime.now(UTC).isoformat(),
        **details,
    }
    REPORT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        write_report("error", error_type=type(exc).__name__, error=str(exc))
        raise
    else:
        write_report("ok", sources=["polymarket", "bybit_spot", "bybit_linear", "coinbase_spot"])
