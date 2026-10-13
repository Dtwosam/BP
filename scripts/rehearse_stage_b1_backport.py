"""Reproduce and reverse-check the source-only Stage B1 backport OFFLINE.

Reads exactly three Git blobs at two immutable commits from the LOCAL Git
object database; never fetches, SSHes, queries a database, or contacts a VM.
It does not install, activate or schedule an observer.

This is an engineering rehearsal, NOT authorization for deployment.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

BASE_COMMIT = "a352c66ec0110925727bc40de767ee4ba981f965"
TARGET_COMMIT = "75281f2b97f19d903f576b973bb7c2236179839e"
FILES: tuple[tuple[str, str | None, str], ...] = (
    (
        "src/bp_engine/collectors/websocket_runner.py",
        "2620416744b46551f31ecd5f593c1b34d125d4a5",
        "c330695c25f61e07a9981581919b7bf05a701ce9",
    ),
    (
        "src/bp_engine/recorder/service.py",
        "a3f1095bed359e6b05bfeb8b83a8e8cccd829591",
        "ef5de19c8efb39cf874f5620a0b9098c07824475",
    ),
    (
        "src/bp_engine/recorder/stage_b1_observer.py",
        None,  # absent at the old deployed-from source commit
        "f5842e1143ca7fc5a2cb201b61d5575e7b9e5959",
    ),
)


def git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\\0".encode("ascii")
    return hashlib.sha1(header + content, usedforsecurity=False).hexdigest()


def _git_show(commit: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        capture_output=True,
        check=True,
        timeout=15,
    )
    return result.stdout


def _git_exists(commit: str, path: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}:{path}"],
        capture_output=True,
        check=False,
        timeout=15,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1 or result.returncode == 128:
        return False
    raise RuntimeError("unexpected Git object availability error")


def _unified_diff(path: str, before: bytes | None, after: bytes) -> bytes:
    before_lines = (before or b"").decode("utf-8").splitlines(keepends=True)
    after_lines = after.decode("utf-8").splitlines(keepends=True)
    if not after_lines or not after.endswith(b"\\n"):
        raise ValueError("target source must be nonempty, newline-terminated UTF-8")
    if before is not None and not before.endswith(b"\\n"):
        raise ValueError("baseline source must be newline-terminated UTF-8")
    return "".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile="a/" + path if before is not None else "/dev/null",
            tofile="b/" + path,
            n=3,
        )
    ).encode("utf-8")


def _run_apply(directory: Path, patch: bytes, *, reverse: bool = False) -> None:
    options = ["-R"] if reverse else []
    for check in (True, False):
        subprocess.run(
            ["git", "apply", *options, *(["--check"] if check else []), "-"],
            input=patch,
            cwd=directory,
            capture_output=True,
            check=True,
            timeout=15,
        )


def rehearse(
    entries: tuple[tuple[str, bytes | None, bytes, str | None, str], ...],
) -> tuple[bytes, dict[str, object]]:
    """Stage ONLY allowlisted source files in a disposable local directory."""
    expected_paths = {path for path, _, _ in FILES}
    if {entry[0] for entry in entries} != expected_paths or len(entries) != len(FILES):
        raise ValueError("B1 backport path allowlist violation")
    pieces: list[bytes] = []
    with tempfile.TemporaryDirectory(prefix="bp-b1-source-rehearsal-") as tmp:
        root = Path(tmp)
        for path, before, after, old_sha, target_sha in entries:
            if Path(path).is_absolute() or ".." in Path(path).parts:
                raise ValueError("unsafe patch path")
            if (git_blob_sha(before) if before is not None else None) != old_sha:
                raise ValueError("baseline Git blob hash mismatch: " + path)
            if git_blob_sha(after) != target_sha:
                raise ValueError("candidate Git blob hash mismatch: " + path)
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if before is not None:
                target.write_bytes(before)
            pieces.append(_unified_diff(path, before, after))

        patch = b"".join(pieces)
        if not patch or b"Binary files" in patch:
            raise ValueError("empty or binary B1 patch")
        _run_apply(root, patch)
        for path, before, after, _, target_sha in entries:
            assert (root / path).read_bytes() == after
            assert git_blob_sha((root / path).read_bytes()) == target_sha

        _run_apply(root, patch, reverse=True)
        for path, before, _, old_sha, _ in entries:
            target = root / path
            if before is None:
                if target.exists():
                    raise RuntimeError("rollback left new B1 observer behind")
            elif target.read_bytes() != before or git_blob_sha(before) != old_sha:
                raise RuntimeError("rollback byte identity mismatch: " + path)

    return patch, {
        "report": "phase14_b1_exact_source_backport_rehearsal_v1",
        "status": "PASS_REPOSITORY_ONLY",
        "previously_reported_deployed_from_commit": BASE_COMMIT,
        "source_target_commit": TARGET_COMMIT,
        "patch_sha256": hashlib.sha256(patch).hexdigest(),
        "patch_bytes": len(patch),
        "allowed_file_paths": [path for path, _, _ in FILES],
        "pre_and_post_blob_hashes_verified": True,
        "forward_apply_checked": True,
        "reverse_apply_checked": True,
        "live_deployed_revision_verified": False,
        "production_host_access": False,
        "production_activation_authorized": False,
        "limitations": [
            "A prior operator-reported commit is not current on-host identity proof.",
            "This rehearses three source blobs only, not systemd/venv/runtime rollback.",
            "No service run, observer output, network, SQL or per-class insert proof.",
            "A one-shot collector and actual VM overhead approval remain outstanding.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-patch",
        type=Path,
        help="Optional offline output filename, created exclusively (never overwritten)",
    )
    args = parser.parse_args()
    entries = []
    for path, old_sha, target_sha in FILES:
        if old_sha is None:
            if _git_exists(BASE_COMMIT, path):
                raise RuntimeError("observer unexpectedly exists at frozen base")
            before = None
        else:
            before = _git_show(BASE_COMMIT, path)
        after = _git_show(TARGET_COMMIT, path)
        entries.append((path, before, after, old_sha, target_sha))
    patch, report = rehearse(tuple(entries))
    if args.write_patch is not None:
        with args.write_patch.open("xb") as stream:
            stream.write(patch)
        report["patch_written_to_requested_offline_path"] = True
    else:
        report["patch_written_to_requested_offline_path"] = False
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
