"""Synthetic integrity/reversal tests for the inert Stage B1 review ZIP."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from scripts import rehearse_stage_b1_review_bundle as bundle
from scripts.rehearse_stage_b1_backport import git_blob_sha


def _fake_sources(monkeypatch):
    old = b"alpha = True\n"
    updated = b"alpha = False\n"
    new = b"offline_only = True\n"
    entries = (
        ("src/bp_engine/recorder/service.py", old, updated,
         git_blob_sha(old), git_blob_sha(updated)),
        ("src/bp_engine/recorder/stage_b1_observer.py", None, new,
         None, git_blob_sha(new)),
    )
    monkeypatch.setattr(
        bundle,
        "FILES",
        tuple((path, oldsha, newsha) for path, _, _, oldsha, newsha in entries),
    )
    return entries


def _pin_fake_bundle(
    monkeypatch: pytest.MonkeyPatch, raw: bytes, report: dict[str, object]
) -> None:
    monkeypatch.setattr(
        bundle, "EXPECTED_REVIEW_ZIP_SHA256", hashlib.sha256(raw).hexdigest()
    )
    monkeypatch.setattr(
        bundle, "EXPECTED_REVIEW_PATCH_SHA256", report["source_only_patch_sha256"]
    )


def _members(raw: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_inert_bundle_deterministic_exact_patch_forward_reverse(monkeypatch) -> None:
    entries = _fake_sources(monkeypatch)
    raw1, report1 = bundle.build_review_bundle(entries)
    raw2, report2 = bundle.build_review_bundle(entries)
    assert raw1 == raw2
    assert report1 == report2
    assert report1["status"] == "PASS_OFFLINE_REVIEW_ONLY_NO_GO"
    assert report1["source_forward_apply_verified"]
    assert report1["source_byte_exact_reverse_verified"]
    assert not report1["production_execution_authorized"]
    assert not report1["includes_one_shot_launcher"]
    assert not report1["includes_process_supervisor"]

    expected = {
        bundle.MANIFEST_PATH, bundle.PATCH_PATH, bundle.README_PATH,
        *(item[0] for item in entries),
    }
    assert set(_members(raw1)) == expected
    _pin_fake_bundle(monkeypatch, raw1, report1)
    checked = bundle.verify_review_bundle(raw1)
    assert checked["status"] == "SOURCE_INTEGRITY_ONLY_NO_GO"
    assert checked["verified_file_count"] == len(expected)
    assert not checked["production_rollback_verified"]


@pytest.mark.parametrize(
    "bad",
    ["changed_before", "changed_after", "extra_path", "reordered", "missing_newline"],
)
def test_source_tampering_and_unexpected_paths_block(monkeypatch, bad: str) -> None:
    entries = list(_fake_sources(monkeypatch))
    if bad == "changed_before":
        x = list(entries[0])
        x[1] = b"tampered old\n"
        entries[0] = tuple(x)
    elif bad == "changed_after":
        x = list(entries[0])
        x[2] = b"tampered new\n"
        entries[0] = tuple(x)
    elif bad == "extra_path":
        entries.append((
            "src/extra.py", None, b"hi\n", None, git_blob_sha(b"hi\n"),
        ))
    elif bad == "reordered":
        entries.reverse()
    else:
        x = list(entries[0])
        x[2] = b"bad no newline"
        x[4] = git_blob_sha(x[2])
        pinned = list(bundle.FILES)
        pinned[0] = (pinned[0][0], pinned[0][1], x[4])
        monkeypatch.setattr(bundle, "FILES", tuple(pinned))
        entries[0] = tuple(x)
    with pytest.raises(ValueError):
        bundle.build_review_bundle(tuple(entries))


@pytest.mark.parametrize(
    "tamper",
    ["extra_file", "changed_source", "changed_patch", "changed_readme",
     "manifest_claims_activation", "manifest_claims_prod_access"],
)
def test_zip_verifier_rejects_bad_contents(monkeypatch, tamper: str) -> None:
    entries = _fake_sources(monkeypatch)
    raw, report = bundle.build_review_bundle(entries)
    _pin_fake_bundle(monkeypatch, raw, report)
    members = _members(raw)
    if tamper == "extra_file":
        members["evil/activate.py"] = b"do not execute"
    elif tamper == "changed_source":
        members[entries[0][0]] = b"tampered\n"
    elif tamper == "changed_patch":
        members[bundle.PATCH_PATH] = b"invalid-patch"
    elif tamper == "changed_readme":
        members[bundle.README_PATH] = b"INSTALL NOW"
    else:
        payload = json.loads(members[bundle.MANIFEST_PATH])
        if tamper == "manifest_claims_activation":
            payload["one_shot_activation_entry_point_present"] = True
        else:
            payload["production_execution_authorized"] = True
        members[bundle.MANIFEST_PATH] = bundle._json_bytes(payload)

    corrupted = bundle._zip_pack(members)
    # Bypass only the outer full-ZIP digest check to reach the inner fixed
    # membership, source identity, manifest and pinned patch validations.
    monkeypatch.setattr(
        bundle, "EXPECTED_REVIEW_ZIP_SHA256", hashlib.sha256(corrupted).hexdigest()
    )
    with pytest.raises(ValueError):
        bundle.verify_review_bundle(corrupted)


def test_verifier_rejects_even_valid_synthetic_zip_without_pinned_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw, _report = bundle.build_review_bundle(_fake_sources(monkeypatch))
    with pytest.raises(ValueError, match="immutable review hash"):
        bundle.verify_review_bundle(raw)


def test_zip_verifier_rejects_malformed_or_oversized_bytes() -> None:
    with pytest.raises(ValueError):
        bundle.verify_review_bundle(b"not a ZIP")
    with pytest.raises(ValueError):
        bundle.verify_review_bundle(b"x" * (bundle.MAX_ZIP_BYTES + 1))


def test_review_bundle_is_not_an_install_or_host_approval(monkeypatch) -> None:
    entries = _fake_sources(monkeypatch)
    raw, report = bundle.build_review_bundle(entries)
    manifest = json.loads(_members(raw)[bundle.MANIFEST_PATH])
    assert manifest["operator_reported_base_commit_not_host_verified"] == bundle.BASE_COMMIT
    assert manifest["reviewed_source_commit"] == bundle.REVIEW_COMMIT
    assert manifest["one_shot_activation_entry_point_present"] is False
    assert manifest["production_report_export_present"] is False
    assert manifest["real_process_supervisor_present"] is False
    assert manifest["production_execution_authorized"] is False
    assert manifest["host_access_performed"] is False
    assert report["status"].endswith("NO_GO")
    assert "NOT DEPLOYABLE" in _members(raw)[bundle.README_PATH].decode()
