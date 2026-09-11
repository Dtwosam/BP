from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FROZEN_PLAN_SHA256 = "8f2a756161bb0d85e6020d6ff0d6f4f3540eb28caf133870a4926f65ac7d2fea"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_consumed_gate_b_holdout_supersedes_same_plan_resume_instructions() -> None:
    master = _read("docs/MASTER-SOURCE-OF-TRUTH.md")
    build_order = _read("docs/BUILD-ORDER.md")
    decisions = _read("docs/DECISION-LOG.md")

    assert "Phase 14 V2 Gate B consumed final-holdout closeout" in master
    assert FROZEN_PLAN_SHA256 in master
    assert "must not be reused" in master

    assert "fresh statistically clean V2 Gate B plan" in build_order
    stale_resume_instruction = (
        "resume only the same frozen plan through `prepare -> evaluate-holdout`"
    )
    assert stale_resume_instruction not in build_order

    d040 = decisions.split("## D-040", 1)[1].split("## D-041", 1)[0]
    assert "**Status:** Superseded by D-041" in d040
    d041 = decisions.split("## D-041", 1)[1]
    assert "**Status:** Active" in d041
    assert FROZEN_PLAN_SHA256 in d041
    assert "must not be reused" in d041
