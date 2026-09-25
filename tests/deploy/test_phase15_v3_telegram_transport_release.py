from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "scripts" / "deploy" / "phase15_v3_telegram_transport_build_release.py"
VERIFY = ROOT / "scripts" / "deploy" / "phase15_v3_telegram_transport_verify_release.py"


def _run(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(path), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _build(tmp_path: Path, name: str) -> tuple[Path, dict[str, object]]:
    output = tmp_path / name
    completed = _run(BUILD, "--root", str(ROOT), "--output", str(output))
    assert completed.returncode == 0, completed.stderr or completed.stdout
    result = json.loads(completed.stdout)
    assert result["status"] == "transport_release_built"
    assert result["network_action_performed"] is False
    assert result["production_mutation_performed"] is False
    assert result["real_order_submitted"] is False
    return output, result


def _rewrite_archive(
    source: Path,
    output: Path,
    transform,
) -> None:
    raw_tar = gzip.decompress(source.read_bytes())
    entries: list[tuple[tarfile.TarInfo, bytes]] = []
    with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:") as archive:
        for member in archive.getmembers():
            extracted = archive.extractfile(member)
            assert extracted is not None
            copied = tarfile.TarInfo(member.name)
            copied.size = member.size
            copied.mode = member.mode
            copied.uid = member.uid
            copied.gid = member.gid
            copied.uname = member.uname
            copied.gname = member.gname
            copied.mtime = member.mtime
            copied.type = member.type
            copied.linkname = member.linkname
            entries.append((copied, extracted.read()))

    entries = transform(entries)
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for member, data in entries:
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))

    with output.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
            compressed.write(tar_buffer.getvalue())
    output.chmod(0o600)


def test_transport_release_is_deterministic_secret_free_and_verifiable(
    tmp_path: Path,
) -> None:
    first, first_result = _build(tmp_path, "first.tar.gz")
    second, second_result = _build(tmp_path, "second.tar.gz")

    assert first.read_bytes() == second.read_bytes()
    assert first_result["archive_sha256"] == second_result["archive_sha256"]
    assert (os.stat(first).st_mode & 0o777) == 0o600

    verified = _run(
        VERIFY,
        str(first),
        "--expected-commit-sha",
        str(first_result["commit_sha"]),
    )
    assert verified.returncode == 0, verified.stderr or verified.stdout
    result = json.loads(verified.stdout)
    assert result["status"] == "verified"
    assert result["commit_sha"] == first_result["commit_sha"]
    assert result["archive_sha256"] == hashlib.sha256(first.read_bytes()).hexdigest()
    assert result["contains_secret_files"] is False
    assert result["contains_environment_files"] is False
    assert result["contains_key_files"] is False
    assert result["production_mutation_performed"] is False
    assert result["network_action_performed"] is False
    assert result["real_order_submitted"] is False

    with tarfile.open(first, mode="r:gz") as archive:
        names = set(archive.getnames())
        assert "RELEASE-MANIFEST.json" in names
        assert not any(name.endswith((".env", ".key", ".pem", ".p12", ".pfx")) for name in names)
        assert "PROJECT_STATE.json" not in names


def test_transport_release_verifier_rejects_wrong_commit(tmp_path: Path) -> None:
    archive, _ = _build(tmp_path, "release.tar.gz")
    result = _run(
        VERIFY,
        str(archive),
        "--expected-commit-sha",
        "0" * 40,
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed_closed"
    assert "expected commit" in payload["error"]


def test_transport_release_verifier_rejects_changed_member_bytes(tmp_path: Path) -> None:
    archive, _ = _build(tmp_path, "release.tar.gz")
    tampered = tmp_path / "tampered.tar.gz"

    def change(entries):
        result = []
        changed = False
        for member, data in entries:
            if not changed and member.name.endswith(".py"):
                data = data + b"\n# tampered\n"
                changed = True
            result.append((member, data))
        assert changed
        return result

    _rewrite_archive(archive, tampered, change)
    result = _run(VERIFY, str(tampered))
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert "mismatch" in payload["error"]


def test_transport_release_verifier_rejects_extra_secret_member(tmp_path: Path) -> None:
    archive, _ = _build(tmp_path, "release.tar.gz")
    tampered = tmp_path / "extra.tar.gz"

    def add_secret(entries):
        member = tarfile.TarInfo("secrets/transport.key")
        member.mode = 0o644
        member.uid = 0
        member.gid = 0
        member.uname = "root"
        member.gname = "root"
        member.mtime = 0
        entries.append((member, b"not-a-real-key\n"))
        return entries

    _rewrite_archive(archive, tampered, add_secret)
    result = _run(VERIFY, str(tampered))
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert "file set mismatch" in payload["error"]


def test_transport_release_verifier_rejects_link_member(tmp_path: Path) -> None:
    archive, _ = _build(tmp_path, "release.tar.gz")
    tampered = tmp_path / "link.tar.gz"

    def make_link(entries):
        member, _ = entries[0]
        member.type = tarfile.SYMTYPE
        member.linkname = "/etc/passwd"
        return entries

    _rewrite_archive(archive, tampered, make_link)
    result = _run(VERIFY, str(tampered))
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert "member type invalid" in payload["error"]


def test_transport_release_tools_have_no_network_or_deployment_path() -> None:
    for path in (BUILD, VERIFY):
        text = path.read_text(encoding="utf-8")
        compile(text, str(path), "exec")
        for forbidden in (
            "httpx",
            "urllib",
            "requests",
            "google.cloud",
            "gcloud",
            "compute ssh",
            "pubsub topics create",
            "pubsub subscriptions create",
            "set-iam-policy",
            "add-iam-policy-binding",
            "systemctl start",
            "systemctl restart",
            "systemctl enable",
            "post_order",
            "create_limit_order",
            "POLYMARKET_PRIVATE_KEY=",
            "POLYMARKET_WALLET_ADDRESS=",
        ):
            assert forbidden not in text
