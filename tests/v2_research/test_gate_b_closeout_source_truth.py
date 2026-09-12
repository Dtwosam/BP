from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FROZEN_PLAN_SHA256 = "8f2a756161bb0d85e6020d6ff0d6f4f3540eb28caf133870a4926f65ac7d2fea"
ROLLOUT_CANDIDATE_HEAD = "7c3af78da1922a0e5187c24b799951130cc98887"
ROLLOUT_PREFLIGHT_MERGE = "f3ef5717390d0dd8d577bdc73143baabd12ff5ce"
ROLLOUT_HELPER_HEAD = "17833c64ba7b3836ad4f047167d7d2d861fd2bc0"
ROLLOUT_EVIDENCE = "/var/lib/bp/evidence/phase14-v2-outcome-label-rollout-20260911T124801Z.json"


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


def test_outcome_label_rollout_pass_is_source_of_truth() -> None:
    project_state = _read("PROJECT_STATE.json")
    master = _read("docs/MASTER-SOURCE-OF-TRUTH.md")
    build_order = _read("docs/BUILD-ORDER.md")
    changelog = _read("docs/CHANGELOG.md")

    for content in (project_state, master, build_order, changelog):
        assert ROLLOUT_CANDIDATE_HEAD in content
        assert ROLLOUT_PREFLIGHT_MERGE in content
        assert ROLLOUT_HELPER_HEAD in content
        assert ROLLOUT_EVIDENCE in content

    assert '"outcome_label_coverage_fix_rollout_preflight_read_only": true' in project_state
    assert '"outcome_label_coverage_fix_rollout_preflight_performed": true' in project_state
    assert '"outcome_label_coverage_fix_rollout_preflight_passed": true' in project_state
    assert '"outcome_label_coverage_fix_production_deployed": true' in project_state
    deployed_head = (
        f'"outcome_label_coverage_fix_deployed_head": "{ROLLOUT_CANDIDATE_HEAD}"'
    )
    assert deployed_head in project_state
    rollout_evidence = (
        f'"outcome_label_coverage_fix_rollout_evidence": "{ROLLOUT_EVIDENCE}"'
    )
    assert rollout_evidence in project_state
    assert '"outcome_label_coverage_fix_rollout_holdout_access_performed": false' in project_state
    assert '"outcome_label_coverage_fix_rollout_gate_b_actions_performed": false' in project_state
    assert '"source_of_truth_version": "0.14.134"' in project_state

    assert "outcome-label coverage rollout passed" in master.lower()
    consumed_closeout = master.split(
        "### Phase 14 V2 Gate B consumed final-holdout closeout", 1
    )[1].split("### Phase 14 V2 outcome-label coverage rollout PASS closeout", 1)[0]
    assert "has not been deployed to production" not in consumed_closeout
    assert "Any rollout remains a separate production-mutation boundary" not in consumed_closeout
    assert "outcome-label coverage rollout passed" in build_order.lower()
    assert "fresh statistically clean V2 Gate B plan" in build_order
    assert "Phase 15" in build_order
    assert "live trading" in build_order

    assert "## 0.14.133 — 12 September 2026" in changelog
    assert "## 0.14.132 — 11 September 2026" in changelog
    assert not (
        ROOT / ".github/workflows/closeout-phase14-v2-outcome-label-rollout-preflight.yml"
    ).exists()
    assert not (
        ROOT / ".github/workflows/fix-phase14-closeout-changelog-heading.yml"
    ).exists()
    assert not (
        ROOT / ".github/workflows/fix-phase14-rollout-preflight-current-main-contract.yml"
    ).exists()
    assert not (
        ROOT / "scripts/closeout_phase14_v2_outcome_label_rollout_preflight.py"
    ).exists()
    assert not (
        ROOT / ".github/workflows/closeout-phase14-v2-outcome-label-rollout-pass.yml"
    ).exists()
    assert not (
        ROOT / "scripts/closeout_phase14_v2_outcome_label_rollout_pass.py"
    ).exists()
