"""B1 local recorder queue/writer overhead harness has NO external I/O."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.benchmark_stage_b1_recorder_queue_offline import (
    _BENCHMARK_EVENTS,
    _QUEUE_CAPACITY,
    _trial,
    measure,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("workers", [1, 3])
@pytest.mark.parametrize("observed", [False, True])
async def test_real_recorder_fake_socket_drains_all_writer_batches(
    workers: int, observed: bool
) -> None:
    result = await asyncio.wait_for(
        _trial(observed=observed, workers=workers), timeout=10
    )
    assert result["accepted_events"] == _BENCHMARK_EVENTS
    assert result["fake_writer_returned_events"] == _BENCHMARK_EVENTS
    assert result["fake_writer_batches"] > 0
    assert 0 < result["queue_peak_events"] <= _QUEUE_CAPACITY
    for metric in (
        "producer_enqueue_block_p95_ns",
        "fake_writer_batch_p95_ns",
        "enqueue_to_fake_writer_return_p95_ns",
        "wall_ns",
        "drain_ns",
        "cpu_ns",
        "python_tracemalloc_peak_bytes",
    ):
        assert result[metric] > 0


@pytest.mark.asyncio
async def test_alternated_real_recorder_benchmark_is_non_gating() -> None:
    report = await asyncio.wait_for(measure(repeats=2), timeout=20)
    assert report["status"] == "LOCAL_SYNTHETIC_NON_GATING_NO_GO"
    assert report["frames_per_trial"] == _BENCHMARK_EVENTS
    assert report["queue_capacity"] == _QUEUE_CAPACITY
    assert [row["worker_count"] for row in report["measurements"]] == [1, 3]
    for row in report["measurements"]:
        assert row["repeats_per_arm"] == 2
        for mode in ("baseline_medians", "instrumented_medians"):
            assert row[mode]["wall_ns"] > 0
            assert row[mode]["queue_peak_events"] > 0
            assert row[mode]["enqueue_to_fake_writer_return_p95_ns"] > 0
        assert row["difference_medians"]["wall_ns"] == (
            row["instrumented_medians"]["wall_ns"]
            - row["baseline_medians"]["wall_ns"]
        )
    assert report["production_execution_authorized"] is False
    assert report["production_overhead_accepted"] is False
    assert report["inserted_rows_proven"] is False
    serialized = json.dumps(report, sort_keys=True)
    for forbidden in ("BTCUSDT", "dedupe_key", "private_key", "wss://"):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_benchmark_only_accepts_fixed_worker_counts_and_repeats() -> None:
    for invalid in (False, 0, 1, 6, "3"):
        with pytest.raises(ValueError, match="2-5"):
            await measure(repeats=invalid)
    for invalid in (0, 2, 4, True):
        with pytest.raises(ValueError, match="synthetic B1"):
            await _trial(observed=False, workers=invalid)
    with pytest.raises(ValueError, match="synthetic B1"):
        await _trial(observed=1, workers=1)


def test_cli_emits_single_aggregate_only_json() -> None:
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts/benchmark_stage_b1_recorder_queue_offline.py"
    )
    result = subprocess.run(
        [sys.executable, str(script), "--repeats", "2"],
        text=True,
        capture_output=True,
        timeout=20,
        check=True,
    )
    report = json.loads(result.stdout)
    assert report["status"] == "LOCAL_SYNTHETIC_NON_GATING_NO_GO"
    assert len(report["measurements"]) == 2
    assert not report["production_overhead_accepted"]
    assert not result.stderr
