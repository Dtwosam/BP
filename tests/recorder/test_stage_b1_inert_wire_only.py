"""Wire-only B1 option: real collector runner, fixed fake socket, no DB."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.rehearse_stage_b1_inert_wire_only import (
    SCENARIOS,
    _FAKE_URL,
    _FakeSocket,
    _frames,
    run_fake_wire_only,
)

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "rehearse_stage_b1_inert_wire_only.py"
_CATEGORIES = {"trade", "orderbook", "liquidation", "other"}


@pytest.mark.asyncio
async def test_all_bulk_frames_are_seen_and_parsed_but_discarded() -> None:
    result = await run_fake_wire_only("bulk_all")
    assert result["status"] == "OFFLINE_FIXED_FAKE_WIRE_ONLY_NO_GO"
    assert result["classification"] == "fake_bulk_frames_parsed_downstream_unknown"
    assert result["fixed_subscription_sent_exactly_once"] is True
    assert result["fixed_fake_socket_entered_and_exited"] is True
    assert result["fake_wire_messages_by_class"] == {
        "trade": 1, "orderbook": 1, "liquidation": 1, "other": 0
    }
    assert result["fake_parsed_events_by_class"] == result["fake_wire_messages_by_class"]
    assert result["fake_subscribe_ack_socket_outcome"] == {
        "success": 1, "rejected": 0, "unclassified": 0
    }
    assert result["fake_discarded_events_not_written"] == 3
    assert result["fake_parser_errors"] == 0
    assert result["fake_error_incidents"] == 0
    assert result["fake_connection_incidents"] == 1
    assert result["inserted_rows_proven"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "classification", "discarded", "errors", "rejected"),
    [
        ("ack_only", "fake_no_bulk_frames", 0, 0, 0),
        ("parse_failure", "fake_parser_exception", 0, 1, 0),
        ("ticker_excluded", "fake_no_bulk_frames", 1, 0, 0),
        ("subscribe_rejected", "fake_no_bulk_frames", 0, 0, 1),
    ],
)
async def test_missing_frames_and_parser_error_do_not_infer_database_state(
    scenario: str, classification: str, discarded: int, errors: int, rejected: int
) -> None:
    x = await run_fake_wire_only(scenario)
    assert x["classification"] == classification
    assert x["fake_discarded_events_not_written"] == discarded
    assert x["fake_parser_errors"] == errors
    assert x["fake_subscribe_ack_socket_outcome"]["rejected"] == rejected
    assert set(x["fake_wire_messages_by_class"]) == _CATEGORIES
    assert set(x["fake_parsed_events_by_class"]) == _CATEGORIES
    assert x["fixed_fake_socket_entered_and_exited"] is True
    assert x["real_exchange_frames_received"] is False
    assert x["database_write_or_commit_proven"] is False
    assert x["production_execution_authorized"] is False


@pytest.mark.asyncio
async def test_rejected_ack_does_not_imply_per_topic_outcome() -> None:
    x = await run_fake_wire_only("subscribe_rejected")
    assert x["fake_subscribe_ack_socket_outcome"]["rejected"] == 1
    assert x["per_topic_subscription_accepted_proven"] is False
    y = await run_fake_wire_only("bulk_all")
    assert y["fake_subscribe_ack_socket_outcome"]["success"] == 1
    assert y["per_topic_subscription_accepted_proven"] is False


@pytest.mark.asyncio
async def test_ticker_is_excluded_from_bulk_but_not_falsely_called_unparsed() -> None:
    x = await run_fake_wire_only("ticker_excluded")
    assert sum(x["fake_wire_messages_by_class"].values()) == 0
    assert sum(x["fake_parsed_events_by_class"].values()) == 0
    assert x["fake_discarded_events_not_written"] == 1
    assert x["classification"] == "fake_no_bulk_frames"


def test_fake_socket_rejects_network_url_and_exhaustion() -> None:
    assert _FAKE_URL == "fixed-memory-fixture-only"
    for invalid in ("unknown", "", "real_host", "https://example.org"):
        with pytest.raises(ValueError, match="fixed offline"):
            _frames(invalid)
    stop = asyncio.Event()
    sock = _FakeSocket(_frames("ack_only"), stop)

    async def run() -> None:
        assert isinstance(await sock.recv(), str)
        assert stop.is_set()
        with pytest.raises(RuntimeError, match="fixed fake frames exhausted"):
            await sock.recv()

    asyncio.run(run())


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_cli_report_is_fixed_aggregate_only_and_no_go(scenario: str) -> None:
    run = subprocess.run(
        [sys.executable, str(_SCRIPT), "--scenario", scenario],
        capture_output=True, text=True, check=True, timeout=10,
    )
    data = json.loads(run.stdout)
    assert data["status"] == "OFFLINE_FIXED_FAKE_WIRE_ONLY_NO_GO"
    assert data["scenario"] == scenario
    assert data["production_execution_authorized"] is False
    assert data["per_topic_subscription_accepted_proven"] is False
    assert data["real_exchange_frames_received"] is False
    assert data["database_write_or_commit_proven"] is False
    assert data["inserted_rows_proven"] is False
    assert data["production_process_lifetime_bounded"] is False
    assert len(run.stdout.encode("utf-8")) < 2500
    for secret in (
        "PRIVATE_ACK_TEXT", "PRIVATE_PRICE", "PRIVATE_INVALID_TIMESTAMP",
        "PRIVATE_TICKER", '"data":', '"payload":', "https://",
    ):
        assert secret not in run.stdout
    assert not run.stderr


def test_no_external_connector_or_recorder_factory_import_in_fake_script() -> None:
    source = _SCRIPT.read_text(encoding="utf-8")
    assert "from websockets" not in source
    assert "from sqlalchemy" not in source
    assert "build_default_recorder_service" not in source
    assert "create_engine(" not in source
    assert "asyncio.create_subprocess" not in source
    assert "os.system(" not in source
