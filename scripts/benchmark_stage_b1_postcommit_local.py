"""Non-gating local PostgreSQL B1 postcommit overhead comparison.

Called ONLY by the existing isolated CI PostgreSQL pytest fixture. No CLI,
host discovery, network probing, live settings, or production connection.
Runs a small bounded alternating baseline/opt-in batch write comparison.
Python allocation peaks are tracemalloc estimates, NOT process/DB RSS.
"""

from __future__ import annotations

import statistics
import time
import tracemalloc
from datetime import UTC, datetime

from sqlalchemy import Engine

from bp_engine.recorder.models import RawEvent
from bp_engine.recorder.stage_b1_insert_attribution import (
    _local_test_engine_only,
    insert_same_batch_attributed_offline,
)
from bp_engine.storage.recorder import RecorderRepository

_BATCH_SIZES = (8, 32, 64)
_REPEATS = 3
_RECEIVED_AT = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)


def _sample_events(n: int, prefix: str) -> list[RawEvent]:
    result: list[RawEvent] = []
    kinds = ("trade", "orderbook_delta", "liquidation", "ticker", "trade")
    for index in range(n):
        if index % 4 == 3:
            result.append(result[-1])
            continue
        kind = kinds[index % len(kinds)]
        source = "coinbase" if index % 5 == 4 else "bybit"
        result.append(
            RawEvent.build(
                source=source,
                stream="linear",
                instrument="BTCUSDT",
                event_type=kind,
                source_timestamp=_RECEIVED_AT,
                received_at=_RECEIVED_AT,
                sequence=f"{prefix}-{index}",
                payload={"kind": kind, "i": index},
            )
        )
    return result


def _median(values: list[int]) -> int:
    return int(statistics.median(values))


def compare_local_postcommit_overhead(engine: Engine) -> dict[str, object]:
    """18 fixed local transactions, alternating normal and postcommit paths."""
    _local_test_engine_only(engine)
    if engine.url.get_backend_name() != "postgresql":
        raise ValueError("B1 benchmark requires isolated local PostgreSQL")

    repo = RecorderRepository()
    results: list[dict[str, object]] = []
    tracemalloc.start()
    try:
        for batch_size in _BATCH_SIZES:
            measurements: dict[str, dict[str, list[int]]] = {
                "baseline": {"wall_ns": [], "cpu_ns": [], "python_peak_bytes": []},
                "postcommit": {"wall_ns": [], "cpu_ns": [], "python_peak_bytes": []},
            }
            for repetition in range(_REPEATS):
                order = (
                    ("baseline", "postcommit")
                    if repetition % 2 == 0
                    else ("postcommit", "baseline")
                )
                for mode in order:
                    rows = _sample_events(
                        batch_size, f"local-b1-{batch_size}-{repetition}-{mode}"
                    )
                    unique_rows = len({event.dedupe_key for event in rows})
                    callbacks: list[dict[str, int]] = []

                    def on_committed(counts: dict[str, int]) -> None:
                        callbacks.append(dict(counts))

                    tracemalloc.reset_peak()
                    started_cpu = time.process_time_ns()
                    started_wall = time.perf_counter_ns()
                    if mode == "baseline":
                        with engine.begin() as connection:
                            inserted = repo.insert_events(connection, rows)
                    else:
                        report = insert_same_batch_attributed_offline(
                            engine, rows, on_committed_counts=on_committed
                        )
                        if (
                            report["postcommit_notification_status"] != "DELIVERED"
                            or len(callbacks) != 1
                            or sum(callbacks[0].values()) != unique_rows
                        ):
                            raise AssertionError("B1 after-commit receipt mismatch")
                        inserted = sum(report["inserted_by_class"].values())
                    elapsed_wall = time.perf_counter_ns() - started_wall
                    elapsed_cpu = time.process_time_ns() - started_cpu
                    _current, peak = tracemalloc.get_traced_memory()
                    if inserted != unique_rows:
                        raise AssertionError("local batch insertion count differs")
                    sample = measurements[mode]
                    sample["wall_ns"].append(elapsed_wall)
                    sample["cpu_ns"].append(elapsed_cpu)
                    sample["python_peak_bytes"].append(peak)

            baseline = {
                metric: _median(measurements["baseline"][metric])
                for metric in ("wall_ns", "cpu_ns", "python_peak_bytes")
            }
            observed = {
                metric: _median(measurements["postcommit"][metric])
                for metric in ("wall_ns", "cpu_ns", "python_peak_bytes")
            }
            results.append({
                "batch_size": batch_size,
                "repetitions_per_arm": _REPEATS,
                "dedupe_pattern": "every_fourth_attempt_duplicates_prior",
                "baseline_medians": baseline,
                "postcommit_medians": observed,
                "delta_medians": {
                    key: observed[key] - baseline[key] for key in baseline
                },
                "relative_wall_delta_percent": round(
                    100 * (observed["wall_ns"] - baseline["wall_ns"])
                    / max(1, baseline["wall_ns"]), 2
                ),
            })
    finally:
        tracemalloc.stop()

    return {
        "report": "stage_b1_local_pg_postcommit_non_gating_benchmark_v1",
        "status": "LOCAL_ONLY_NOT_PRODUCTION_ACCEPTANCE",
        "metrics": results,
        "transaction_count": len(_BATCH_SIZES) * _REPEATS * 2,
        "database_scope": "isolated_ci_local_postgresql_fixture",
        "normal_recorder_changed": False,
        "production_execution_authorized": False,
        "production_overhead_accepted": False,
        "limitations": [
            "Only three alternated samples per arm; medians are noisy.",
            "tracemalloc peak is Python allocations, not process/DB RSS.",
            "CPU time includes Python process work; wall includes local database.",
            "No actual recorder queue, sockets, thread cancellation, or host load.",
            "No performance threshold is used to approve production activation.",
        ],
    }
