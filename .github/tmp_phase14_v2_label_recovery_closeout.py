from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "PROJECT_STATE.json"
MASTER = ROOT / "docs" / "MASTER-SOURCE-OF-TRUTH.md"
BUILD = ROOT / "docs" / "BUILD-ORDER.md"
CHANGELOG = ROOT / "docs" / "CHANGELOG.md"

MAIN = "3e00939f7f70453f9aa054e46c31c72600cdb83d"
FINAL_HEAD = "1f6709632b3a5546bdd158552d8670ca7603bd1b"
CI = 34479665673
HIST = 34479665575
LIVE = 34479665644
SOAK = 34479665747
POST_CI = 34480403106
TESTS = 1059
UPDATED_AT = "2026-09-10T13:10:48Z"


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


state = STATE.read_text(encoding="utf-8")
state = replace_once(
    state,
    '"source_of_truth_version": "0.14.125"',
    '"source_of_truth_version": "0.14.126"',
    label="state version",
)
state = replace_once(
    state,
    '"updated_at": "2026-09-10T12:05:24Z"',
    f'"updated_at": "{UPDATED_AT}"',
    label="state updated_at",
)
state_anchor = '    "v2_gate_b_label_gap_recovery_implementation_test_count": 1055,\n'
state_insert = state_anchor + (
    '    "v2_gate_b_label_gap_recovery_status": "MERGED_MAIN_AWAITING_PRODUCTION_AUDIT_AUTHORIZATION",\n'
    '    "v2_gate_b_label_gap_recovery_pr": 172,\n'
    f'    "v2_gate_b_label_gap_recovery_final_head": "{FINAL_HEAD}",\n'
    f'    "v2_gate_b_label_gap_recovery_pr_ci_run_id": {CI},\n'
    f'    "v2_gate_b_label_gap_recovery_historical_backfill_smoke_run_id": {HIST},\n'
    f'    "v2_gate_b_label_gap_recovery_live_recorder_smoke_run_id": {LIVE},\n'
    f'    "v2_gate_b_label_gap_recovery_recorder_short_soak_run_id": {SOAK},\n'
    '    "v2_gate_b_label_gap_recovery_exact_head_gates_passed": true,\n'
    f'    "v2_gate_b_label_gap_recovery_final_test_count": {TESTS},\n'
    f'    "v2_gate_b_label_gap_recovery_merge_commit": "{MAIN}",\n'
    f'    "v2_gate_b_label_gap_recovery_post_merge_ci_run_id": {POST_CI},\n'
    '    "v2_gate_b_label_gap_recovery_post_merge_ci_passed": true,\n'
    f'    "v2_gate_b_label_gap_recovery_post_merge_test_count": {TESTS},\n'
    '    "v2_gate_b_label_gap_recovery_review_threads_resolved": true,\n'
    '    "v2_gate_b_label_gap_recovery_gamma_receipt_timestamp_after_response": true,\n'
    '    "v2_gate_b_resume_holdout_attempt_marker": "holdout-attempt.json",\n'
    '    "v2_gate_b_resume_holdout_attempt_marker_fsynced_before_evaluation": true,\n'
    '    "v2_gate_b_resume_failure_reports_marker_as_holdout_touched": true,\n'
)
state = replace_once(state, state_anchor, state_insert, label="state recovery fields")
parsed = json.loads(state)
assert parsed["source_of_truth_version"] == "0.14.126"
assert parsed["phase_14_checkpoint"]["v2_gate_b_label_gap_recovery_merge_commit"] == MAIN
assert parsed["phase_14_checkpoint"]["v2_gate_b_label_gap_recovery_post_merge_ci_passed"] is True
assert parsed["phase_14_checkpoint"]["v2_gate_b_label_gap_recovery_production_mutation_authorized"] is False
assert parsed["phase_14_checkpoint"]["v2_gate_b_label_gap_recovery_production_mutation_performed"] is False
assert parsed["phase_14_checkpoint"]["v2_gate_b_resume_authorized"] is False
assert parsed["phase_14_checkpoint"]["v2_gate_b_resume_performed"] is False
STATE.write_text(state, encoding="utf-8")

master = MASTER.read_text(encoding="utf-8")
master = replace_once(
    master,
    "Engineering head `dd659527f70837ab80ac75e5158f9ab063fd78ef` adds a separate holdout-blind label audit/recovery path and a separate same-plan resume path.",
    "PR #172 merged the hardened frozen-plan recovery package to `main` as `3e00939f7f70453f9aa054e46c31c72600cdb83d` from final head `1f6709632b3a5546bdd158552d8670ca7603bd1b` after CI `34479665673`, Historical Backfill Smoke `34479665575`, Live Recorder Smoke `34479665644`, and Recorder Short Soak `34479665747` all passed; post-merge CI `34480403106` then passed 1,059 tests. The package adds a separate holdout-blind label audit/recovery path and a separate same-plan resume path.",
    label="master merge status",
)
master = replace_once(
    master,
    "Resume requires zero missing non-holdout labels, the exact same `plan.json` SHA-256, and its own fresh SHA-bound authorization; it cannot repair or replan and only then may run `prepare -> evaluate-holdout` once.",
    "Resume requires zero missing non-holdout labels, the exact same `plan.json` SHA-256, and its own fresh SHA-bound authorization; it cannot repair or replan and only then may run `prepare -> evaluate-holdout` once. Before that one-shot final-holdout evaluation, the resume helper exclusively creates and fsyncs `holdout-attempt.json`; any later failure reports `HOLDOUT_TOUCHED=true`, and a preexisting attempt marker blocks another evaluation. Recovery provenance now timestamps each successful Gamma response immediately after receipt before storing the canonical snapshot and deriving its label.",
    label="master review fixes",
)
MASTER.write_text(master, encoding="utf-8")

build = BUILD.read_text(encoding="utf-8")
build = replace_once(
    build,
    "Engineering implementation `dd659527f70837ab80ac75e5158f9ab063fd78ef` passed CI `34472169586` with 1,055 tests. No production recovery/resume mutation has been performed.",
    "PR #172 merged the hardened recovery engineering to `main` as `3e00939f7f70453f9aa054e46c31c72600cdb83d` from final head `1f6709632b3a5546bdd158552d8670ca7603bd1b`. All four exact-head gates passed (CI `34479665673`, Historical Backfill Smoke `34479665575`, Live Recorder Smoke `34479665644`, Recorder Short Soak `34479665747`), and post-merge CI `34480403106` passed 1,059 tests. The final helper also persists a durable `holdout-attempt.json` marker before any final-holdout evaluation and records Gamma provenance at response receipt time. No production recovery/resume mutation has been authorized or performed.",
    label="build merge status",
)
BUILD.write_text(build, encoding="utf-8")

changelog = CHANGELOG.read_text(encoding="utf-8")
entry = """## 0.14.126 — 10 September 2026

PR #172 merged the hardened Phase 14 V2 frozen-plan label-gap recovery package to `main` as `3e00939f7f70453f9aa054e46c31c72600cdb83d` from final head `1f6709632b3a5546bdd158552d8670ca7603bd1b`. Final exact-head CI `34479665673` passed **1,059 tests** plus Ruff, dashboard, deployment validation, and research-mode health; Historical Backfill Smoke `34479665575`, Live Recorder Smoke `34479665644`, and Recorder Short Soak `34479665747` also passed. Post-merge main CI `34480403106` then completed successfully with **1,059 tests**.

Pre-merge review found two additional fail-closed requirements and both are now merged with RED→GREEN regression coverage. The same-plan resume helper exclusively creates and fsyncs `holdout-attempt.json` before `evaluate-holdout`, reports `HOLDOUT_TOUCHED=true` if that durable marker or `holdout.json` exists, and rejects any rerun with a preexisting marker. Non-holdout Gamma label recovery now captures provenance immediately after each successful awaited response and uses that receipt time for the preserved snapshot and derived canonical label.

This closeout records engineering integration only. Production label repair remains unauthorized and unperformed; same-plan Gate B resume remains separately unauthorized and unperformed. The original one-shot Gate B authorization remains consumed and non-reusable. No final-holdout label was read by this engineering work. `MODE=research`, `LIVE_TRADING_ENABLED=false`, both money limits remain zero, and `automatic_promotion=false`; V2 acceptance, prospective V2 activation, Phase 15, geographic bypass, live trading, and nonzero money remain blocked.

"""
changelog = replace_once(changelog, "# Changelog\n\n", "# Changelog\n\n" + entry, label="changelog header")
CHANGELOG.write_text(changelog, encoding="utf-8")
