"""Non-gating B1 recorder-component overhead: FAKE socket and FAKE DB sink.

Real WebSocketCollectorRunner -> parser -> EventBuffer -> BatchWriter ->
RecorderService; only immutable local JSON frames and asyncio.sleep fake
writes. No Settings, socket connection, PostgreSQL, host, or order path.
The benchmark does NOT approximate production workload, CPU/RSS or p95 SLA.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import tracemalloc
from collections import deque
from datetime import UTC, datetime
from typing import Any

from bp_engine.collectors.bybit_ws import parse_bybit_message
from bp_engine.collectors.websocket_runner import WebSocketCollectorRunner
from bp_engine.recorder.models import RawEvent
from bp_engine.recorder.service import RecorderService
from bp_engine.recorder.stage_b1_observer import StageB1BulkObserver
from bp_engine.recorder.writer import BatchWriter, EventBuffer

_BENCHMARK_EVENTS = 128
_MAX_REPEATS = 5
_WORKERS = (1, 3)
_QUEUE_CAPACITY = 32
_BATCH_SIZE = 16
_FAKE_WRITE_DELAY = 0.002
_TIME = datetime(2026, 10, 13, 11, tzinfo=UTC)
_TIMESTAMP_MS = 1791889200000


def _frames() -> list[str]:
    frames: list[str] = [json.dumps({"op": "subscribe", "success": True})]
    for index in range(_BENCHMARK_EVENTS):
        timestamp = _TIMESTAMP_MS + index
        mod = index % 3
        if mod == 0:
            frame: dict[str, object] = {
                "topic": "publicTrade.BTCUSDT", "ts": timestamp,
                "data": [{"s": "BTCUSDT", "p": "1", "T": timestamp}],
            }
        elif mod == 1:
            frame = {
                "topic": "orderbook.50.BTCUSDT", "type": "delta",
                "cts": timestamp,
                "data": {"s": "BTCUSDT", "b": [], "a": []},
            }
        else:
            frame = {
                "topic": "allLiquidation.BTCUSDT", "ts": timestamp,
                "data": [{"s": "BTCUSDT", "v": "1"}],
            }
        frames.append(json.dumps(frame, separators=(",", ":")))
    return frames


class _LocalSocket:
    def __init__(self, frames: list[str]) -> None:
        self._frames: deque[str] = deque(frames)

    async def send(self, _message: str) -> None:
        return None

    async def recv(self) -> str:
        if self._frames:
            return self._frames.popleft()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class _Connection:
    def __init__(self, socket: _LocalSocket) -> None:
        self._socket = socket

    async def __aenter__(self) -> _LocalSocket:
        return self._socket

    async def __aexit__(self, *_args: object) -> None:
        return None


def _parse(message: object, received_at: datetime) -> list[RawEvent]:
    if not isinstance(message, dict):
        raise ValueError("expected offline fixed dictionary")
    return parse_bybit_message(message, venue="linear", received_at=received_at)


def _percentile_ns(numbers: list[int], percent: float) -> int:
    if not numbers:
        raise RuntimeError("missing local recorder timing samples")
    ordered = sorted(numbers)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * percent))]


async def _trial(*, observed: bool, workers: int) -> dict[str, int | float]:
    """A bounded full recorder lifecycle, with real queue and writer workers."""
    if type(observed) is not bool or workers not in _WORKERS:
        raise ValueError("unknown synthetic B1 recorder benchmark arm")
    buffer = EventBuffer(_QUEUE_CAPACITY)
    socket = _LocalSocket(_frames())
    observer = StageB1BulkObserver(duration_seconds=120) if observed else None
    stop = asyncio.Event()
    all_enqueued = asyncio.Event()
    timings: dict[int, int] = {}
    blocked_ns: list[int] = []
    write_ms_ns: list[int] = []
    enqueue_to_commit_ns: list[int] = []
    accepted = 0
    committed = 0
    peak_depth = 0
    writer_batches = 0

    async def enqueue(event: RawEvent) -> None:
        nonlocal accepted, peak_depth
        started = time.perf_counter_ns()
        await buffer.put(event)
        accepted += 1
        blocked_ns.append(time.perf_counter_ns() - started)
        timings[id(event)] = time.perf_counter_ns()
        peak_depth = max(peak_depth, buffer.qsize())
        if accepted == _BENCHMARK_EVENTS:
            all_enqueued.set()

    async def fake_transaction(events: list[RawEvent]) -> None:
        nonlocal committed, writer_batches
        started = time.perf_counter_ns()
        await asyncio.sleep(_FAKE_WRITE_DELAY)
        finished = time.perf_counter_ns()
        write_ms_ns.append(finished - started)
        for event in events:
            enqueue_to_commit_ns.append(finished - timings.pop(id(event)))
        committed += len(events)
        writer_batches += 1

    parser = observer.wrap_parser(_parse) if observer is not None else _parse
    sink = observer.wrap_event_sink(enqueue) if observer is not None else enqueue
    writer_sink = (
        observer.wrap_writer_sink(fake_transaction)
        if observer is not None else fake_transaction
    )
    runner = WebSocketCollectorRunner(
        source="bybit",
        stream="linear",
        url="wss://invalid.example/OFFLINE_NO_NETWORK",
        connector=lambda _url: _Connection(socket),
        subscription={"op": "subscribe", "args": ["publicTrade.BTCUSDT"]},
        parser=parser,
        event_sink=sink,
        incident_sink=lambda _incident: None,
        heartbeat_message=None,
        heartbeat_interval_seconds=None,
        component_name="bybit_linear",
        now=lambda: _TIME,
    )
    writer = BatchWriter(
        buffer=buffer,
        sink=writer_sink,
        batch_size=_BATCH_SIZE,
        flush_interval_seconds=0.01,
        worker_count=workers,
    )
    service = RecorderService({"bybit_linear": runner, "writer_bulk": writer})

    task = asyncio.create_task(service.run(stop))
    started_wall = time.perf_counter_ns()
    started_cpu = time.process_time_ns()
    tracemalloc.start()
    try:
        await asyncio.wait_for(all_enqueued.wait(), timeout=4)
        produced_at = time.perf_counter_ns()
        stop.set()
        await asyncio.wait_for(task, timeout=4)
        finished = time.perf_counter_ns()
        cpu = time.process_time_ns() - started_cpu
        _current, allocation_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        stop.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    if (
        accepted != _BENCHMARK_EVENTS
        or committed != accepted
        or timings
        or not buffer.empty()
        or not 0 < peak_depth <= _QUEUE_CAPACITY
    ):
        raise RuntimeError("synthetic recorder did not completely drain")
    if observer is not None:
        snapshot = observer.snapshot()
        if (
            not snapshot["collecting"]
            or sum(snapshot["successfully_enqueued_events_by_class"].values())
            != accepted
            or sum(snapshot["writer_attempted_events_by_class"].values())
            != committed
            or snapshot["writer_batches_in_flight"] != 0
            or snapshot["writer_batches_returned"] != writer_batches
        ):
            raise RuntimeError("observer missed a synthetic event or writer batch")

    return {
        "frames": _BENCHMARK_EVENTS,
        "accepted_events": accepted,
        "fake_writer_returned_events": committed,
        "fake_writer_batches": writer_batches,
        "queue_peak_events": peak_depth,
        "producer_enqueue_block_p95_ns": _percentile_ns(blocked_ns, 0.95),
        "fake_writer_batch_p95_ns": _percentile_ns(write_ms_ns, 0.95),
        "enqueue_to_fake_writer_return_p95_ns": _percentile_ns(
            enqueue_to_commit_ns, 0.95
        ),
        "wall_ns": finished - started_wall,
        "drain_ns": finished - produced_at,
        "cpu_ns": cpu,
        "python_tracemalloc_peak_bytes": allocation_peak,
    }


def _medians(samples: list[dict[str, int | float]]) -> dict[str, int]:
    keys = (
        "queue_peak_events", "producer_enqueue_block_p95_ns",
        "fake_writer_batch_p95_ns", "enqueue_to_fake_writer_return_p95_ns",
        "wall_ns", "drain_ns", "cpu_ns", "python_tracemalloc_peak_bytes",
    )
    return {
        key: int(statistics.median(sample[key] for sample in samples))
        for key in keys
    }


async def measure(*, repeats: int = 3) -> dict[str, Any]:
    if type(repeats) is not int or not 2 <= repeats <= _MAX_REPEATS:
        raise ValueError("offline recorder repeats must be 2-5")
    rows = []
    for workers in _WORKERS:
        samples: dict[str, list[dict[str, int | float]]] = {
            "baseline": [], "instrumented": [],
        }
        for repeat in range(repeats):
            arms = (
                ("baseline", "instrumented")
                if repeat % 2 == 0 else ("instrumented", "baseline")
            )
            for arm in arms:
                samples[arm].append(
                    await _trial(observed=arm == "instrumented", workers=workers)
                )
        baseline = _medians(samples["baseline"])
        instrumented = _medians(samples["instrumented"])
        rows.append({
            "worker_count": workers,
            "repeats_per_arm": repeats,
            "baseline_medians": baseline,
            "instrumented_medians": instrumented,
            "difference_medians": {
                key: instrumented[key] - baseline[key] for key in baseline
            },
            "relative_wall_delta_percent": round(
                100 * (instrumented["wall_ns"] - baseline["wall_ns"])
                / max(1, baseline["wall_ns"]), 2
            ),
        })
    return {
        "report": "stage_b1_offline_supervised_recorder_queue_v1",
        "status": "LOCAL_SYNTHETIC_NON_GATING_NO_GO",
        "scope": "real_recorder_runner_queue_writer_fake_socket_and_sink_only",
        "frames_per_trial": _BENCHMARK_EVENTS,
        "queue_capacity": _QUEUE_CAPACITY,
        "batch_size": _BATCH_SIZE,
        "fake_writer_sleep_ms": 2,
        "measurements": rows,
        "production_host_access": False,
        "production_execution_authorized": False,
        "production_overhead_accepted": False,
        "inserted_rows_proven": False,
        "limitations": [
            "Only fixed in-memory Bybit-like frames; no actual WebSocket or SQL.",
            "Artificial asyncio sleep is not a PostgreSQL transaction.",
            "Queue p95 is synthetic, not production recorder p95.",
            "Process CPU is shared per trial; tracemalloc is not RSS.",
            "No subprocess watchdog, thread cancellation or service rollback.",
            "Two to five alternated samples per arm are not representative.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(measure(repeats=args.repeats)), sort_keys=True))


if __name__ == "__main__":
    main()
