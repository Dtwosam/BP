"""Fail-closed review-only Stage B1 lifecycle/restoration evidence contracts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import review_stage_b1_lifecycle_contract as review

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (
    ROOT
    / "docs/evidence/phase14-b1-INCOMPLETE-lifecycle-review-template-20261014.json"
)
DOCUMENT = (
    ROOT
    / "docs/PHASE-14-V4-B1-OPERATOR-LIFECYCLE-RESTORE-CONTRACT-20261014.md"
)
SCRIPT = ROOT / "scripts/review_stage_b1_lifecycle_contract.py"


def test_shipped_template_exactly_incomplete_and_no_go() -> None:
    data = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    assert data == review.empty_template()
    assert len(data["gate_claims"]) == 17
    assert all(value is None for value in data["gate_claims"].values())
    result = review.evaluate_contract(data)
    assert result["status"] == "INCOMPLETE_OR_INVALID_NO_GO"
    assert result["schema_valid"] is True
    assert result["fixed_scope_matches"] is True
    assert result["allowlisted_claim_tokens_only"] is True
    assert result["claimed_gate_count"] == 0
    assert result["missing_or_invalid_gate_names"] == list(review.GATE_NAMES)
    assert result["production_execution_authorized"] is False


def test_every_self_claimed_gate_is_still_unverified_and_not_authorized() -> None:
    data = review.empty_template()
    data["gate_claims"] = dict.fromkeys(review.GATE_NAMES, "claimed_reviewed")
    result = review.evaluate_contract(data)
    assert result["status"] == "CLAIMS_FORMAT_COMPLETE_UNVERIFIED_NO_GO"
    assert result["missing_or_invalid_gate_names"] == []
    assert result["claimed_gate_count"] == result["total_gate_count"] == 17
    for denied in (
        "claims_independently_authenticated",
        "production_execution_authorized",
        "production_deploy_or_restart_permitted",
        "operational_candidate_present",
        "production_queries_performed",
        "actual_process_tree_termination_verified",
        "actual_db_worker_quiescence_verified",
        "actual_systemd_restoration_verified",
        "actual_production_overhead_accepted",
    ):
        assert result[denied] is False


@pytest.mark.parametrize("missing_gate", review.GATE_NAMES)
def test_omitting_any_single_gate_fails_closed(missing_gate: str) -> None:
    data = review.empty_template()
    data["gate_claims"] = dict.fromkeys(review.GATE_NAMES, "claimed_reviewed")
    data["gate_claims"][missing_gate] = None
    result = review.evaluate_contract(data)
    assert result["status"] == "INCOMPLETE_OR_INVALID_NO_GO"
    assert result["missing_or_invalid_gate_names"] == [missing_gate]


@pytest.mark.parametrize(
    ("scope_key", "bad_value"),
    [
        ("observer_duration_upper_bound_seconds", 121),
        ("observer_duration_upper_bound_seconds", True),
        ("database_queries_allowed", 1),
        ("database_queries_allowed", False),
        ("real_money_operations_allowed", 0),
        ("live_trading_enabled", 0),
        ("live_trading_enabled", True),
        ("approved_operator_launcher_available", True),
        ("host_operations_permitted_by_this_review", True),
        ("target", "all_feeds"),
    ],
)
def test_scope_change_blocks_even_all_claimed_evidence(
    scope_key: str, bad_value: object,
) -> None:
    data = review.empty_template()
    data["gate_claims"] = dict.fromkeys(review.GATE_NAMES, "claimed_reviewed")
    data["proposed_scope"][scope_key] = bad_value
    result = review.evaluate_contract(data)
    assert result["status"] == "INCOMPLETE_OR_INVALID_NO_GO"
    assert result["fixed_scope_matches"] is False
    assert result["production_execution_authorized"] is False


def test_untrusted_extra_and_missing_fields_never_change_contract() -> None:
    for section in ("top", "scope", "gates"):
        data = review.empty_template()
        if section == "top":
            data["private_key"] = "VERY_SENSITIVE_CANARY"
        elif section == "scope":
            data["proposed_scope"]["unplanned_restart"] = True
        else:
            data["gate_claims"]["unexpected_execution_grant"] = "claimed_reviewed"
        result = review.evaluate_contract(data)
        assert result["schema_valid"] is False
        assert "VERY_SENSITIVE_CANARY" not in json.dumps(result)
        assert "unexpected_execution_grant" not in json.dumps(result)

    data = review.empty_template()
    del data["gate_claims"][review.GATE_NAMES[0]]
    assert review.evaluate_contract(data)["schema_valid"] is False


@pytest.mark.parametrize(
    "bad_value", [True, False, 1, 0, "VERIFIED", "real_host", [], {}, "pass"]
)
def test_unknown_claim_token_does_not_count_as_reviewed(bad_value: object) -> None:
    data = review.empty_template()
    data["gate_claims"] = dict.fromkeys(review.GATE_NAMES, "claimed_reviewed")
    data["gate_claims"][review.GATE_NAMES[2]] = bad_value
    result = review.evaluate_contract(data)
    assert result["allowlisted_claim_tokens_only"] is False
    assert result["status"] == "INCOMPLETE_OR_INVALID_NO_GO"


@pytest.mark.parametrize("value", [None, [], 1, "secret", {}, {"schema": "wrong"}])
def test_nonmanifest_data_is_sanitized_no_go(value: object) -> None:
    result = review.evaluate_contract(value)
    assert result["status"] == "INCOMPLETE_OR_INVALID_NO_GO"
    assert result["schema_valid"] is False
    assert result["production_deploy_or_restart_permitted"] is False


@pytest.mark.parametrize("fill", [False, True])
def test_cli_exits_nonzero_even_for_complete_claims(
    tmp_path: Path, fill: bool,
) -> None:
    data = review.empty_template()
    if fill:
        data["gate_claims"] = dict.fromkeys(review.GATE_NAMES, "claimed_reviewed")
    path = tmp_path / "review.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    run = subprocess.run(
        [sys.executable, str(SCRIPT), "--manifest", str(path)],
        capture_output=True, text=True, check=False, timeout=10,
    )
    assert run.returncode == 2
    result = json.loads(run.stdout)
    assert result["status"].endswith("_NO_GO")
    assert result["production_execution_authorized"] is False
    assert not run.stderr


def test_cli_rejects_missing_oversized_and_contaminated_inputs(
    tmp_path: Path,
) -> None:
    for data in (
        b"not JSON",
        b"x" * (review.MAX_INPUT_BYTES + 1),
        b'{"schema": "PRIVATE_AUTHORIZATION_TOKEN"}',
    ):
        path = tmp_path / "untrusted.json"
        path.write_bytes(data)
        run = subprocess.run(
            [sys.executable, str(SCRIPT), "--manifest", str(path)],
            capture_output=True, text=True, check=False, timeout=10,
        )
        assert run.returncode == 2
        result = json.loads(run.stdout)
        assert result["status"] == "INCOMPLETE_OR_INVALID_NO_GO"
        assert "PRIVATE_AUTHORIZATION_TOKEN" not in run.stdout
        assert not run.stderr

    assert review._bounded_read(tmp_path / "nonexistent.json") is None


def test_contract_document_enumerates_every_evidence_gate_and_no_go() -> None:
    content = DOCUMENT.read_text(encoding="utf-8")
    for name in review.GATE_NAMES:
        assert name in content
    assert "NO GO" in content
    assert "not" in content.lower()
    assert "independent" in content.lower()
    assert "Phase 14" in content
