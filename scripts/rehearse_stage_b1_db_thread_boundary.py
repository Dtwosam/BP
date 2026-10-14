"""Fake-only Stage B1 witness: cancellation cannot stop a synchronous DB worker.

Uses the actual _DatabaseSink.write_events -> asyncio.to_thread boundary and
StageB1BulkObserver writer wrapper, but ONLY an in-memory fake engine/repository.
No SQL, DB driver connection, network, recorder factory, operator command,
production service, credentials, or host operations. All waits are finite.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
import time
from datetime import UTC, datetime

from bp_engine.recorder.models import RawEvent
from bp_engine.recorder.service import _DatabaseSink
from bp_engine.recorder.stage_b1_observer import StageB1BulkObserver

SCENARIOS = ("late_success", "late_failure")
_WAIT_SECONDS = 3.0


class _FakeTransaction:
    def __init__(self, engine: _FakeEngine) -> None:
        self.engine = engine

    def __enter__(self) -> _FakeTransaction:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        del exc, tb
        self.engine.outcome = "fake_committed" if exc_type is None else "fake_rolled_back"
        self.engine.finished.set()
        return False


class _FakeEngine:
    def __init__(self) -> None:
        self.finished = threading.Event()
        self.outcome = "pending"

    def begin(self) -> _FakeTransaction:
        return _FakeTransaction(self)


class _FakeRepository:
    def __init__(
        self, *, scenario: str, started: threading.Event, release: threading.Event
    ) -> None:
        self.scenario = scenario
        self.started = started
        self.release = release

    def insert_events(self, connection: object, events: list[object]) -> int:
        del connection
        if len(events) != 1:
            raise RuntimeError("fixed synthetic batch mismatch")
        self.started.set()
        if not self.release.wait(_WAIT_SECONDS):
            raise RuntimeError("fixed synthetic worker release timeout")
        if self.scenario == "late_failure":
            raise RuntimeError("fixed synthetic transaction error")
        return 1


async def _until(flag: threading.Event, *, seconds: float = _WAIT_SECONDS) -> bool:
    """Poll a synthetic thread signal without spawning a second worker."""
    deadline = time.monotonic() + seconds
    while not flag.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(0.01, remaining))
    return True


def _fixed_event() -> RawEvent:
    when = datetime(2026, 10, 14, tzinfo=UTC)
    return RawEvent.build(
        source="bybit", stream="linear", instrument="BTCUSDT",
        event_type="trade", source_timestamp=when, received_at=when,
        payload={"synthetic": True},
    )


async def rehearse_fake_thread_boundary(scenario: str) -> dict[str, object]:
    """Demonstrate that cancelling the awaited task does not cancel its worker."""
    if scenario not in SCENARIOS:
        raise ValueError("only fixed fake transaction scenarios are accepted")

    started = threading.Event()
    release = threading.Event()
    engine = _FakeEngine()
    repository = _FakeRepository(scenario=scenario, started=started, release=release)
    observer = StageB1BulkObserver(duration_seconds=10)
    sink = _DatabaseSink(engine, repository)
    wrapped = observer.wrap_writer_sink(sink.write_events)
    task = asyncio.create_task(wrapped([_fixed_event()]))
    try:
        if not await _until(started):
            raise RuntimeError("fixed synthetic worker did not start")
        if engine.finished.is_set():
            raise RuntimeError("fixed synthetic worker completed before release")

        task.cancel()
        cancelled = False
        try:
            await asyncio.wait_for(task, timeout=1)
        except asyncio.CancelledError:
            cancelled = True
        if not cancelled:
            raise RuntimeError("fake awaited task unexpectedly survived cancellation")

        # The cancelled coroutine has exited, but the synchronous worker is
        # still blocked in a fake insert, so no rollback/commit is proven.
        worker_pending_after_task_cancel = not engine.finished.is_set()
        pending_counts = observer.snapshot()

        release.set()
        if not await _until(engine.finished):
            raise RuntimeError("fixed synthetic worker did not finish after release")

        final_counts = observer.snapshot()
        expected = "fake_committed" if scenario == "late_success" else "fake_rolled_back"
        if (
            not worker_pending_after_task_cancel
            or engine.outcome != expected
            or pending_counts["writer_batches_cancelled_unresolved"] != 1
            or pending_counts["writer_batches_in_flight"] != 1
            or final_counts["writer_batches_returned"] != 0
            or final_counts["writer_batches_in_flight"] != 1
            or final_counts["events_in_successfully_returned_transactions_by_class"][
                "trade"
            ] != 0
        ):
            raise RuntimeError("fixed synthetic cancellation accounting mismatch")

        # This is a known fixture thread, NOT an assertion about production
        # connection/transaction or thread lifetime after service cancellation.
        return {
            "report": "phase14_b1_fake_db_thread_cancellation_v1",
            "scope": "fake_in_memory_transaction_actual_asyncio_to_thread_boundary",
            "scenario": scenario,
            "awaiting_task_cancelled": cancelled,
            "fake_worker_pending_after_task_cancellation": worker_pending_after_task_cancel,
            "fake_worker_finished_after_release": engine.finished.is_set(),
            "fake_transaction_outcome_after_release": engine.outcome,
            "writer_batches_cancelled_unresolved": 1,
            "writer_batches_in_flight_conservative": 1,
            "writer_batches_returned": 0,
            "writer_attempts_marked_committed": 0,
            "actual_database_commit_or_rollback_proven": False,
            "production_db_threads_terminated_proven": False,
            "systemd_restore_proven": False,
            "operator_launcher_available": False,
            "production_execution_authorized": False,
        }
    finally:
        # Known fake releases itself after at most _WAIT_SECONDS. This does
        # not solve shutdown/reaping of actual production DB worker threads.
        release.set()
        if not task.done():
            task.cancel()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True, choices=SCENARIOS)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(rehearse_fake_thread_boundary(args.scenario)), sort_keys=True))


if __name__ == "__main__":
    main()
