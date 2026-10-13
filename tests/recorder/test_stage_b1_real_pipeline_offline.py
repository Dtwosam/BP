"""Offline Stage B1 realistic supervisor/runner/queue/writer lifecycle tests."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.rehearse_stage_b1_real_pipeline_offline import (
    rehearse_fake_pipeline,
)


@pytest.mark.asyncio
async def test_real_recorder_service_fake_socket_gracefully_drains() -> None:
    report = await asyncio.wait_for(
        rehearse_fake_pipeline("healthy", duration_seconds=1), timeout=4
    )
    counts = report["counts"]
    assert report["status"] == "COMPLETE_AGGREGATES_ONLY"
    assert report["service_state"] == "clean"
    assert report["stop_reason"] == "window_elapsed"
    assert not report["collecting_at_readout"]
    assert counts["wire_messages_by_topic_class"] == {
        "trade": 1, "orderbook": 1, "liquidation": 1, "other": 0,
    }
    assert counts["subscribe_ack_messages_by_outcome"] == {
        "success": 1, "rejected": 0, "unclassified": 0,
    }
    assert counts["parsed_events_by_class"] == {
        "trade": 1, "orderbook": 1, "liquidation": 1, "other": 0,
    }
    assert counts["successfully_enqueued_events_by_class"] == {
        "trade": 1, "orderbook": 1, "liquidation": 1, "other": 0,
    }
    assert counts["writer_attempted_events_by_class"] == {
        "trade": 1, "orderbook": 1, "liquidation": 1, "other": 0,
    }
    assert counts["events_in_successfully_returned_transactions_by_class"] == {
        "trade": 1, "orderbook": 1, "liquidation": 1, "other": 0,
    }
    assert counts["writer_batches_started"] == 3
    assert counts["writer_batches_returned"] == 3
    assert counts["writer_batches_in_flight"] == 0
    assert report["fake_sink_only_no_database_transaction"] is True
    assert not report["inserted_rows_by_class_proven"]
    assert not report["process_terminated_proven"]
    assert not report["production_execution_authorized"]


@pytest.mark.asyncio
async def test_real_recorder_service_writer_failure_is_inconclusive() -> None:
    report = await asyncio.wait_for(
        rehearse_fake_pipeline("writer_failure"), timeout=4
    )
    assert report["status"] == "INCONCLUSIVE"
    assert report["service_state"] == "failed"
    assert report["stop_reason"] == "service_raised"
    assert report["counts"]["writer_batches_failed"] >= 1
    assert report["counts"]["writer_batches_returned"] == 0
    assert "FAKE_SECRET" not in json.dumps(report)
    assert not report["inserted_rows_by_class_proven"]


@pytest.mark.asyncio
async def test_real_recorder_service_stalled_writer_has_unresolved_batch() -> None:
    report = await asyncio.wait_for(
        rehearse_fake_pipeline("writer_cancelled"), timeout=5
    )
    assert report["status"] == "INCONCLUSIVE"
    assert report["stop_reason"] == "window_elapsed"
    assert report["counts"]["writer_batches_cancelled_unresolved"] >= 1
    assert report["counts"]["writer_batches_in_flight"] >= 1
    assert not report["process_tree_termination_proven"]
    assert not report["recorder_operationally_approved"]


@pytest.mark.asyncio
async def test_fake_pipeline_rejects_non_allowlisted_scenarios_and_durations() -> None:
    for bad in ("live", "production", "wss://stream.bybit.com", ""):
        with pytest.raises(ValueError, match="unlisted"):
            await rehearse_fake_pipeline(bad)
    for bad in (0, -1, 4, True, 1.5):
        with pytest.raises(ValueError, match="1-3 seconds"):
            await rehearse_fake_pipeline(duration_seconds=bad)


def test_offline_cli_outputs_only_fixed_aggregate_no_payloads() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "scripts/rehearse_stage_b1_real_pipeline_offline.py"
    )
    completed = subprocess.run(
        [sys.executable, str(path), "--scenario", "healthy"],
        capture_output=True, text=True, timeout=8, check=True,
    )
    report = json.loads(completed.stdout)
    assert report["status"] == "COMPLETE_AGGREGATES_ONLY"
    assert report["scope"] == (
        "offline_in_memory_socket_real_recorder_service_and_batch_writer"
    )
    assert report["fake_sink_only_no_database_transaction"]
    assert "BTCUSDT" not in completed.stdout
    assert "offline_fixture" not in completed.stdout
    assert "private_key" not in completed.stdout
    assert not completed.stderr
