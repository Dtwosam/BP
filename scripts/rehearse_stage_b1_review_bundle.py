"""Build and verify a deterministic, OFFLINE, non-deployable B1 review ZIP.

Reads only local Git objects at two immutable commits, never fetches or
executes code in the bundle. The archive includes reviewed source, a unified
source patch, fixed manifest and a NO-GO readme. A reversible source patch
is NOT a service, process-tree or database rollback.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import tempfile
import zipfile
from pathlib import Path

if __package__:
    from scripts.rehearse_stage_b1_backport import (
        _git_exists,
        _git_show,
        _run_apply,
        _unified_diff,
        git_blob_sha,
    )
else:
    from rehearse_stage_b1_backport import (
        _git_exists,
        _git_show,
        _run_apply,
        _unified_diff,
        git_blob_sha,
    )

BASE_COMMIT = "a352c66ec0110925727bc40de767ee4ba981f965"
REVIEW_COMMIT = "347db08ee47d113f35fd6a9bc0e4bd85610aea0d"
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
        None,
        "bd0d4a77051efb15d7eee0f0feef05fa8eaa7c8c",
    ),
    (
        "src/bp_engine/recorder/stage_b1_one_shot.py",
        None,
        "cae616873e83a56d3a12aaae50a4b8825ebad999",
    ),
)
MANIFEST_PATH = "B1-REVIEW-MANIFEST.json"
PATCH_PATH = "B1-REVIEW-SOURCE-ONLY.patch"
README_PATH = "B1-NO-GO-READ-ME.txt"
MAX_ZIP_BYTES = 256 * 1024
README = (
    "B1 SOURCE-ONLY REVIEW PACKET - NOT DEPLOYABLE OR AUTHORIZED\n"
    "The regular recorder never injects StageB1BulkObserver.\n"
    "No production one-shot entry point or report destination is provided.\n"
    "The POSIX watchdog in PR #609 tests only fake subprocesses; it is NOT\n"
    "an operator supervisor and is intentionally absent from this packet.\n"
    "Git source rollback verification does NOT restore systemd, process\n"
    "trees, PostgreSQL threads or host runtime and does not prove health.\n"
    "The old previously reported host source SHA is not live-verified.\n"
    "Stage B1 remains inactive; Phase 14 HOLD, economics NOT MET.\n"
).encode("ascii")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json_bytes(data: object) -> bytes:
    return (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _zip_pack(members: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o100644 << 16
            info.create_system = 3
            archive.writestr(info, members[name])
    packed = output.getvalue()
    if not packed or len(packed) > MAX_ZIP_BYTES:
        raise ValueError("B1 offline review packet out of bounds")
    return packed


def _expected_paths() -> tuple[str, ...]:
    return tuple(path for path, _, _ in FILES)


def build_review_bundle(
    entries: tuple[tuple[str, bytes | None, bytes, str | None, str], ...],
) -> tuple[bytes, dict[str, object]]:
    """Check pinned blobs, source forward/reverse, then pack inert contents."""
    if (
        len(entries) != len(FILES)
        or tuple(item[0] for item in entries) != _expected_paths()
        or len({item[0] for item in entries}) != len(entries)
    ):
        raise ValueError("B1 candidate source allowlist or file order mismatch")

    patch_parts = []
    reviewed = {}
    file_metadata = []
    with tempfile.TemporaryDirectory(prefix="bp-b1-review-") as tmp:
        root = Path(tmp)
        for (path, before, after, old_sha, new_sha), pinned in zip(
            entries, FILES, strict=True
        ):
            if (
                path != pinned[0]
                or old_sha != pinned[1]
                or new_sha != pinned[2]
                or path.startswith("/")
                or ".." in Path(path).parts
            ):
                raise ValueError("B1 path or immutable blob identity changed")
            if (git_blob_sha(before) if before is not None else None) != old_sha:
                raise ValueError("B1 base Git blob mismatch")
            if git_blob_sha(after) != new_sha:
                raise ValueError("B1 reviewed Git blob mismatch")
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if before is not None:
                target.write_bytes(before)
            patch_parts.append(_unified_diff(path, before, after))
            reviewed[path] = after
            file_metadata.append({
                "path": path,
                "before_git_blob_sha1": old_sha,
                "after_git_blob_sha1": new_sha,
                "after_sha256": _sha256(after),
            })

        patch = b"".join(patch_parts)
        if not patch or len(patch) > 128 * 1024:
            raise ValueError("B1 patch missing or oversized")
        _run_apply(root, patch)
        for path, content in reviewed.items():
            if (root / path).read_bytes() != content:
                raise ValueError("B1 forward application does not match bytes")
        _run_apply(root, patch, reverse=True)
        for path, before, _, _, _ in entries:
            file = root / path
            if before is None and file.exists():
                raise ValueError("B1 reverse left new file behind")
            if before is not None and file.read_bytes() != before:
                raise ValueError("B1 reverse changed base bytes")

    manifest = {
        "schema": "phase14_b1_inert_review_bundle_v1",
        "status": "SOURCE_REVERSIBLE_REVIEW_ONLY_NO_GO",
        "operator_reported_base_commit_not_host_verified": BASE_COMMIT,
        "reviewed_source_commit": REVIEW_COMMIT,
        "reviewed_files": file_metadata,
        "source_only_patch_sha256": _sha256(patch),
        "source_only_patch_bytes": len(patch),
        "source_forward_apply_verified": True,
        "source_byte_exact_reverse_verified": True,
        "one_shot_activation_entry_point_present": False,
        "production_report_export_present": False,
        "real_process_supervisor_present": False,
        "production_execution_authorized": False,
        "host_access_performed": False,
        "limitations": [
            "This ZIP cannot be run as a production Stage B1 one-shot.",
            "The operator-reported old commit has not been checked on the host.",
            "Byte-exact source reverse is not a systemd/DB/venv restore.",
            "Actual recorder CPU/RSS/queue/DB overhead is not measured.",
            "Additional production design review and explicit approval required.",
        ],
    }
    members = {
        **reviewed,
        MANIFEST_PATH: _json_bytes(manifest),
        PATCH_PATH: patch,
        README_PATH: README,
    }
    zipped = _zip_pack(members)
    return zipped, {
        "report": "phase14_b1_inert_review_bundle_build_v1",
        "status": "PASS_OFFLINE_REVIEW_ONLY_NO_GO",
        "bundle_sha256": _sha256(zipped),
        "bundle_bytes": len(zipped),
        "source_only_patch_sha256": _sha256(patch),
        "source_only_patch_bytes": len(patch),
        "reviewed_source_commit": REVIEW_COMMIT,
        "source_forward_apply_verified": True,
        "source_byte_exact_reverse_verified": True,
        "archive_file_count": len(members),
        "production_execution_authorized": False,
        "host_access_performed": False,
        "includes_one_shot_launcher": False,
        "includes_process_supervisor": False,
    }


def verify_review_bundle(contents: bytes) -> dict[str, object]:
    """Strictly check deterministic membership and source hashes; never extract."""
    expected = set(_expected_paths()) | {
        MANIFEST_PATH, PATCH_PATH, README_PATH,
    }
    if len(contents) > MAX_ZIP_BYTES or not contents:
        raise ValueError("B1 archive exceeds bound or is empty")
    try:
        with zipfile.ZipFile(io.BytesIO(contents)) as archive:
            files = archive.infolist()
            names = [file.filename for file in files]
            if len(names) != len(expected) or set(names) != expected:
                raise ValueError("B1 ZIP membership mismatch")
            if any(
                file.file_size > 128 * 1024
                or file.compress_type != zipfile.ZIP_STORED
                or file.is_dir()
                or file.date_time != (1980, 1, 1, 0, 0, 0)
                for file in files
            ):
                raise ValueError("B1 ZIP metadata/size mismatch")
            members = {name: archive.read(name) for name in names}
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise ValueError("invalid B1 ZIP bytes") from exc
    manifest = json.loads(members[MANIFEST_PATH])
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != "phase14_b1_inert_review_bundle_v1"
        or manifest.get("status") != "SOURCE_REVERSIBLE_REVIEW_ONLY_NO_GO"
        or manifest.get("operator_reported_base_commit_not_host_verified") != BASE_COMMIT
        or manifest.get("reviewed_source_commit") != REVIEW_COMMIT
        or manifest.get("production_execution_authorized") is not False
        or manifest.get("one_shot_activation_entry_point_present") is not False
        or manifest.get("production_report_export_present") is not False
        or manifest.get("real_process_supervisor_present") is not False
        or manifest.get("source_forward_apply_verified") is not True
        or manifest.get("source_byte_exact_reverse_verified") is not True
        or members[README_PATH] != README
        or manifest.get("source_only_patch_sha256") != _sha256(members[PATCH_PATH])
        or manifest.get("source_only_patch_bytes") != len(members[PATCH_PATH])
        or not isinstance(manifest.get("reviewed_files"), list)
        or len(manifest["reviewed_files"]) != len(FILES)
    ):
        raise ValueError("B1 bundle manifest invalid")
    for record, (path, old_sha, new_sha) in zip(
        manifest["reviewed_files"], FILES, strict=True
    ):
        source = members[path]
        if record != {
            "path": path,
            "before_git_blob_sha1": old_sha,
            "after_git_blob_sha1": new_sha,
            "after_sha256": _sha256(source),
        } or git_blob_sha(source) != new_sha:
            raise ValueError("B1 source identity changed")
    return {
        "report": "phase14_b1_inert_review_bundle_verify_v1",
        "status": "SOURCE_INTEGRITY_ONLY_NO_GO",
        "bundle_sha256": _sha256(contents),
        "verified_file_count": len(expected),
        "production_execution_authorized": False,
        "host_access_performed": False,
        "production_rollback_verified": False,
    }


def _read_bounded(path: Path) -> bytes:
    with path.open("rb") as file:
        content = file.read(MAX_ZIP_BYTES + 1)
    if len(content) > MAX_ZIP_BYTES:
        raise ValueError("B1 local ZIP too large")
    return content


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument(
        "--write-review-only-zip", type=Path,
        help="Optional exclusive local ZIP output; NEVER an installable package",
    )
    choice.add_argument(
        "--verify-review-only-zip", type=Path,
        help="Read an existing bounded local ZIP without extracting/executing",
    )
    args = parser.parse_args()
    if args.verify_review_only_zip is not None:
        report = verify_review_bundle(_read_bounded(args.verify_review_only_zip))
    else:
        entries = []
        for path, old, new in FILES:
            if old is None:
                if _git_exists(BASE_COMMIT, path):
                    raise ValueError("B1 file unexpectedly exists at old commit")
                before = None
            else:
                before = _git_show(BASE_COMMIT, path)
            after = _git_show(REVIEW_COMMIT, path)
            entries.append((path, before, after, old, new))
        bundle, report = build_review_bundle(tuple(entries))
        if args.write_review_only_zip is not None:
            with args.write_review_only_zip.open("xb") as output:
                output.write(bundle)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
