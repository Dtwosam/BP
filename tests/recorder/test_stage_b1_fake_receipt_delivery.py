"""Postcommit receipt delivery gaps: fixed in-memory NO-GO regression tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import rehearse_stage_b1_fake_receipt_delivery as review

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts/rehearse_stage_b1_fake_receipt_delivery.py"
)


@pytest.mark.parametrize("scenario", review.SCENARIOS)
def test_every_scenario_has_no_production_claims(scenario: str) -> None:
    row = review.review_fake_receipts(scenario)
    assert row["status"].endswith("_NO_GO")
    assert row["fixed_fake_attempted_by_class"]["trade"] == 2
    assert row["fixed_fake_committed_inserted_by_class"]["trade"] == 1
    for key in (
        "current_local_callback_has_batch_identifier",
        "current_local_callback_has_delivery_acknowledgement",
        "current_local_callback_provides_independent_db_attestation",
        "real_commit_or_insertion_proven",
        "real_exactly_once_receipt_delivery_proven",
        "actual_on_host_process_or_db_thread_quiescence_proven",
        "production_execution_authorized",
    ):
        assert row[key] is False


def test_lost_callback_is_not_evidence_of_no_commit() -> None:
    row = review.review_fake_receipts("lost_callback")
    assert row["status"] == "FAKE_RECEIPT_DELIVERY_INCOMPLETE_NO_GO"
    assert row["fake_delivery_count"] == 0
    assert row["naive_double_count_prone_delivery_trade_total"] == 0
    assert row["fixed_fake_committed_inserted_by_class"]["trade"] == 1


def test_duplicate_notification_double_counts_without_reliable_batch_id() -> None:
    row = review.review_fake_receipts("duplicate_callback")
    assert row["fake_delivery_count"] == 2
    assert row["fake_duplicate_delivery_count"] == 1
    assert row["naive_double_count_prone_delivery_trade_total"] == 2
    assert row["idempotently_grouped_fake_trade_total"] == 1
    assert row["status"] == "FAKE_RECEIPT_SHAPE_ONLY_NO_GO"
    assert not row["real_exactly_once_receipt_delivery_proven"]


def test_conflicting_replay_must_not_silently_win() -> None:
    row = review.review_fake_receipts("conflicting_retry")
    assert row["fake_conflicting_delivery_count"] == 1
    assert row["status"] == "FAKE_RECEIPT_INVALID_OR_CONFLICTING_NO_GO"
    assert row["idempotently_grouped_fake_trade_total"] == 1
    assert row["naive_double_count_prone_delivery_trade_total"] == 3


def test_delivery_before_fake_commit_is_invalid() -> None:
    row = review.review_fake_receipts("before_commit")
    assert row["fake_before_commit_delivery_count"] == 1
    assert row["status"] == "FAKE_RECEIPT_INVALID_OR_CONFLICTING_NO_GO"


def test_late_delivery_is_not_in_completed_observation_readout() -> None:
    row = review.review_fake_receipts("late_after_readout")
    assert row["fake_late_after_readout_delivery_count"] == 1
    assert row["status"] == "FAKE_RECEIPT_DELIVERY_INCOMPLETE_NO_GO"


def test_task_cancellation_before_fake_commit_is_not_rollback() -> None:
    row = review.review_fake_receipts("cancelled_await_late_commit")
    assert row["fake_coroutine_cancelled_before_commit"]
    assert row["fake_delivery_count"] == 1
    assert row["fixed_fake_committed_inserted_by_class"]["trade"] == 1
    assert not row["real_commit_or_insertion_proven"]


@pytest.mark.parametrize("scenario", review.SCENARIOS)
def test_cli_only_emits_sanitized_bounded_fixture(scenario: str) -> None:
    run = subprocess.run(
        [sys.executable, str(SCRIPT), "--scenario", scenario],
        capture_output=True, text=True, timeout=8, check=True,
    )
    assert not run.stderr
    data = json.loads(run.stdout)
    assert data == review.review_fake_receipts(scenario)
    assert len(run.stdout.encode("utf-8")) < 2500
    forbidden_values = (
        "postgresql://", "private_key", "secret",
        "DATABASE_URL", "FIXED_FAKE_BATCH_01",
    )
    for forbidden in forbidden_values:
        assert forbidden not in run.stdout


@pytest.mark.parametrize("bad", ["", "production", "real", "connect", "query", "resume"])
def test_untrusted_scenario_is_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="fixed nonproduction"):
        review.review_fake_receipts(bad)


def test_no_real_backend_or_recorder_import() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in (
        "from bp_engine", "import sqlalchemy", "import websockets", "create_engine(",
        "get_settings(", "subprocess.", "os.system(", "socket.", "urllib.",
    ):
        assert forbidden not in source
