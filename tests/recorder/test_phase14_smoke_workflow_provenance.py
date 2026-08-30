from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
PHASE14_BRANCH = "phase-14-live-readiness-v1"


def _workflow(name: str) -> str:
    path = WORKFLOWS / name
    assert path.exists(), f"missing workflow: {name}"
    return path.read_text(encoding="utf-8")


def test_phase14_operational_smokes_run_from_phase14_candidate() -> None:
    for name in (
        "recorder-smoke.yml",
        "recorder-short-soak.yml",
        "historical-backfill-smoke.yml",
    ):
        workflow = _workflow(name)
        assert PHASE14_BRANCH in workflow, f"{name} does not run for Phase 14"


def test_recorder_smokes_do_not_checkout_stale_phase2_branch() -> None:
    for name in ("recorder-smoke.yml", "recorder-short-soak.yml"):
        workflow = _workflow(name)
        assert "ref: build/phase-2-recorder" not in workflow
