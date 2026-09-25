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
RELEASE_PURPOSE = "phase15-v3-telegram-transport-release-v1"

RELEASE_FILES = (
    "deploy/bp-phase15-telegram-pubsub-publisher.service",
    "deploy/bp-phase15-telegram-pubsub-streaming-receiver.service",
    "deploy/bp-phase15-telegram-transport-claim-worker.service",
    "deploy/phase15-telegram-transport-runtime-requirements.txt",
    "scripts/run_phase15_v3_telegram_pubsub_publish_worker.py",
    "scripts/run_phase15_v3_telegram_pubsub_streaming_receive.py",
    "scripts/run_phase15_v3_telegram_transport_claim_worker.py",
    "scripts/run_phase15_v3_telegram_execution_ready_verify.py",
    "scripts/run_phase15_v3_telegram_pre_execution_gate.py",
    "scripts/run_phase15_v3_telegram_dispatch_ticket.py",
    "src/bp_engine/execution/telegram_approval.py",
    "src/bp_engine/execution/telegram_dispatch_ticket.py",
    "src/bp_engine/execution/telegram_execution_ready.py",
    "src/bp_engine/execution/telegram_origin_attestation.py",
    "src/bp_engine/execution/telegram_pre_execution.py",
    "src/bp_engine/execution/telegram_pubsub.py",
    "src/bp_engine/execution/telegram_pubsub_delivery.py",
    "src/bp_engine/execution/telegram_transport.py",
)

FORBIDDEN_SUFFIXES = (".env", ".key", ".pem", ".p12", ".pfx")
FORBIDDEN_BASENAMES = {
    "PROJECT_STATE.json",
    ".env",
    "credentials.json",
    "service-account.json",
}


class ReleaseError(RuntimeError):
    pass


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(payload: dict[str, Any]) -> bytes:
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


def _validate_release_path(root: Path, relative: str) -> tuple[Path, bytes]:
    if relative.startswith("/") or ".." in Path(relative).parts:
        raise ReleaseError(f"release path invalid: {relative}")
    path = root / relative
    try:
        info = path.lstat()
    except OSError as exc:
        raise ReleaseError(f"release file missing: {relative}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ReleaseError(f"release file must be regular non-symlink: {relative}")
    name = path.name
    if name in FORBIDDEN_BASENAMES or name.endswith(FORBIDDEN_SUFFIXES):
        raise ReleaseError(f"secret-bearing file type forbidden: {relative}")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ReleaseError(f"release file unreadable: {relative}") from exc
    if not data:
        raise ReleaseError(f"release file empty: {relative}")
    return path, data


def _manifest(root: Path, *, commit_sha: str) -> tuple[dict[str, Any], dict[str, bytes]]:
    if len(commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in commit_sha):
        raise ReleaseError("commit SHA must be 40 lowercase hex characters")
    files: dict[str, bytes] = {}
    entries: list[dict[str, Any]] = []
    for relative in RELEASE_FILES:
        _, data = _validate_release_path(root, relative)
        files[relative] = data
        entries.append(
            {
                "path": relative,
                "sha256": _sha256_bytes(data),
                "size_bytes": len(data),
            }
        )
    manifest = {
        "schema_version": RELEASE_SCHEMA_VERSION,
        "purpose": RELEASE_PURPOSE,
        "commit_sha": commit_sha,
        "file_count": len(entries),
        "files": entries,
        "contains_secret_files": False,
        "contains_environment_files": False,
        "contains_key_files": False,
        "production_mutation_performed": False,
        "real_order_submitted": False,
    }
    manifest["manifest_sha256"] = _sha256_bytes(_canonical_json(manifest))
    return manifest, files


def build_release(
    *,
    root: Path,
    output_path: Path,
    commit_sha: str,
) -> dict[str, Any]:
    try:
        root_info = root.lstat()
    except OSError as exc:
        raise ReleaseError("repository root is not accessible") from exc
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise ReleaseError("repository root must be a non-symlink directory")

    manifest, files = _manifest(root, commit_sha=commit_sha)
    manifest_bytes = _canonical_json(manifest)

    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
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

    output_parent = output_path.parent
    output_parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() or output_path.is_symlink():
        raise ReleaseError("release output already exists")
    temporary = output_path.with_name(f".{output_path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
                compressed.write(tar_buffer.getvalue())
            raw.flush()
            os.fsync(raw.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, output_path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

    archive_sha256 = _sha256_bytes(output_path.read_bytes())
    return {
        **manifest,
        "archive_path": str(output_path),
        "archive_sha256": archive_sha256,
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
    value = completed.stdout.strip()
    if len(value) != 40:
        raise ReleaseError("git HEAD invalid")
    return value


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
            "Build a deterministic, secret-free BP Phase 15 Telegram transport release. "
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

    print(
        json.dumps(
            {
                "status": "transport_release_built",
                **result,
                "network_action_performed": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
