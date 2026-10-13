"""Dormant Stage B1 Bybit-linear bulk pipeline counters; NOT deployed or enabled.

The observer has fixed low-cardinality metric keys, retains no raw messages or
events, stops accepting new observations after 120 seconds at most, and does
not log or open files/sockets. Wrappers must be wired to the *same existing*
bulk WebSocket parser, routed event sink and DB-commit-returning writer in a
separately reviewed, expressly authorized deployment. Existing production
recorder assembly does NOT instantiate or call this class.

A successful writer return counts attempts in a successfully completed
transaction, NOT individually inserted rows (dedupe may skip attempts).
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Awaitable, Callable, Mapping
from threading import Lock
from typing import Any

from bp_engine.recorder.models import RawEvent

MAX_DURATION_SECONDS = 120
_CATEGORIES = ("trade", "orderbook", "liquidation", "other")
_WIRE_TOPICS = {
    "publicTrade.BTCUSDT": "trade",
    "orderbook.50.BTCUSDT": "orderbook",
    "allLiquidation.BTCUSDT": "liquidation",
}


def _raw_category(event: RawEvent) -> str | None:
    if (
        event.source != "bybit"
        or event.stream != "linear"
        or event.instrument != "BTCUSDT"
    ):
        return None
    if event.event_type == "trade":
        return "trade"
    if event.event_type in {"orderbook_snapshot", "orderbook_delta"}:
        return "orderbook"
    if event.event_type == "liquidation":
        return "liquidation"
    # The same writer may also handle the dedicated linear TICKER socket.
    # Its rows are not bulk-socket evidence and must not enter B1 counters.
    if event.event_type == "ticker":
        return None
    return "other"


def _empty_categories() -> dict[str, int]:
    return dict.fromkeys(_CATEGORIES, 0)


class StageB1BulkObserver:
    """Aggregate-only, bounded counter collection for one linear bulk socket.

    No process scheduling, logging, database access, or activation is provided.
    A caller must explicitly and separately authorize a future deployment.
    """

    def __init__(
        self, *, duration_seconds: int, monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        if (
            not isinstance(duration_seconds, int)
            or isinstance(duration_seconds, bool)
            or not 1 <= duration_seconds <= MAX_DURATION_SECONDS
        ):
            raise ValueError("B1 duration must be an integer from 1 to 120 seconds")
        self._clock = monotonic
        self._start = monotonic()
        self._duration = duration_seconds
        self._lock = Lock()
        self._wire = _empty_categories()
        self._parsed = _empty_categories()
        self._parsed_null_source_time = _empty_categories()
        self._enqueued = _empty_categories()
        self._writer_attempts = _empty_categories()
        self._writer_successful_transaction_attempts = _empty_categories()
        self._ack = {"success": 0, "rejected": 0, "unclassified": 0}
        self._counters = {
            "wire_control_messages": 0,
            "wire_other_messages": 0,
            "parser_errors": 0,
            "enqueue_errors": 0,
            "writer_batches_started": 0,
            "writer_batches_returned": 0,
            "writer_batches_failed": 0,
            "writer_batches_in_flight": 0,
        }

    def _active(self) -> bool:
        return self._clock() - self._start < self._duration

    def _increment(self, group: dict[str, int], key: str, n: int = 1) -> None:
        with self._lock:
            group[key] += n

    def wrap_parser(
        self, parser: Callable[[object, Any], list[RawEvent]]
    ) -> Callable[[object, Any], list[RawEvent]]:
        """Observe decoded wire messages and parsed events on THIS bulk socket."""

        def observed(message: object, received_at: Any) -> list[RawEvent]:
            counted = self._active()
            if counted:
                if isinstance(message, Mapping):
                    topic = message.get("topic")
                    event_class = (
                        _WIRE_TOPICS.get(topic) if isinstance(topic, str) else None
                    )
                    if event_class is not None:
                        self._increment(self._wire, event_class)
                    elif message.get("op") is not None:
                        self._increment(self._counters, "wire_control_messages")
                        if message.get("op") == "subscribe":
                            success = message.get("success")
                            category = (
                                "success" if success is True
                                else "rejected" if success is False
                                else "unclassified"
                            )
                            self._increment(self._ack, category)
                    else:
                        self._increment(self._counters, "wire_other_messages")
                else:
                    self._increment(self._counters, "wire_other_messages")

            try:
                events = parser(message, received_at)
            except Exception:
                if counted:
                    self._increment(self._counters, "parser_errors")
                raise
            if counted:
                for event in events:
                    event_class = _raw_category(event)
                    if event_class is not None:
                        self._increment(self._parsed, event_class)
                        if event.source_timestamp is None:
                            self._increment(self._parsed_null_source_time, event_class)
            return events

        return observed

    def wrap_event_sink(
        self, sink: Callable[[RawEvent], Awaitable[None] | None]
    ) -> Callable[[RawEvent], Awaitable[None]]:
        """Count only successful returns from the existing routed enqueue sink."""

        async def observed(event: RawEvent) -> None:
            counted = self._active()
            event_class = _raw_category(event) if counted else None
            try:
                result = sink(event)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                if event_class is not None:
                    self._increment(self._counters, "enqueue_errors")
                raise
            if event_class is not None:
                self._increment(self._enqueued, event_class)

        return observed

    def wrap_writer_sink(
        self, sink: Callable[[list[RawEvent]], Awaitable[None]]
    ) -> Callable[[list[RawEvent]], Awaitable[None]]:
        """Count attempted events after a transactional DB sink *returns*.

        Call only around a sink that returns after its actual transaction has
        committed successfully; the observer cannot verify this contract.
        No category-specific inserted row count is inferred from the return.
        """

        async def observed(events: list[RawEvent]) -> None:
            batch_counts = _empty_categories()
            counted = self._active()
            if counted:
                for event in events:
                    event_class = _raw_category(event)
                    if event_class is not None:
                        batch_counts[event_class] += 1
                counted = any(batch_counts.values())
            if counted:
                with self._lock:
                    self._counters["writer_batches_started"] += 1
                    self._counters["writer_batches_in_flight"] += 1
                    for category, count in batch_counts.items():
                        self._writer_attempts[category] += count
            try:
                await sink(events)
            except BaseException:
                if counted:
                    with self._lock:
                        self._counters["writer_batches_failed"] += 1
                        self._counters["writer_batches_in_flight"] -= 1
                raise
            else:
                if counted:
                    with self._lock:
                        self._counters["writer_batches_returned"] += 1
                        self._counters["writer_batches_in_flight"] -= 1
                        for category, count in batch_counts.items():
                            self._writer_successful_transaction_attempts[category] += count

        return observed

    def snapshot(self) -> dict[str, object]:
        """Return strictly allowlisted aggregates, never messages or payloads."""
        elapsed = max(0.0, self._clock() - self._start)
        with self._lock:
            return {
                "report": "stage_b1_bulk_observer_offline_candidate_v1",
                "enabled_in_production": False,
                "duration_limit_seconds": self._duration,
                "elapsed_seconds": round(elapsed, 3),
                "collecting": elapsed < self._duration,
                "wire_messages_by_topic_class": dict(self._wire),
                "subscribe_ack_messages_by_outcome": dict(self._ack),
                "parsed_events_by_class": dict(self._parsed),
                "parsed_null_source_timestamp_by_class": dict(
                    self._parsed_null_source_time
                ),
                "successfully_enqueued_events_by_class": dict(self._enqueued),
                "writer_attempted_events_by_class": dict(self._writer_attempts),
                "events_in_successfully_returned_transactions_by_class": dict(
                    self._writer_successful_transaction_attempts
                ),
                **dict(self._counters),
                "limitations": [
                    "ACK counts are socket-level only, not per-topic acceptance proof.",
                    "Writer returned means the injected sink's contract was followed.",
                    "Events in a successful batch may have been deduplicated, not inserted.",
                    "In-flight writes must be reconciled before interpreting missing rows.",
                    "No database query, raw payload, frame storage or logging is performed.",
                    "This offline candidate is not wired into the deployed recorder.",
                ],
            }
