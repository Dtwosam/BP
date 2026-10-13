"""Fully synthetic one-shot B1 readout; no sockets, DB or production access."""

from __future__ import annotations

import asyncio
import json

import pytest

from bp_engine.recorder.stage_b1_observer import StageB1BulkObserver
from bp_engine.recorder.stage_b1_one_shot import (
    MAX_REPORT_BYTES,
    run_offline_rehearsal,
    serialize_final_report,
)


class CooperativeService:
    def __init__(self) -> None:
        self.stopped = False

    async def run(self, stop: asyncio.Event) -> None:
        await stop.wait()
        self.stopped = True


class EarlyExitService:
    async def run(self, stop: asyncio.Event) -> None:
        del stop


class FailingService:
    async def run(self, stop: asyncio.Event) -> None:
        del stop
        raise RuntimeError("private-credential-text-must-not-escape")


class CancelledService:
    def __init__(self) -> None:
        self.cancelled = False

    async def run(self, stop: asyncio.Event) -> None:
        del stop
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class IgnoresCancelService:
    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def run(self, stop: asyncio.Event) -> None:
        del stop
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await self.release.wait()


@pytest.mark.asyncio
async def test_b1_cooperative_window_reports_aggregate_collection_only() -> None:
    service = CooperativeService()
    observer = StageB1BulkObserver(duration_seconds=1)
    raw = await asyncio.wait_for(
        run_offline_rehearsal(service=service, observer=observer),
        timeout=3,
    )
    report = json.loads(raw)
    assert service.stopped
    assert report["stop_reason"] == "window_elapsed"
    assert report["service_state"] == "clean"
    assert report["status"] == "COMPLETE_AGGREGATES_ONLY"
    assert not report["inserted_rows_by_class_proven"]
    assert not report["process_terminated_proven"]
    assert not report["production_execution_authorized"]
    assert len(raw.encode("utf-8")) <= MAX_REPORT_BYTES
    assert "payload" not in raw


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service", "reason", "state"),
    [
        (EarlyExitService(), "service_exited", "early_exit"),
        (FailingService(), "service_raised", "failed"),
    ],
)
async def test_b1_early_exit_or_error_is_inconclusive_without_details(
    service, reason: str, state: str
) -> None:
    report = json.loads(
        await run_offline_rehearsal(
            service=service, observer=StageB1BulkObserver(duration_seconds=1)
        )
    )
    assert report["status"] == "INCONCLUSIVE"
    assert report["stop_reason"] == reason
    assert report["service_state"] == state
    assert "private-credential" not in json.dumps(report)


@pytest.mark.asyncio
async def test_b1_timeout_cancels_only_task_and_never_claims_process_stop() -> None:
    service = CancelledService()
    report = json.loads(
        await run_offline_rehearsal(
            service=service,
            observer=StageB1BulkObserver(duration_seconds=1),
            shutdown_grace_seconds=0.01,
        )
    )
    assert service.cancelled
    assert report["status"] == "INCONCLUSIVE"
    assert report["service_state"] == "cancelled_task"
    assert not report["process_terminated_proven"]


@pytest.mark.asyncio
async def test_b1_uncooperative_service_remains_unresolved() -> None:
    service = IgnoresCancelService()
    report = json.loads(
        await run_offline_rehearsal(
            service=service,
            observer=StageB1BulkObserver(duration_seconds=1),
            shutdown_grace_seconds=0.01,
            cancel_grace_seconds=0.01,
        )
    )
    assert report["service_state"] == "unresolved_task"
    assert report["status"] == "INCONCLUSIVE"
    service.release.set()
    await asyncio.sleep(0)


def test_b1_readout_strips_unknown_fields_even_if_snapshot_is_contaminated() -> None:
    class ContaminatedObserver(StageB1BulkObserver):
        def snapshot(self) -> dict[str, object]:
            values = super().snapshot()
            values["raw_packet"] = "secret-marker-shall-not-escape"
            values["message"] = {"private_key": "also-secret"}
            return values

    observer = ContaminatedObserver(duration_seconds=1)
    report = serialize_final_report(
        observer, stop_reason="service_exited", service_state="early_exit"
    )
    assert "secret" not in report
    assert "raw_packet" not in report
    assert json.loads(report)["counts"]["wire_messages_by_topic_class"] == {
        "trade": 0, "orderbook": 0, "liquidation": 0, "other": 0,
    }


def test_b1_bad_counter_or_invalid_status_fails_closed() -> None:
    class BrokenObserver(StageB1BulkObserver):
        def snapshot(self) -> dict[str, object]:
            values = super().snapshot()
            values["parser_errors"] = "private-event-payload"
            return values

    with pytest.raises(ValueError, match="nonnegative integers"):
        serialize_final_report(
            BrokenObserver(duration_seconds=1),
            stop_reason="service_exited",
            service_state="early_exit",
        )
    with pytest.raises(ValueError, match="stop reason"):
        serialize_final_report(
            StageB1BulkObserver(duration_seconds=1),
            stop_reason="raw-user-string",
            service_state="early_exit",
        )


@pytest.mark.asyncio
async def test_b1_expired_window_or_unbounded_grace_rejected_without_launch() -> None:
    observer = StageB1BulkObserver(
        duration_seconds=1, monotonic=lambda: 500.0
    )
    service = CooperativeService()
    with pytest.raises(ValueError, match="grace"):
        await run_offline_rehearsal(
            service=service, observer=observer, shutdown_grace_seconds=11
        )
    # An injected synthetic clock that has already expired blocks service start.
    now = [100.0]
    expired = StageB1BulkObserver(duration_seconds=1, monotonic=lambda: now[0])
    now[0] = 102.0
    with pytest.raises(ValueError, match="already expired"):
        await run_offline_rehearsal(service=service, observer=expired)
    assert not service.stopped
