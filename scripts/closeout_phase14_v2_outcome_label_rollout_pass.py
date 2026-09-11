from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "PROJECT_STATE.json"
MASTER_PATH = ROOT / "docs/MASTER-SOURCE-OF-TRUTH.md"
BUILD_PATH = ROOT / "docs/BUILD-ORDER.md"
CHANGELOG_PATH = ROOT / "docs/CHANGELOG.md"
CONTRACT_PATH = ROOT / "tests/v2_research/test_gate_b_contract.py"

FROM_HEAD = "71b33d3beaba4a11ef93e7c5bde1c517323f3440"
CANDIDATE_HEAD = "7c3af78da1922a0e5187c24b799951130cc98887"
PREFLIGHT_MERGE = "f3ef5717390d0dd8d577bdc73143baabd12ff5ce"
HELPER_HEAD = "17833c64ba7b3836ad4f047167d7d2d861fd2bc0"
RUNTIME_BLOB = "00e15c067aa4d47176b530c3150f2da9400f5207"
TEST_BLOB = "59800179c88743a65f5f26483c7af82fa9aced88"
EVIDENCE = "/var/lib/bp/evidence/phase14-v2-outcome-label-rollout-20260911T124801Z.json"
ROLLOUT_AT = "2026-09-11T12:48:01Z"


def replace_section(text: str, heading: str, next_prefix: str, replacement: str) -> str:
    start = text.index(heading)
    next_start = text.find(f"\n{next_prefix}", start + len(heading))
    if next_start == -1:
        return text[:start] + replacement.rstrip() + "\n"
    return text[:start] + replacement.rstrip() + "\n" + text[next_start:]


state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
assert state["source_of_truth_version"] == "0.14.131"
state["source_of_truth_version"] = "0.14.132"
state["updated_at"] = "2026-09-11T12:56:00Z"

completed = state["completed"]
completed.append(
    "Phase 14 V2 outcome-label coverage rollout passed in production at "
    f"{ROLLOUT_AT} using helper/main {HELPER_HEAD}: deployed exact immutable candidate "
    f"{CANDIDATE_HEAD} from {FROM_HEAD}, restarted only bp-prospective-outcomes.service, "
    "kept unrelated long-running service PIDs unchanged, returned storage status ok, "
    "preserved research/live-disabled/zero-money/automatic-promotion-false safety, "
    f"performed no holdout or Gate B action, and wrote durable evidence {EVIDENCE}."
)

state["not_completed"] = [
    item
    for item in state["not_completed"]
    if "Execution and acceptance of PR #182's merged read-only production preflight" not in item
    and "Separately authorized production rollout and acceptance of PR #179's feature-only outcome-label coverage fix" not in item
]

state["next_actions"] = [
    f"Preserve {EVIDENCE} as immutable rollout evidence and do not rerun the consumed Phase 14 V2 outcome-label coverage rollout. Production is accepted at exact candidate {CANDIDATE_HEAD} for this research-only fix.",
    "Build future Gate B evidence from a fresh feature-only planning epoch and a new final holdout. Planning/readiness must remain label-blind, and the consumed frozen plan/final holdout must never be reused.",
    "Treat access to the new final holdout as a separate one-shot explicit authorization boundary; do not infer authorization from the completed outcome-label coverage rollout.",
    "Keep Gate B unaccepted and do not activate a V2 policy/model/calibration/edge configuration, Phase 15, geographic bypass, live trading, automatic promotion, or nonzero money limits unless a new statistically clean Gate B run and all other live gates pass under their separate authorization boundaries.",
]

closeout = state["phase_14_market_price_v2_followup"]["v2_gate_b_consumed_holdout_closeout"]
closeout.update(
    {
        "outcome_label_coverage_fix_rollout_preflight_performed": True,
        "outcome_label_coverage_fix_rollout_preflight_passed": True,
        "outcome_label_coverage_fix_rollout_preflight_helper_head": HELPER_HEAD,
        "outcome_label_coverage_fix_rollout_preflight_observed_deployed_head": FROM_HEAD,
        "outcome_label_coverage_fix_production_deployed": True,
        "outcome_label_coverage_fix_deployed_head": CANDIDATE_HEAD,
        "outcome_label_coverage_fix_rollout_evidence": EVIDENCE,
        "outcome_label_coverage_fix_rollout_passed_at": ROLLOUT_AT,
        "outcome_label_coverage_fix_rollout_runtime_blob": RUNTIME_BLOB,
        "outcome_label_coverage_fix_rollout_test_blob": TEST_BLOB,
        "outcome_label_coverage_fix_rollout_outcome_service_pid_before": 3889510,
        "outcome_label_coverage_fix_rollout_outcome_service_pid_after": 3985114,
        "outcome_label_coverage_fix_rollout_unrelated_service_pids_unchanged": True,
        "outcome_label_coverage_fix_rollout_storage_status": "ok",
        "outcome_label_coverage_fix_rollout_production_mutations_performed": True,
        "outcome_label_coverage_fix_rollout_holdout_access_performed": False,
        "outcome_label_coverage_fix_rollout_gate_b_actions_performed": False,
    }
)
assert closeout["automatic_promotion"] is False
assert closeout["phase15_permitted"] is False
assert closeout["live_trading_enabled"] is False
assert closeout["max_trade_size_usd"] == 0
assert closeout["max_daily_loss_usd"] == 0
STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

master = MASTER_PATH.read_text(encoding="utf-8")
master_heading = "### Phase 14 V2 outcome-label rollout preflight closeout — 11 September 2026"
master_replacement = f"""### Phase 14 V2 outcome-label coverage rollout PASS closeout — 11 September 2026

The Phase 14 V2 outcome-label coverage rollout passed in production at `{ROLLOUT_AT}`. PR #182 merge `{PREFLIGHT_MERGE}` remains the provenance of the read-only preflight implementation, and the passing preflight ran from exact helper/main `{HELPER_HEAD}` against previously deployed `{FROM_HEAD}`. The separately authorized rollout then moved `/opt/bp` to exact immutable candidate `{CANDIDATE_HEAD}`, whose runtime blob is `{RUNTIME_BLOB}` and regression-test blob is `{TEST_BLOB}`.

Only `bp-prospective-outcomes.service` was restarted; its PID changed from `3889510` to `3985114`. Unrelated long-running service PIDs remained unchanged, composite storage status remained `ok`, and the effective safety boundary remained `MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, `MAX_DAILY_LOSS_USD=0`, and `automatic_promotion=false`. The rollout performed no holdout access and no Gate B action. Durable production evidence is `{EVIDENCE}`.

This rollout closes only the future outcome-label coverage gap. Gate B remains unaccepted. Frozen plan SHA-256 `8f2a756161bb0d85e6020d6ff0d6f4f3540eb28caf133870a4926f65ac7d2fea` and its consumed final holdout must never be reused. Any future Gate B attempt requires a fresh statistically clean, feature-only planning epoch and a new final holdout; access to that new final holdout remains a separate one-shot explicit authorization boundary. Phase 15, live trading, automatic promotion, geographic bypass, and nonzero money remain blocked.
"""
master = replace_section(master, master_heading, "### ", master_replacement)
MASTER_PATH.write_text(master, encoding="utf-8")

build = BUILD_PATH.read_text(encoding="utf-8")
build_heading = "## Phase 14 V2 outcome-label coverage rollout preflight — current order"
build_replacement = f"""## Phase 14 V2 outcome-label coverage rollout PASS — current order

The Phase 14 V2 outcome-label coverage rollout passed in production at `{ROLLOUT_AT}`. The immutable production-shaped candidate `{CANDIDATE_HEAD}` was rooted at `{FROM_HEAD}` and remained scoped to the prospective-outcome runtime fix plus its regression test. PR #182 merge `{PREFLIGHT_MERGE}` is historical preflight-helper provenance; the passing read-only preflight and authorized rollout were bound to exact helper/main `{HELPER_HEAD}`. Durable rollout evidence is `{EVIDENCE}`.

Current order: (1) do not rerun this rollout and preserve its evidence; (2) build a fresh statistically clean V2 Gate B plan from a new feature-only, label-blind planning epoch and reserve a new final holdout; (3) require a separate one-shot explicit authorization before any access to that new final holdout; (4) keep Gate B unaccepted and preserve `MODE=research`, `LIVE_TRADING_ENABLED=false`, zero money limits, `automatic_promotion=false`, Phase 15 blocked, and live trading blocked until a new clean Gate B run and every other live gate pass. The consumed frozen Gate B plan/final holdout must never be reused.
"""
build = replace_section(build, build_heading, "## ", build_replacement)
BUILD_PATH.write_text(build, encoding="utf-8")

changelog = CHANGELOG_PATH.read_text(encoding="utf-8")
assert changelog.startswith("# Changelog\n\n## 0.14.131 — 11 September 2026")
entry = f"""## 0.14.132 — 11 September 2026

- Recorded the passing production rollout of the Phase 14 V2 outcome-label coverage fix: exact helper/main `{HELPER_HEAD}` moved production from `{FROM_HEAD}` to immutable candidate `{CANDIDATE_HEAD}` after the PR #182 read-only preflight (`{PREFLIGHT_MERGE}` provenance) passed.
- Only `bp-prospective-outcomes.service` restarted (PID `3889510` -> `3985114`); unrelated long-running service PIDs were unchanged, storage remained `ok`, and research/live-disabled/zero-money/automatic-promotion-false safety remained intact.
- The rollout performed no holdout access and no Gate B action. Durable evidence is `{EVIDENCE}`. Gate B remains unaccepted; the consumed frozen plan/final holdout must not be reused, and any new final holdout remains a separate one-shot authorization boundary.

"""
changelog = "# Changelog\n\n" + entry + changelog[len("# Changelog\n\n"):]
CHANGELOG_PATH.write_text(changelog, encoding="utf-8")

contract = CONTRACT_PATH.read_text(encoding="utf-8")
old = '    assert closeout["outcome_label_coverage_fix_production_deployed"] is False\n'
assert old in contract
new = f'''    assert closeout["outcome_label_coverage_fix_production_deployed"] is True
    assert closeout["outcome_label_coverage_fix_deployed_head"] == "{CANDIDATE_HEAD}"
    assert closeout["outcome_label_coverage_fix_rollout_evidence"] == (
        "{EVIDENCE}"
    )
    assert closeout["outcome_label_coverage_fix_rollout_preflight_performed"] is True
    assert closeout["outcome_label_coverage_fix_rollout_preflight_passed"] is True
    assert closeout["outcome_label_coverage_fix_rollout_holdout_access_performed"] is False
    assert closeout["outcome_label_coverage_fix_rollout_gate_b_actions_performed"] is False
'''
contract = contract.replace(old, new, 1)
CONTRACT_PATH.write_text(contract, encoding="utf-8")
