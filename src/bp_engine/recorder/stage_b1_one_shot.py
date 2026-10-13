"""Dormant Stage B1 one-shot *library* for offline lifecycle rehearsal.

There is deliberately NO CLI, environment switch, network connection,
file write, print/log, systemd action, or production caller. An explicitly
injected service controls its own I/O; unit tests use fake services only.
A timed counter window is NOT a hard process lifetime guarantee.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Protocol

from bp_engine.recorder.stage_b1_observer import MAX_DURATION_SECONDS, StageB1BulkObserver

_CLASSES = ("trade", "orderbook", "liquidation", "other")
_ACKS = ("success", "rejected", "unclassified")
_GROUPS = (
    "wire_messages_by_topic_class",
    "subscribe_ack_messages_by_outcome",
    "parsed_events_by_class",
    "parsed_null_source_timestamp_by_class",
    "successfully_enqueued_events_by_class",
    "writer_attempted_events_by_class",
    "events_in_successfully_returned_transactions_by_class",
)
_SCALARS = (
    "wire_control_messages",
    "wire_other_messages",
    "parser_errors",
    "enqueue_errors",
    "writer_batches_started",
    "writer_batches_returned",
    "writer_batches_failed",
    "writer_batches_cancelled_unresolved",
    "writer_batches_in_flight",
)
MAX_REPORT_BYTES = 4096


class BoundedService(Protocol):
    async def run(self, stop: asyncio.Event) -> None: ...


def _nonnegative_int(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("B1 counters must be nonnegative integers")
    return value


def _safe_counts(snapshot: Mapping[str, object]) -> dict[str, object]:
    """Copy ONLY exact fixed counter keys; silently exclude other snapshot data."""
    counts: dict[str, object] = {}
    for group in _GROUPS:
        raw = snapshot.get(group)
        keys = _ACKS if group == "subscribe_ack_messages_by_outcome" else _CLASSES
        if not isinstance(raw, Mapping) or set(raw) != set(keys):
            raise ValueError("B1 snapshot metric group mismatch")
        counts[group] = {key: _nonnegative_int(raw[key]) for key in keys}
    for key in _SCALARS:
        counts[key] = _nonnegative_int(snapshot.get(key))
    return counts


def serialize_final_report(
    observer: StageB1BulkObserver, *, stop_reason: str, service_state: str
) -> str:
    """Fixed-schema JSON only. Never accept user-provided payload/error strings."""
    if stop_reason not in {"window_elapsed", "service_exited", "service_raised"}:
        raise ValueError("invalid B1 stop reason")
    if service_state not in {
        "clean", "early_exit", "failed", "cancelled_task", "unresolved_task"
    }:
        raise ValueError("invalid B1 service state")
    snapshot = observer.snapshot()
    duration = snapshot.get("duration_limit_seconds")
    if type(duration) is not int or not 1 <= duration <= MAX_DURATION_SECONDS:
        raise ValueError("invalid B1 duration")
    in_flight = _nonnegative_int(snapshot.get("writer_batches_in_flight"))
    unresolved = _nonnegative_int(
        snapshot.get("writer_batches_cancelled_unresolved")
    )
    complete = (
        stop_reason == "window_elapsed"
        and service_state == "clean"
        and in_flight == 0
        and unresolved == 0
        and snapshot.get("collecting") is False
    )
    report = {
        "report": "phase14_stage_b1_one_shot_library_candidate_v1",
        "status": "COMPLETE_AGGREGATES_ONLY" if complete else "INCONCLUSIVE",
        "stop_reason": stop_reason,
        "service_state": service_state,
        "duration_limit_seconds": duration,
        "collecting_at_readout": snapshot.get("collecting") is True,
        "counts": _safe_counts(snapshot),
        "inserted_rows_by_class_proven": False,
        "subscribe_ack_per_topic_proven": False,
        "process_terminated_proven": False,
        "production_execution_authorized": False,
        "interpretation": [
            "Completed transaction attempts are not inserted rows.",
            "A cancelled asyncio task does not prove that DB threads stopped.",
            "A stopped counter window does not prove full process termination.",
            "The normal production recorder entry point does not call this library.",
        ],
    }
    payload = json.dumps(report, sort_keys=True, separators=(",", ":"))
    if len(payload.encode("utf-8")) > MAX_REPORT_BYTES:
        raise ValueError("B1 report exceeds fixed maximum size")
    return payload


async def run_offline_rehearsal(
    *,
    service: BoundedService,
    observer: StageB1BulkObserver,
    shutdown_grace_seconds: float = 2.0,
    cancel_grace_seconds: float = 0.25,
) -> str:
    """Exercise a pre-built injected service with bounded *task* waits.

    Not a real-process kill switch. Even if cancellation finishes, threaded
    writers may continue. No production CLI or service factory invokes this.
    """
    if not 0 < shutdown_grace_seconds <= 10:
        raise ValueError("shutdown grace must be in (0, 10] seconds")
    if not 0 < cancel_grace_seconds <= 2:
        raise ValueError("cancellation grace must be in (0, 2] seconds")
    left = observer.remaining_seconds()
    if left <= 0:
        raise ValueError("B1 collection window already expired")
    stop = asyncio.Event()
    task = asyncio.create_task(service.run(stop), name="stage-b1-offline-service")
    try:
        finished, _ = await asyncio.wait({task}, timeout=left)
        if finished:
            if task.cancelled() or task.exception() is not None:
                return serialize_final_report(
                    observer, stop_reason="service_raised", service_state="failed"
                )
            return serialize_final_report(
                observer, stop_reason="service_exited", service_state="early_exit"
            )

        stop.set()
        finished, _ = await asyncio.wait({task}, timeout=shutdown_grace_seconds)
        if finished:
            state = (
                "failed"
                if task.cancelled() or task.exception() is not None
                else "clean"
            )
            return serialize_final_report(
                observer, stop_reason="window_elapsed", service_state=state
            )

        task.cancel()
        finished, _ = await asyncio.wait({task}, timeout=cancel_grace_seconds)
        state = "cancelled_task" if finished else "unresolved_task"
        return serialize_final_report(
            observer, stop_reason="window_elapsed", service_state=state
        )
    except asyncio.CancelledError:
        stop.set()
        task.cancel()
        # Never claim the child task/process stopped; propagate caller cancel.
        raise
