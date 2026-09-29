from __future__ import annotations

import importlib.util
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "scripts" / "deploy" / "phase15_v3_fast_live_build_release.py"


def _module():
    spec = importlib.util.spec_from_file_location("phase15_fast_live_release", BUILDER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fast_live_release_is_secret_free_and_self_contained(tmp_path: Path) -> None:
    module = _module()
    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"
    commit = "a" * 40

    result1 = module.build_release(root=ROOT, output_path=first, commit_sha=commit)
    result2 = module.build_release(root=ROOT, output_path=second, commit_sha=commit)

    assert first.read_bytes() == second.read_bytes()
    assert result1["archive_sha256"] == result2["archive_sha256"]
    assert result1["contains_project_state"] is False
    assert result1["contains_authorization"] is False
    assert result1["contains_secret_files"] is False
    assert result1["production_mutation_performed"] is False
    assert result1["real_order_submitted"] is False

    with tarfile.open(first, "r:gz") as archive:
        names = set(archive.getnames())

    for required in (
        "RELEASE-MANIFEST.json",
        "pyproject.toml",
        "src/bp_engine/__init__.py",
        "src/bp_engine/execution/__init__.py",
        "src/bp_engine/execution/fast_live.py",
        "src/bp_engine/execution/fast_live_book.py",
        "src/bp_engine/execution/fast_live_executor.py",
        "src/bp_engine/execution/fast_live_prepare.py",
        "scripts/run_phase15_v3_fast_live_source.py",
        "scripts/run_phase15_v3_fast_live_receiver.py",
        "scripts/run_phase15_v3_canary_telegram_approval.py",
        "deploy/bp-phase15-fast-live-source.service",
        "deploy/bp-phase15-fast-live-receiver.service",
        "deploy/bp-phase15-canary-telegram-approval.service",
    ):
        assert required in names

    assert "PROJECT_STATE.json" not in names
    assert not any(name.endswith((".env", ".key", ".pem", ".p12", ".pfx")) for name in names)
