"""Fixed Stage B1 synthetic counterexample to wire-only root-cause claims."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import review_stage_b1_observability_equivalence as review

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts/review_stage_b1_observability_equivalence.py"
)


def test_separate_wire_probe_cannot_distinguish_six_recorder_worlds() -> None:
    result = review.review_equivalence("separate_wire_only")
    assert result["status"] == "FIXED_SYNTHETIC_DECISION_REVIEW_NO_GO"
    assert result["fixture_world_count"] == 6
    assert result["equivalence_class_count"] == 1
    assert result["ambiguous_hypothesis_pair_count"] == 15
    assert result["indistinguishable_fixture_world_groups"] == [
        list(review.WORLD_NAMES)
    ]
    assert result["separate_probe_is_not_same_recorder_evidence"] is True
    assert result["production_root_cause_identified"] is False
    assert result["production_execution_authorized"] is False


def test_same_recorder_dormant_observer_cannot_disambiguate_dedupe() -> None:
    result = review.review_equivalence("same_recorder_dormant_b1")
    assert result["equivalence_class_count"] == 5
    assert result["ambiguous_hypothesis_pair_count"] == 1
    assert result["indistinguishable_fixture_world_groups"] == [[
        "recorder_transaction_all_deduped",
        "recorder_new_rows_inserted",
    ]]
    assert result["same_recorder_batch_counter_is_attempts_not_insertions"]
    assert result["hypothetical_insert_receipt_is_unimplemented_in_production"]
    assert not result["production_root_cause_identified"]


def test_hypothetical_same_batch_receipts_only_distinguish_fixed_worlds() -> None:
    result = review.review_equivalence(
        "same_recorder_plus_verified_postcommit"
    )
    assert result["equivalence_class_count"] == 6
    assert result["ambiguous_hypothesis_pair_count"] == 0
    assert result["indistinguishable_fixture_world_groups"] == []
    assert result["hypothetical_receipt_assumes_verified_commit_and_same_batch"]
    assert result["any_single_stage_pattern_proves_unique_cause"] is False
    assert result["production_execution_authorized"] is False
    assert result["independent_stop_restoration_verified"] is False


@pytest.mark.parametrize("view", review.VIEWS)
def test_exact_synthetic_report_contract_has_no_payloads_or_authority(
    view: str,
) -> None:
    report = review.review_equivalence(view)
    assert report["view"] == view
    assert report["status"].endswith("_NO_GO")
    assert report["fixture_world_count"] == len(review.WORLD_NAMES)
    assert report["same_recorder_counts_are_hypothetical"]
    assert not report["actual_host_source_identity_verified"]
    assert not report["independent_stop_restoration_verified"]
    assert not report["production_root_cause_identified"]
    assert not report["production_execution_authorized"]
    raw = json.dumps(report, sort_keys=True)
    assert len(raw.encode("utf-8")) < 2500
    for leaked in (
        "DATABASE_URL", "private_key", "SECRET_", "ExecStart=",
        "systemctl", "postgresql://", "PRIVATE_PRICE",
    ):
        assert leaked not in raw


@pytest.mark.parametrize("invalid", ["", "prod", "live", "all", "real_sql"])
def test_only_three_fixed_views_are_accepted(invalid: str) -> None:
    with pytest.raises(ValueError, match="only fixed observability"):
        review.review_equivalence(invalid)


@pytest.mark.parametrize("view", review.VIEWS)
def test_cli_emits_only_fixed_no_go_evidence(view: str) -> None:
    run = subprocess.run(
        [sys.executable, str(SCRIPT), "--view", view],
        capture_output=True, text=True, check=True, timeout=8,
    )
    assert not run.stderr
    data = json.loads(run.stdout)
    assert data == review.review_equivalence(view)
    assert data["production_execution_authorized"] is False


def test_report_comparability_and_explicit_no_go_boundaries() -> None:
    a, b, c = [review.review_equivalence(view) for view in review.VIEWS]
    assert set(a) == set(b) == set(c)
    assert a["ambiguous_hypothesis_pair_count"] > (
        b["ambiguous_hypothesis_pair_count"]
    ) > c["ambiguous_hypothesis_pair_count"]
    assert list(review.WORLD_NAMES) == [
        "recorder_no_bulk_frames",
        "recorder_parser_no_events",
        "recorder_enqueue_no_returns",
        "recorder_writer_no_returns",
        "recorder_transaction_all_deduped",
        "recorder_new_rows_inserted",
    ]


def test_no_imported_production_runtime_or_real_connector() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for phrase in (
        "from bp_engine", "import websockets", "import sqlalchemy",
        "create_engine(", "subprocess.", "socket.", "requests.",
        "urllib.", "os.system(", "get_settings(",
    ):
        assert phrase not in source
