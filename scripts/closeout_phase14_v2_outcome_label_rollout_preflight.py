from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

CANDIDATE = "7c3af78da1922a0e5187c24b799951130cc98887"
FROM_HEAD = "71b33d3beaba4a11ef93e7c5bde1c517323f3440"
PREFLIGHT_MERGE = "f3ef5717390d0dd8d577bdc73143baabd12ff5ce"
HELPER = "scripts/deploy/phase14_v2_outcome_label_coverage_rollout_preflight_cloudshell.sh"

state_path = Path("PROJECT_STATE.json")
state = json.loads(state_path.read_text(encoding="utf-8"))
assert state["source_of_truth_version"] == "0.14.129"
state["source_of_truth_version"] = "0.14.130"
state["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

closeout = state["phase_14_market_price_v2_followup"]["v2_gate_b_consumed_holdout_closeout"]
assert closeout["outcome_label_coverage_fix_production_deployed"] is False
closeout.update(
    {
        "outcome_label_coverage_fix_rollout_candidate_from_head": FROM_HEAD,
        "outcome_label_coverage_fix_rollout_candidate_head": CANDIDATE,
        "outcome_label_coverage_fix_rollout_candidate_verified": True,
        "outcome_label_coverage_fix_rollout_candidate_scope_files": [
            "src/bp_engine/prospective_outcomes/service.py",
            "tests/prospective_outcomes/test_prospective_outcome_sync_service.py",
        ],
        "outcome_label_coverage_fix_rollout_preflight_pr": 182,
        "outcome_label_coverage_fix_rollout_preflight_merge_commit": PREFLIGHT_MERGE,
        "outcome_label_coverage_fix_rollout_preflight_post_merge_ci_run_id": 34594260516,
        "outcome_label_coverage_fix_rollout_preflight_helper": HELPER,
        "outcome_label_coverage_fix_rollout_preflight_read_only": True,
        "outcome_label_coverage_fix_rollout_preflight_performed": False,
    }
)

completed = (
    "PR #182 merged a read-only production preflight for immutable PR #179 rollout candidate "
    f"{CANDIDATE}, rooted at deployed production head {FROM_HEAD}, as {PREFLIGHT_MERGE}; post-merge CI "
    "34594260516 passed. The preflight performs no production mutation and has not been executed from an "
    "authenticated production operator environment; the outcome-label coverage fix remains undeployed."
)
if completed not in state["completed"]:
    state["completed"].append(completed)

assert state["not_completed"][0].startswith("Production rollout and acceptance of PR #179")
state["not_completed"] = [
    "Execution and acceptance of PR #182's merged read-only production preflight for the exact PR #179 rollout candidate",
    "Separately authorized production rollout and acceptance of PR #179's feature-only outcome-label coverage fix after a passing read-only preflight",
    *state["not_completed"][1:],
]
assert state["next_actions"][1].startswith("Before another Gate B attempt")
state["next_actions"] = [
    state["next_actions"][0],
    f"Run {HELPER} from a clean checkout at exact merged main {PREFLIGHT_MERGE} to verify immutable candidate {CANDIDATE} against deployed head {FROM_HEAD}. This is a read-only production preflight and does not authorize or perform the production rollout.",
    "Only after that read-only preflight passes may the PR #179 production rollout be considered under its separate exact authorization boundary. Any rollout must preserve MODE=research, LIVE_TRADING_ENABLED=false, MAX_TRADE_SIZE_USD=0, MAX_DAILY_LOSS_USD=0, and automatic_promotion=false.",
    *state["next_actions"][2:],
]
state_path.write_text(json.dumps(state, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

master_path = Path("docs/MASTER-SOURCE-OF-TRUTH.md")
master = master_path.read_text(encoding="utf-8").rstrip()
heading = "### Phase 14 V2 outcome-label rollout preflight closeout — 11 September 2026"
assert heading not in master
master += f"""

{heading}

PR #179's feature-only outcome-label coverage fix remains source-integrated but undeployed. A production-shaped immutable rollout candidate was built from deployed head `{FROM_HEAD}` at `{CANDIDATE}` with exactly two changed files: `src/bp_engine/prospective_outcomes/service.py` and its regression test. The candidate passed full CI plus historical-source, live-recorder, and short-soak verification.

PR #182 merged the fail-closed read-only production preflight `{HELPER}` as `{PREFLIGHT_MERGE}`; post-merge CI run `34594260516` passed. The preflight binds the exact merged helper, immutable candidate, deployed head, storage evidence, service liveness, and RESEARCH/live-disabled/zero-money/automatic-promotion-false boundaries. It has not been executed from an authenticated production operator environment and does not authorize or perform the production rollout. Production remains on `{FROM_HEAD}`, the PR #179 fix remains undeployed, and a future statistically clean Gate B still requires a new planning epoch and new final holdout after any separately authorized and accepted rollout.
"""
master_path.write_text(master + "\n", encoding="utf-8")

build_path = Path("docs/BUILD-ORDER.md")
build = build_path.read_text(encoding="utf-8").rstrip()
build_heading = "## Phase 14 V2 outcome-label coverage rollout preflight — current order"
assert build_heading not in build
build += f"""

{build_heading}

The verified production-shaped PR #179 rollout candidate is `{CANDIDATE}`, rooted at deployed production head `{FROM_HEAD}` and scoped only to the prospective-outcome runtime fix plus its regression test. PR #182 merged `{HELPER}` as `{PREFLIGHT_MERGE}` and post-merge CI `34594260516` passed.

Current order: (1) run that exact merged helper as a read-only production preflight from a clean checkout at `{PREFLIGHT_MERGE}`; it does not authorize or perform the production rollout; (2) only after preflight PASS, cross the separate exact production-mutation authorization boundary for rollout/acceptance of candidate `{CANDIDATE}`; (3) only after accepted rollout, build a fresh statistically clean feature-only Gate B planning epoch with a new final holdout; (4) preserve RESEARCH, `LIVE_TRADING_ENABLED=false`, zero money limits, `automatic_promotion=false`, and Phase 15/live-trading blocks throughout.
"""
build_path.write_text(build + "\n", encoding="utf-8")

changelog_path = Path("docs/CHANGELOG.md")
changelog = changelog_path.read_text(encoding="utf-8")
assert not changelog.startswith("## 0.14.130")
entry = f"""## 0.14.130 — 11 September 2026

- Froze and verified production-shaped PR #179 rollout candidate `{CANDIDATE}` from deployed head `{FROM_HEAD}` with exactly the prospective-outcome runtime fix and its regression test; full CI, historical-source smoke, live-recorder smoke, and 45-second short soak passed.
- Merged PR #182's fail-closed read-only production preflight as `{PREFLIGHT_MERGE}`; post-merge CI `34594260516` passed. The helper binds exact main/candidate/deployed/storage/safety/service identities but performs no checkout, service restart, Gate B action, holdout access, approval consumption, or other production mutation.
- The read-only production preflight has not been executed from an authenticated production operator environment. The PR #179 fix remains undeployed, production remains on `{FROM_HEAD}`, the consumed Gate B holdout remains non-reusable, and any future Gate B requires a fresh planning epoch/new final holdout after a separately authorized and accepted rollout.
- RESEARCH mode, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, `MAX_DAILY_LOSS_USD=0`, `automatic_promotion=false`, Phase 15 blocked, and live trading disabled remain unchanged.

"""
changelog_path.write_text(entry + changelog, encoding="utf-8")
