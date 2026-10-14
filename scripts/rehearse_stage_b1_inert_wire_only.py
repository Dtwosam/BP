"""Offline-only Bybit-linear BULK wire/parser seam rehearsal.

The ONLY connector is a fixed in-memory fake. No real endpoint, real
WebSocket dialer, recorder factory, queue, database, service, host or
operator launcher is used. This is a diagnostic capability boundary
test, not a live source observation or an approved one-shot process.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from bp_engine.collectors.bybit_ws import (
    build_bybit_subscription,
    parse_bybit_message,
)
from bp_engine.collectors.websocket_runner import WebSocketCollectorRunner
from bp_engine.recorder.stage_b1_observer import StageB1BulkObserver

TOPICS = (
    "orderbook.50.BTCUSDT",
    "publicTrade.BTCUSDT",
    "allLiquidation.BTCUSDT",
)
SCENARIOS = (
    "bulk_all",
    "ack_only",
    "parse_failure",
    "ticker_excluded",
    "subscribe_rejected",
)
_NOW = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
_TS = int(_NOW.timestamp() * 1000)
_FAKE_URL = "fixed-memory-fixture-only"
_MAX_FRAMES = 5


def _frames(scenario: str) -> tuple[dict[str, object], ...]:
    if scenario not in SCENARIOS:
        raise ValueError("only fixed offline Bybit bulk scenarios are permitted")
    ack: dict[str, object] = {
        "op": "subscribe", "success": scenario != "subscribe_rejected",
        "ret_msg": "PRIVATE_ACK_TEXT_NOT_FOR_REPORT",
    }
    trade = {
        "topic": "publicTrade.BTCUSDT", "ts": _TS,
        "data": [{"s": "BTCUSDT", "p": "PRIVATE_PRICE_NOT_FOR_REPORT"}],
    }
    orderbook = {
        "topic": "orderbook.50.BTCUSDT", "type": "delta", "cts": _TS,
        "data": {"s": "BTCUSDT", "b": [], "a": []},
    }
    liquidation = {
        "topic": "allLiquidation.BTCUSDT", "ts": _TS,
        "data": [{"s": "BTCUSDT", "S": "Buy"}],
    }
    if scenario == "bulk_all":
        frames = (ack, trade, orderbook, liquidation)
    elif scenario == "parse_failure":
        frames = (ack, {**trade, "ts": "PRIVATE_INVALID_TIMESTAMP"})
    elif scenario == "ticker_excluded":
        frames = (ack, {
            "topic": "tickers.BTCUSDT", "ts": _TS,
            "data": {"s": "BTCUSDT", "openInterest": "PRIVATE_TICKER"},
        })
    else:
        frames = (ack,)
    assert len(frames) <= _MAX_FRAMES
    return frames


class _FakeSocket:
    def __init__(self, frames: tuple[dict[str, object], ...], stop: asyncio.Event):
        self._frames = frames
        self._stop = stop
        self._next = 0
        self.sent: list[str] = []
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> _FakeSocket:
        self.entered = True
        return self

    async def __aexit__(self, *unused: object) -> None:
        self.exited = True

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        if self._next >= len(self._frames):
            raise RuntimeError("fixed fake frames exhausted")
        frame = self._frames[self._next]
        self._next += 1
        if self._next == len(self._frames):
            self._stop.set()
        return json.dumps(frame)


async def run_fake_wire_only(scenario: str) -> dict[str, object]:
    """Use real runner/parser, but the only socket is a known in-memory fake."""
    frames = _frames(scenario)
    stop = asyncio.Event()
    clock = [0.0]
    observer = StageB1BulkObserver(duration_seconds=1, monotonic=lambda: clock[0])
    parser = observer.wrap_parser(
        lambda payload, received_at: parse_bybit_message(
            payload, venue="linear", received_at=received_at
        )
    )
    fake = _FakeSocket(frames, stop)
    discarded_events = 0
    incident_types: list[str] = []

    def only_fake_connector(url: str) -> _FakeSocket:
        if url != _FAKE_URL:
            raise ValueError("non-fixture socket URL rejected")
        return fake

    def discard_event(_event: Any) -> None:
        nonlocal discarded_events
        discarded_events += 1
        # There is intentionally no queue, DB sink, serialization or logging.

    def count_incident(incident: Any) -> None:
        # Never propagate arbitrary incident details or exception text.
        incident_types.append(incident.incident_type)

    runner = WebSocketCollectorRunner(
        source="bybit",
        stream="linear",
        url=_FAKE_URL,
        connector=only_fake_connector,
        subscription=build_bybit_subscription(TOPICS),
        parser=parser,
        event_sink=discard_event,
        incident_sink=count_incident,
        heartbeat_message=None,
        heartbeat_interval_seconds=None,
        now=lambda: _NOW,
        component_name="bybit_linear_fixed_fake_only",
    )
    # A coroutine wait bound is NOT an OS process lifetime guarantee.
    await asyncio.wait_for(runner.run(stop), timeout=2.0)
    clock[0] = 1.0
    observed = observer.snapshot()
    wire = observed["wire_messages_by_topic_class"]
    parsed = observed["parsed_events_by_class"]
    if observed["parser_errors"]:
        classification = "fake_parser_exception"
    elif sum(wire.values()) == 0:
        classification = "fake_no_bulk_frames"
    elif sum(parsed.values()) == 0:
        classification = "fake_bulk_frames_without_parsed_events"
    else:
        classification = "fake_bulk_frames_parsed_downstream_unknown"

    # The observer carries only fixed aggregate counters and flags.
    return {
        "report": "phase14_b1_inert_bulk_wire_seam_v1",
        "status": "OFFLINE_FIXED_FAKE_WIRE_ONLY_NO_GO",
        "scenario": scenario,
        "classification": classification,
        "fixed_subscription_sent_exactly_once": (
            len(fake.sent) == 1
            and json.loads(fake.sent[0]) == build_bybit_subscription(TOPICS)
        ),
        "fixed_fake_socket_entered_and_exited": fake.entered and fake.exited,
        "fake_wire_messages_by_class": wire,
        "fake_parsed_events_by_class": parsed,
        "fake_subscribe_ack_socket_outcome": observed[
            "subscribe_ack_messages_by_outcome"
        ],
        "fake_parser_errors": observed["parser_errors"],
        "fake_discarded_events_not_written": discarded_events,
        "fake_connection_incidents": sum(
            kind == "connected" for kind in incident_types
        ),
        "fake_error_incidents": sum(
            kind == "error" for kind in incident_types
        ),
        "per_topic_subscription_accepted_proven": False,
        "real_exchange_frames_received": False,
        "real_recorder_ingestion_proven": False,
        "event_enqueue_proven": False,
        "database_write_or_commit_proven": False,
        "inserted_rows_proven": False,
        "production_process_lifetime_bounded": False,
        "production_execution_authorized": False,
        "limitations": [
            "All frames and ACKs are synthetic; no real endpoint was contacted.",
            "ACK outcome is socket-level only, not per-topic acceptance.",
            "Parsed fixture events were discarded and never inserted.",
            "No queue, database, operating-system child or service was created.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    args = parser.parse_args()
    result = asyncio.run(run_fake_wire_only(args.scenario))
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
