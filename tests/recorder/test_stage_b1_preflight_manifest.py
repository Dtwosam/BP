"""Fail-closed tests for Stage B1 *offline* source-only preflight schema."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import review_stage_b1_preflight_manifest as preflight


def _fake_complete_attestation() -> dict[str, object]:
    """Synthetic claims only, never a verified real host attestation."""
    manifest = preflight.empty_template()
    sha = "a" * 64
    manifest["operator_evidence"].update({
        "observed_deployed_commit": preflight.BASE_COMMIT,
        "service_unit_sha256": sha,
        "python_executable_sha256": sha,
        "recorder_service_pid": 1234,
        "recorder_start_utc": "2026-10-13T14:00:00+00:00",
        "recorder_restart_count": 0,
        "recorder_health_verified": True,
        "mode": "research",
        "live_trading_enabled": False,
        "max_trade_size_usd": 0,
        "max_daily_loss_usd": 0.0,
        "max_total_exposure_usd": 0,
        "wallet_credentials_accessible": False,
        "auto_promotion_enabled": False,
    })
    manifest["rollback_evidence"].update({
        "previous_commit": preflight.BASE_COMMIT,
        "previous_tree_sha256": sha,
        "rollback_bundle_sha256": sha,
        "service_unit_restore_sha256": sha,
        "restore_rehearsal_evidence_sha256": sha,
        "restore_rehearsed": True,
    })
    manifest["observation_controls"].update({
        "proposed_duration_seconds": 120,
        "approved_output_contract_sha256": sha,
        "independent_process_tree_timeout_tested": True,
        "representative_overhead_review_accepted": True,
        "stop_owner_acknowledged": True,
        "queries_allowed": 0,
    })
    return manifest


def test_incomplete_template_is_always_no_go_and_has_fixed_check_labels() -> None:
    manifest = preflight.empty_template()
    report = preflight.evaluate_manifest(manifest)
    assert report["status"] == "INCOMPLETE_OR_MISMATCHED_NO_GO"
    assert report["checks"]["candidate_source_identity"]
    assert not report["checks"]["local_source_patch_bytes"]
    assert not report["checks"]["on_host_commit_attestation"]
    assert not report["production_execution_authorized"]
    assert not report["host_claims_independently_verified"]
    assert not report["includes_production_one_shot_runner"]
    assert "a352c66e" not in json.dumps(report)


def test_even_synthetically_complete_manifest_is_unverified_and_not_authorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_patch = b"fake-source-patch\\n"
    monkeypatch.setattr(preflight, "EXPECTED_PATCH_BYTES", len(fake_patch))
    monkeypatch.setattr(
        preflight, "EXPECTED_PATCH_SHA256", hashlib.sha256(fake_patch).hexdigest()
    )
    report = preflight.evaluate_manifest(
        _fake_complete_attestation(), local_patch=fake_patch
    )
    assert all(report["checks"].values())
    assert report["status"] == "STRUCTURE_COMPLETE_UNVERIFIED_NO_GO"
    assert not report["production_execution_authorized"]
    assert not report["production_deploy_or_restart_permitted"]
    assert not report["host_claims_independently_verified"]
    assert not report["includes_production_one_shot_runner"]


@pytest.mark.parametrize(
    ("group", "field", "invalid", "check"),
    [
        ("candidate", "source_patch_sha256", "0" * 64, "candidate_source_identity"),
        ("candidate", "source_patch_bytes", 20046, "candidate_source_identity"),
        ("candidate", "includes_one_shot_launcher", True, "candidate_source_identity"),
        ("operator_evidence", "mode", "paper", "research_zero_money_attestation"),
        ("operator_evidence", "live_trading_enabled", True, "research_zero_money_attestation"),
        ("operator_evidence", "max_trade_size_usd", 1, "research_zero_money_attestation"),
        ("operator_evidence", "max_daily_loss_usd", float("nan"), "research_zero_money_attestation"),
        ("operator_evidence", "wallet_credentials_accessible", True, "research_zero_money_attestation"),
        ("operator_evidence", "recorder_health_verified", 1, "runtime_identity_attestation"),
        ("operator_evidence", "recorder_service_pid", True, "runtime_identity_attestation"),
        ("operator_evidence", "recorder_restart_count", -1, "runtime_identity_attestation"),
        ("operator_evidence", "recorder_start_utc", "2026-10-13", "runtime_identity_attestation"),
        ("operator_evidence", "recorder_start_utc", "2026-10-13T14:00:00+01:00", "runtime_identity_attestation"),
        ("rollback_evidence", "previous_commit", "f" * 40, "rollback_material_attestation"),
        ("rollback_evidence", "restore_rehearsed", False, "restore_rehearsal_attestation"),
        ("observation_controls", "proposed_duration_seconds", 121, "one_shot_limits_attestation"),
        ("observation_controls", "proposed_duration_seconds", True, "one_shot_limits_attestation"),
        ("observation_controls", "queries_allowed", 1, "one_shot_limits_attestation"),
        ("observation_controls", "stop_owner_acknowledged", "yes", "one_shot_limits_attestation"),
        ("observation_controls", "independent_process_tree_timeout_tested", False, "independent_isolation_and_overhead_attestation"),
    ],
)
def test_each_unsafe_or_mismatched_field_blocks_review(
    group: str, field: str, invalid: object, check: str
) -> None:
    manifest = _fake_complete_attestation()
    manifest[group][field] = invalid
    report = preflight.evaluate_manifest(manifest, local_patch=b"wrong patch")
    assert not report["checks"][check]
    assert report["status"] == "INCOMPLETE_OR_MISMATCHED_NO_GO"


def test_input_unknown_fields_or_source_list_reordering_fail_closed() -> None:
    for group in ("candidate", "operator_evidence", "rollback_evidence", "observation_controls"):
        manifest = _fake_complete_attestation()
        manifest[group]["unknown_secret"] = "value"
        report = preflight.evaluate_manifest(manifest)
        assert not report["checks"]["manifest_shape"]
        assert "value" not in json.dumps(report)

    manifest = _fake_complete_attestation()
    manifest["unknown_root_secret"] = "PRIVATE_ROOT_FIELD"
    assert not preflight.evaluate_manifest(manifest)["checks"]["manifest_shape"]

    manifest = _fake_complete_attestation()
    manifest["candidate"]["file_blobs"].reverse()
    assert not preflight.evaluate_manifest(manifest)["checks"]["candidate_source_identity"]


def test_manifest_bad_type_or_host_secrets_not_echoed() -> None:
    for invalid in (None, [], {}, 1, {"schema": "other"}):
        report = preflight.evaluate_manifest(invalid)
        assert report["status"] == "INCOMPLETE_OR_MISMATCHED_NO_GO"

    manifest = _fake_complete_attestation()
    manifest["operator_evidence"]["python_executable_sha256"] = "PRIVATE_TOKEN_VALUE"
    report = preflight.evaluate_manifest(manifest)
    assert "PRIVATE_TOKEN_VALUE" not in json.dumps(report)
    assert not report["checks"]["runtime_identity_attestation"]


def test_repository_incomplete_template_matches_factory() -> None:
    template = (
        Path(__file__).resolve().parents[2]
        / "docs/evidence/phase14-b1-INCOMPLETE-offline-preflight-template-20261013.json"
    )
    assert json.loads(template.read_text(encoding="utf-8")) == preflight.empty_template()


def test_cli_incomplete_template_exits_two_prints_only_sanitized_status(
    tmp_path: Path,
) -> None:
    source = tmp_path / "preflight.json"
    unsafe = preflight.empty_template()
    unsafe["operator_evidence"]["service_unit_sha256"] = "PRIVATE_HOST_VALUE"
    source.write_text(json.dumps(unsafe), encoding="utf-8")
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts/review_stage_b1_preflight_manifest.py"
    )
    run = subprocess.run(
        [sys.executable, str(script), "--manifest", str(source)],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert run.returncode == 2
    report = json.loads(run.stdout)
    assert report["status"] == "INCOMPLETE_OR_MISMATCHED_NO_GO"
    assert "PRIVATE_HOST_VALUE" not in run.stdout
    assert not run.stderr


def test_bounded_file_read_rejects_oversized_data(tmp_path: Path) -> None:
    target = tmp_path / "large.json"
    target.write_bytes(b"x" * (preflight.MAX_MANIFEST_BYTES + 1))
    assert preflight._read_bounded(target, preflight.MAX_MANIFEST_BYTES) is None
    assert preflight._read_bounded(tmp_path / "missing", 100) is None
