from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FROZEN_PLAN_SHA256 = "8f2a756161bb0d85e6020d6ff0d6f4f3540eb28caf133870a4926f65ac7d2fea"
ROLLOUT_CANDIDATE_HEAD = "7c3af78da1922a0e5187c24b799951130cc98887"
ROLLOUT_PREFLIGHT_MERGE = "f3ef5717390d0dd8d577bdc73143baabd12ff5ce"


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


def test_outcome_label_rollout_preflight_is_source_of_truth_but_not_deployment() -> None:
    project_state = _read("PROJECT_STATE.json")
    master = _read("docs/MASTER-SOURCE-OF-TRUTH.md")
    build_order = _read("docs/BUILD-ORDER.md")
    changelog = _read("docs/CHANGELOG.md")

    for content in (project_state, master, build_order, changelog):
        assert ROLLOUT_CANDIDATE_HEAD in content
        assert ROLLOUT_PREFLIGHT_MERGE in content

    assert "phase14_v2_outcome_label_coverage_rollout_preflight_cloudshell.sh" in build_order
    assert "read-only production preflight" in build_order
    assert "does not authorize or perform the production rollout" in build_order

    assert '"outcome_label_coverage_fix_rollout_preflight_read_only": true' in project_state
    assert '"outcome_label_coverage_fix_rollout_preflight_performed": false' in project_state
    assert '"outcome_label_coverage_fix_production_deployed": false' in project_state
    assert '"source_of_truth_version": "0.14.130"' in project_state

    assert not (
        ROOT / ".github/workflows/closeout-phase14-v2-outcome-label-rollout-preflight.yml"
    ).exists()
    assert not (
        ROOT / "scripts/closeout_phase14_v2_outcome_label_rollout_preflight.py"
    ).exists()
