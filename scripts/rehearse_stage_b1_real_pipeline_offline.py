"""Run the REAL recorder supervisor/runner/writer on FAKE local Bybit frames.

Never uses the recorder service factory, Settings, network connector,
PostgreSQL, systemd, or a production output. This is a synthetic
integration rehearsal, NOT a production one-shot diagnostic.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from bp_engine.collectors.bybit_ws import parse_bybit_message
from bp_engine.collectors.websocket_runner import WebSocketCollectorRunner
from bp_engine.recorder.models import RawEvent
from bp_engine.recorder.service import RecorderService
from bp_engine.recorder.stage_b1_observer import StageB1BulkObserver
from bp_engine.recorder.stage_b1_one_shot import run_offline_rehearsal
from bp_engine.recorder.writer import BatchWriter, EventBuffer

SCENARIOS = ("healthy", "writer_failure", "writer_cancelled")
_FIXED_TIME = datetime(2026, 10, 13, 11, tzinfo=UTC)
_FRAME_DATA = (
    {"op": "subscribe", "success": True, "ret_msg": "offline_fixture"},
    {
        "topic": "publicTrade.BTCUSDT",
        "ts": 1791889200000,
        "data": [{"s": "BTCUSDT", "p": "1", "T": 1791889200000}],
    },
    {
        "topic": "orderbook.50.BTCUSDT",
        "type": "delta",
        "cts": 1791889200000,
        "data": {"s": "BTCUSDT", "b": [], "a": []},
    },
    {
        "topic": "allLiquidation.BTCUSDT",
        "ts": 1791889200000,
        "data": [{"s": "BTCUSDT", "v": "1"}],
    },
)


class _FakeLocalSocket:
    def __init__(self) -> None:
        self._messages: asyncio.Queue[str] = asyncio.Queue()
        for frame in _FRAME_DATA:
            self._messages.put_nowait(json.dumps(frame))

    async def send(self, _message: str) -> None:
        return None

    async def recv(self) -> str:
        return await self._messages.get()


class _FakeLocalConnection:
    def __init__(self, socket: _FakeLocalSocket) -> None:
        self._socket = socket

    async def __aenter__(self) -> _FakeLocalSocket:
        return self._socket

    async def __aexit__(self, *args: object) -> None:
        return None


def _parse(message: object, received_at: datetime) -> list[RawEvent]:
    if not isinstance(message, dict):
        raise TypeError("fixture must be a decoded dictionary")
    return parse_bybit_message(message, venue="linear", received_at=received_at)


def _build_fake_service(
    observer: StageB1BulkObserver, scenario: str
) -> RecorderService:
    if scenario not in SCENARIOS:
        raise ValueError("B1 scenario must be explicitly allowlisted")
    buffer = EventBuffer(maxsize=16)

    async def fake_returned_transaction(events: list[RawEvent]) -> None:
        # No DB query or transaction. Only return/raise/cancel semantics
        # are exercised; "successful transaction attempts" are SIMULATED.
        del events
        if scenario == "writer_failure":
            raise RuntimeError("FAKE_SECRET_must_not_enter_report")
        if scenario == "writer_cancelled":
            await asyncio.Event().wait()

    writer = BatchWriter(
        buffer=buffer,
        sink=observer.wrap_writer_sink(fake_returned_transaction),
        batch_size=1,
        flush_interval_seconds=0.01,
        worker_count=1,
    )

    async def fake_enqueue(event: RawEvent) -> None:
        await buffer.put(event)

    socket = _FakeLocalSocket()
    socket_runner = WebSocketCollectorRunner(
        source="bybit",
        stream="linear",
        url="wss://nonexistent.invalid/FIXTURE_NOT_CONNECTED",
        connector=lambda _url: _FakeLocalConnection(socket),
        subscription={
            "op": "subscribe",
            "args": [
                "publicTrade.BTCUSDT",
                "orderbook.50.BTCUSDT",
                "allLiquidation.BTCUSDT",
            ],
        },
        parser=observer.wrap_parser(_parse),
        event_sink=observer.wrap_event_sink(fake_enqueue),
        incident_sink=lambda _incident: None,
        heartbeat_message=None,
        heartbeat_interval_seconds=None,
        component_name="bybit_linear",
        now=lambda: _FIXED_TIME,
    )
    return RecorderService({"bybit_linear": socket_runner, "writer_bulk": writer})


async def rehearse_fake_pipeline(
    scenario: str = "healthy", *, duration_seconds: int = 1
) -> dict[str, Any]:
    """One real supervisor/writer and a fake in-memory Bybit bulk socket."""
    if scenario not in SCENARIOS:
        raise ValueError("unlisted synthetic B1 scenario")
    if type(duration_seconds) is not int or not 1 <= duration_seconds <= 3:
        raise ValueError("B1 synthetic duration must be 1-3 seconds")
    observer = StageB1BulkObserver(duration_seconds=duration_seconds)
    service = _build_fake_service(observer, scenario)
    report = json.loads(
        await run_offline_rehearsal(
            service=service,
            observer=observer,
            shutdown_grace_seconds=0.25,
            cancel_grace_seconds=0.15,
        )
    )
    report["scope"] = "offline_in_memory_socket_real_recorder_service_and_batch_writer"
    report["synthetic_scenario"] = scenario
    report["fake_sink_only_no_database_transaction"] = True
    report["process_tree_termination_proven"] = False
    report["recorder_operationally_approved"] = False
    # Run on fixed private fixture only; no attacker-controlled fields.
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    parser.add_argument("--duration-seconds", type=int, default=1)
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(
                rehearse_fake_pipeline(
                    args.scenario, duration_seconds=args.duration_seconds
                )
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
