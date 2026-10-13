"""Offline/fail-closed tests for source-only B1 patch generation and rollback."""

from __future__ import annotations

import hashlib

import pytest

from scripts import rehearse_stage_b1_backport as backport


def test_rehearsal_forward_reverse_is_byte_exact_and_no_host_access(monkeypatch) -> None:
    path = "src/bp_engine/recorder/service.py"
    old, new = b"old\\n", b"new\\n"
    old_sha, new_sha = backport.git_blob_sha(old), backport.git_blob_sha(new)
    monkeypatch.setattr(backport, "FILES", ((path, old_sha, new_sha),))

    patch, report = backport.rehearse(((path, old, new, old_sha, new_sha),))
    assert patch.startswith(b"--- a/src/bp_engine/recorder/service.py\\n")
    assert b"+++ b/src/bp_engine/recorder/service.py" in patch
    assert hashlib.sha256(patch).hexdigest() == report["patch_sha256"]
    assert report["status"] == "PASS_REPOSITORY_ONLY"
    assert report["forward_apply_checked"]
    assert report["reverse_apply_checked"]
    assert not report["production_host_access"]
    assert not report["production_activation_authorized"]
    assert not report["live_deployed_revision_verified"]


def test_rehearsal_can_add_new_source_and_roll_it_back(monkeypatch) -> None:
    path = "src/bp_engine/recorder/stage_b1_observer.py"
    new = b"irreversible = False\\n"
    new_sha = backport.git_blob_sha(new)
    monkeypatch.setattr(backport, "FILES", ((path, None, new_sha),))
    patch, report = backport.rehearse(((path, None, new, None, new_sha),))
    assert b"--- /dev/null" in patch
    assert b"+++ b/src/bp_engine/recorder/stage_b1_observer.py" in patch
    assert report["pre_and_post_blob_hashes_verified"]


@pytest.mark.parametrize(
    "entries",
    [
        # No unlisted files, even if hashes are otherwise legitimate.
        (("other.py", b"old\\n", b"new\\n", "no", "no"),),
        # No missing listed paths.
        (),
    ],
)
def test_rehearsal_rejects_path_set_changes(monkeypatch, entries) -> None:
    monkeypatch.setattr(
        backport, "FILES", (("src/bp_engine/recorder/service.py", "a", "b"),)
    )
    with pytest.raises(ValueError, match="path allowlist"):
        backport.rehearse(entries)


def test_rehearsal_rejects_wrong_commit_blob_and_nonterminated_source(
    monkeypatch,
) -> None:
    path = "src/bp_engine/recorder/service.py"
    old, new = b"old\\n", b"new\\n"
    expected_new = backport.git_blob_sha(new)
    monkeypatch.setattr(backport, "FILES", ((path, "a" * 40, expected_new),))
    with pytest.raises(ValueError, match="baseline Git blob hash mismatch"):
        backport.rehearse(((path, old, new, "a" * 40, expected_new),))

    correct_old = backport.git_blob_sha(old)
    bad_new = b"not newline terminated"
    bad_new_sha = backport.git_blob_sha(bad_new)
    monkeypatch.setattr(backport, "FILES", ((path, correct_old, bad_new_sha),))
    with pytest.raises(ValueError, match="newline-terminated"):
        backport.rehearse(
            ((path, old, bad_new, correct_old, bad_new_sha),)
        )
