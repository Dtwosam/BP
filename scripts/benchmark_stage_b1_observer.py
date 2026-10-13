"""Synthetic, offline B1 instrumentation microbenchmark. No sockets, SQL or orders.

This measures wrapper overhead with a real Bybit parser and no-op queue/write
sinks. It is NOT a production CPU/DB/queue-latency acceptance measurement.
Run with: python scripts/benchmark_stage_b1_observer.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from datetime import UTC, datetime
from typing import Any

from bp_engine.collectors.bybit_ws import parse_bybit_message
from bp_engine.recorder.models import RawEvent
from bp_engine.recorder.stage_b1_observer import StageB1BulkObserver


def _payload(when: datetime) -> dict[str, object]:
    timestamp_ms = int(when.timestamp() * 1000)
    return {
        "topic": "publicTrade.BTCUSDT",
        "ts": timestamp_ms,
        "data": [{"s": "BTCUSDT", "p": "60001", "T": timestamp_ms}],
    }


def _parse(message: object, received_at: datetime) -> list[RawEvent]:
    return parse_bybit_message(message, venue="linear", received_at=received_at)


async def _enqueue(_: RawEvent) -> None:
    return None


async def _write(_: list[RawEvent]) -> None:
    return None


async def _time_cycles(
    parser: Any, enqueue: Any, write: Any,
    payload: dict[str, object], when: datetime, iterations: int,
) -> float:
    start_ns = time.perf_counter_ns()
    for _ in range(iterations):
        events = parser(payload, when)
        for event in events:
            await enqueue(event)
        await write(events)
    return (time.perf_counter_ns() - start_ns) / iterations


async def measure(*, iterations: int = 2000, repeats: int = 5) -> dict[str, object]:
    if not 100 <= iterations <= 20000 or not 3 <= repeats <= 9:
        raise ValueError("iterations 100..20000 and repeats 3..9 required")
    when = datetime(2026, 10, 13, 11, tzinfo=UTC)
    payload = _payload(when)
    observer = StageB1BulkObserver(duration_seconds=120)
    parser = observer.wrap_parser(_parse)
    enqueue = observer.wrap_event_sink(_enqueue)
    writer = observer.wrap_writer_sink(_write)

    # Warm both paths to avoid measuring cold imports/model initialization.
    await _time_cycles(_parse, _enqueue, _write, payload, when, 100)
    await _time_cycles(parser, enqueue, writer, payload, when, 100)

    baseline: list[float] = []
    observed: list[float] = []
    for repeat in range(repeats):
        modes = (
            ("baseline", "observed")
            if repeat % 2 == 0
            else ("observed", "baseline")
        )
        for mode in modes:
            ns = await _time_cycles(
                parser if mode == "observed" else _parse,
                enqueue if mode == "observed" else _enqueue,
                writer if mode == "observed" else _write,
                payload, when, iterations,
            )
            (observed if mode == "observed" else baseline).append(ns)

    snapshot = observer.snapshot()
    if not snapshot["collecting"]:
        raise RuntimeError("B1 120s counter window expired; measurements inconclusive")
    expected = 100 + iterations * repeats
    if snapshot["parsed_events_by_class"]["trade"] != expected:
        raise RuntimeError("B1 parsed counter did not match benchmark iterations")

    base_ns = statistics.median(baseline)
    observed_ns = statistics.median(observed)
    return {
        "report": "stage_b1_synthetic_injection_overhead_v1",
        "scope": "synthetic_offline_parser_and_noop_queue_writer_only",
        "iterations_per_repeat": iterations,
        "repeats": repeats,
        "baseline_median_ns_per_cycle": round(base_ns),
        "observer_median_ns_per_cycle": round(observed_ns),
        "difference_ns_per_cycle": round(observed_ns - base_ns),
        "relative_difference_percent": round((observed_ns / base_ns - 1) * 100, 2),
        "limitations": [
            "No production VM, DB transaction, socket, backpressure or disk I/O.",
            "This is a microbenchmark, not an approved latency/CPU acceptance gate.",
            "Actual per-class inserted rows are not measured.",
            "Performance review must verify representative live recorder "
            "baseline separately.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(measure(iterations=args.iterations, repeats=args.repeats)),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
