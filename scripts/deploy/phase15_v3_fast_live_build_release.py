from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import stat
import subprocess
import tarfile
from pathlib import Path
from typing import Any

RELEASE_SCHEMA_VERSION = 1
RELEASE_PURPOSE = "phase15-v3-fast-live-release-v1"
STATIC_FILES = (
    "pyproject.toml",
    "deploy/bp-phase15-fast-live-source.service",
    "deploy/bp-phase15-fast-live-receiver.service",
    "deploy/phase15-fast-live-executor-requirements.txt",
    "scripts/run_phase15_v3_fast_live_source.py",
    "scripts/run_phase15_v3_fast_live_receiver.py",
)
SOURCE_ROOT = "src/bp_engine"
FORBIDDEN_SUFFIXES = (".env", ".key", ".pem", ".p12", ".pfx")
FORBIDDEN_BASENAMES = {
    "PROJECT_STATE.json",
    ".env",
    "credentials.json",
    "service-account.json",
}


class ReleaseError(RuntimeError):
    pass


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode()


def _validate_path(root: Path, relative: str) -> bytes:
    if relative.startswith("/") or ".." in Path(relative).parts:
        raise ReleaseError(f"release path invalid: {relative}")
    path = root / relative
    try:
        info = path.lstat()
    except OSError as exc:
        raise ReleaseError(f"release file missing: {relative}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ReleaseError(f"release file must be regular non-symlink: {relative}")
    if path.name in FORBIDDEN_BASENAMES or path.name.endswith(FORBIDDEN_SUFFIXES):
        raise ReleaseError(f"secret-bearing file forbidden: {relative}")
    data = path.read_bytes()
    if not data:
        raise ReleaseError(f"release file empty: {relative}")
    return data


def release_files(root: Path) -> tuple[str, ...]:
    source = root / SOURCE_ROOT
    try:
        info = source.lstat()
    except OSError as exc:
        raise ReleaseError("bp_engine source tree missing") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ReleaseError("bp_engine source tree invalid")
    dynamic = tuple(
        str(path.relative_to(root))
        for path in sorted(source.rglob("*.py"))
        if path.is_file() and not path.is_symlink()
    )
    if not dynamic:
        raise ReleaseError("bp_engine source tree contains no Python files")
    return tuple(sorted((*STATIC_FILES, *dynamic)))


def build_release(
    *,
    root: Path,
    output_path: Path,
    commit_sha: str,
) -> dict[str, Any]:
    if len(commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in commit_sha):
        raise ReleaseError("commit SHA must be 40 lowercase hex characters")
    paths = release_files(root)
    files: dict[str, bytes] = {}
    entries: list[dict[str, Any]] = []
    for relative in paths:
        data = _validate_path(root, relative)
        files[relative] = data
        entries.append(
            {
                "path": relative,
                "sha256": _sha256(data),
                "size_bytes": len(data),
            }
        )

    manifest = {
        "schema_version": RELEASE_SCHEMA_VERSION,
        "purpose": RELEASE_PURPOSE,
        "commit_sha": commit_sha,
        "file_count": len(entries),
        "files": entries,
        "contains_project_state": False,
        "contains_authorization": False,
        "contains_secret_files": False,
        "production_mutation_performed": False,
        "real_order_submitted": False,
    }
    manifest["manifest_sha256"] = _sha256(_canonical(manifest))
    manifest_bytes = _canonical(manifest)

    if output_path.exists() or output_path.is_symlink():
        raise ReleaseError("release output already exists")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = output_path.with_name(f".{output_path.name}.{os.getpid()}.tmp")
    try:
        tar_buffer = io.BytesIO()
        with tarfile.open(
            fileobj=tar_buffer,
            mode="w",
            format=tarfile.PAX_FORMAT,
        ) as archive:
            for relative in sorted(files):
                data = files[relative]
                info = tarfile.TarInfo(relative)
                info.size = len(data)
                info.mode = 0o644
                info.uid = 0
                info.gid = 0
                info.uname = "root"
                info.gname = "root"
                info.mtime = 0
                archive.addfile(info, io.BytesIO(data))
            info = tarfile.TarInfo("RELEASE-MANIFEST.json")
            info.size = len(manifest_bytes)
            info.mode = 0o644
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = 0
            archive.addfile(info, io.BytesIO(manifest_bytes))

        with temp.open("xb") as raw:
            with gzip.GzipFile(
                fileobj=raw,
                mode="wb",
                filename="",
                mtime=0,
            ) as compressed:
                compressed.write(tar_buffer.getvalue())
            raw.flush()
            os.fsync(raw.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, output_path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass

    return {
        **manifest,
        "archive_path": str(output_path),
        "archive_sha256": _sha256(output_path.read_bytes()),
        "archive_mode": "0600",
    }


def _git_head(root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ReleaseError("unable to resolve git HEAD")
    return completed.stdout.strip()


def _require_clean_tree(root: Path) -> None:
    completed = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ReleaseError("unable to inspect git working tree")
    if completed.stdout.strip():
        raise ReleaseError("git working tree must be clean")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a deterministic secret-free Phase 15 fast-live release. "
            "This command performs no deployment or network action."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        _require_clean_tree(args.root)
        result = build_release(
            root=args.root,
            output_path=args.output,
            commit_sha=_git_head(args.root),
        )
    except ReleaseError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
