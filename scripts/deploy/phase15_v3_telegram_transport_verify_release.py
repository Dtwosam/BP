from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import stat
import tarfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from phase15_v3_telegram_transport_build_release import (
    FORBIDDEN_BASENAMES,
    FORBIDDEN_SUFFIXES,
    RELEASE_FILES,
    RELEASE_PURPOSE,
    RELEASE_SCHEMA_VERSION,
    ReleaseError,
)

MAX_ARCHIVE_BYTES = 4 * 1024 * 1024
MAX_MEMBER_BYTES = 512 * 1024


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_archive(path: Path) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ReleaseError("release archive is not readable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ReleaseError("release archive must be a regular non-symlink file")
    if stat.S_IMODE(info.st_mode) not in (0o600, 0o640):
        raise ReleaseError("release archive mode must be 0600 or 0640")
    if info.st_size <= 0 or info.st_size > MAX_ARCHIVE_BYTES:
        raise ReleaseError("release archive size invalid")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ReleaseError("release archive is not readable") from exc


def _extract_members(archive_bytes: bytes) -> dict[str, bytes]:
    try:
        raw_tar = gzip.decompress(archive_bytes)
    except (OSError, EOFError) as exc:
        raise ReleaseError("release archive gzip invalid") from exc

    result: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:") as archive:
            members = archive.getmembers()
            expected = set(RELEASE_FILES) | {"RELEASE-MANIFEST.json"}
            names = [member.name for member in members]
            if len(names) != len(set(names)):
                raise ReleaseError("release archive contains duplicate paths")
            if set(names) != expected:
                raise ReleaseError("release archive file set mismatch")

            for member in members:
                if (
                    not member.isfile()
                    or member.issym()
                    or member.islnk()
                    or member.name.startswith("/")
                    or ".." in Path(member.name).parts
                ):
                    raise ReleaseError(
                        f"release archive member type invalid: {member.name}"
                    )
                if member.size <= 0 or member.size > MAX_MEMBER_BYTES:
                    raise ReleaseError(
                        f"release archive member size invalid: {member.name}"
                    )
                if member.mode != 0o644:
                    raise ReleaseError(
                        f"release archive member mode invalid: {member.name}"
                    )
                if (
                    member.uid != 0
                    or member.gid != 0
                    or member.uname != "root"
                    or member.gname != "root"
                    or member.mtime != 0
                ):
                    raise ReleaseError(
                        f"release archive member metadata invalid: {member.name}"
                    )
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ReleaseError(
                        f"release archive member unreadable: {member.name}"
                    )
                result[member.name] = extracted.read()
    except (tarfile.TarError, OSError) as exc:
        raise ReleaseError("release archive tar invalid") from exc
    return result


def verify_release(
    *,
    archive_path: Path,
    expected_commit_sha: str | None = None,
) -> dict[str, Any]:
    archive_bytes = _read_archive(archive_path)
    members = _extract_members(archive_bytes)
    try:
        manifest = json.loads(members["RELEASE-MANIFEST.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseError("release manifest JSON invalid") from exc
    if not isinstance(manifest, dict):
        raise ReleaseError("release manifest must contain a JSON object")

    supplied_manifest_sha = str(manifest.get("manifest_sha256") or "")
    manifest_body = dict(manifest)
    manifest_body.pop("manifest_sha256", None)
    if _sha256(_canonical_json(manifest_body)) != supplied_manifest_sha:
        raise ReleaseError("release manifest hash mismatch")

    if manifest.get("schema_version") != RELEASE_SCHEMA_VERSION:
        raise ReleaseError("release manifest schema mismatch")
    if manifest.get("purpose") != RELEASE_PURPOSE:
        raise ReleaseError("release manifest purpose mismatch")
    commit_sha = str(manifest.get("commit_sha") or "")
    if (
        len(commit_sha) != 40
        or any(ch not in "0123456789abcdef" for ch in commit_sha)
    ):
        raise ReleaseError("release manifest commit SHA invalid")
    if expected_commit_sha is not None and commit_sha != expected_commit_sha:
        raise ReleaseError("release commit does not match expected commit")

    for name in (
        "contains_secret_files",
        "contains_environment_files",
        "contains_key_files",
        "production_mutation_performed",
        "real_order_submitted",
    ):
        if manifest.get(name) is not False:
            raise ReleaseError(f"release manifest {name} must be false")

    entries = manifest.get("files")
    if not isinstance(entries, list) or len(entries) != len(RELEASE_FILES):
        raise ReleaseError("release manifest file list invalid")
    if manifest.get("file_count") != len(RELEASE_FILES):
        raise ReleaseError("release manifest file count mismatch")

    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size_bytes"}:
            raise ReleaseError("release manifest file entry invalid")
        relative = str(entry["path"])
        if relative in seen:
            raise ReleaseError("release manifest contains duplicate path")
        seen.add(relative)
        if relative not in RELEASE_FILES:
            raise ReleaseError("release manifest contains unexpected path")
        name = Path(relative).name
        if name in FORBIDDEN_BASENAMES or name.endswith(FORBIDDEN_SUFFIXES):
            raise ReleaseError("release manifest contains secret-bearing file type")
        data = members[relative]
        if entry["size_bytes"] != len(data):
            raise ReleaseError(f"release file size mismatch: {relative}")
        if str(entry["sha256"]) != _sha256(data):
            raise ReleaseError(f"release file hash mismatch: {relative}")

    if seen != set(RELEASE_FILES):
        raise ReleaseError("release manifest required file set mismatch")

    return {
        "schema_version": RELEASE_SCHEMA_VERSION,
        "purpose": RELEASE_PURPOSE,
        "status": "verified",
        "commit_sha": commit_sha,
        "manifest_sha256": supplied_manifest_sha,
        "archive_sha256": _sha256(archive_bytes),
        "file_count": len(RELEASE_FILES),
        "contains_secret_files": False,
        "contains_environment_files": False,
        "contains_key_files": False,
        "production_mutation_performed": False,
        "network_action_performed": False,
        "real_order_submitted": False,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a BP Phase 15 Telegram transport release without extracting "
            "or deploying it."
        )
    )
    parser.add_argument("archive", type=Path)
    parser.add_argument("--expected-commit-sha")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        result = verify_release(
            archive_path=args.archive,
            expected_commit_sha=args.expected_commit_sha,
        )
    except ReleaseError as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error": str(exc),
                    "production_mutation_performed": False,
                    "network_action_performed": False,
                    "real_order_submitted": False,
                },
                sort_keys=True,
            )
        )
        return 1

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
