#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "PROJECT_STATE.json"
BUILD = ROOT / "docs" / "BUILD-ORDER.md"
CHANGELOG = ROOT / "docs" / "CHANGELOG.md"
MASTER = ROOT / "docs" / "MASTER-SOURCE-OF-TRUTH.md"
DECISIONS = ROOT / "docs" / "DECISION-LOG.md"

VERSION = "0.14.123"
CHECKED_AT = "2026-09-10T09:07:50Z"
HELPER_HEAD = "26e91498672618b5cafb7f2d901994103c5554b8"
DEPLOYED_HEAD = "e9c7afc1536880e4612cb6e3d1a7282fa37c69f5"
EVIDENCE = "docs/evidence/phase-14-v2-gate-b-readiness-20260910T090750Z.json"
TRANSCRIPT_SHA256 = "6e56d007833c75c989350ce05f86ed81fb4225093a5de29700b71d2d6672192f"
WOULD_PLAN_SHA256 = "a1c2dff984a1dc6a2720961781af83f0d1999fbb632e33d9932c5582e688d5e2"


def find_readiness_checkpoint(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        if "v2_gate_b_readiness_status" in value:
            return value
        for item in value.values():
            found = find_readiness_checkpoint(item)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_readiness_checkpoint(item)
            if found is not None:
                return found
    return None


def update_state() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    if state.get("source_of_truth_version") != "0.14.122":
        raise SystemExit("unexpected source_of_truth_version")
    if state.get("trading_mode") != "RESEARCH" or state.get("live_trading_enabled") is not False:
        raise SystemExit("research/live boundary changed")

    checkpoint = find_readiness_checkpoint(state)
    if checkpoint is None:
        raise SystemExit("V2 readiness checkpoint not found")
    if checkpoint.get("v2_gate_b_research_gate_b_authorized") is not False:
        raise SystemExit("Gate B authorization boundary changed")
    if checkpoint.get("v2_gate_b_readiness_holdout_touched") is not False:
        raise SystemExit("holdout boundary changed")

    state["source_of_truth_version"] = VERSION
    state["updated_at"] = CHECKED_AT

    completion = (
        "Fresh feature-only Phase 14 V2 Gate B readiness passed at "
        "2026-09-10T09:07:50Z on exact helper/main head "
        f"{HELPER_HEAD} against deployed head {DEPLOYED_HEAD}: READY=true, "
        "651 markets, analysis start 2026-09-09T10:20:00Z, 5 eligible folds, "
        "24 feature-only final-holdout markets, no labels read, no Gate B "
        "artifacts written, and HOLDOUT_TOUCHED=false"
    )
    if completion not in state["completed"]:
        state["completed"].append(completion)

    state["next_actions"] = [
        "Feature-only V2 Gate B readiness prerequisite is now satisfied: the accepted 2026-09-10T09:07:50Z exact-main check returned READY=true with 651 markets, analysis start 2026-09-09T10:20:00Z, 5 eligible folds, and 24 feature-only final-holdout markets. Stop readiness retries. Gate B itself is the next explicit authorization boundary and must not run without separate authorization; the final holdout remains unread/untouched.",
        "Keep docs/evidence/phase-14-v2-freshness-preregistration-20260909.json immutable: max_last_trade_age_seconds candidates remain exactly [1, 2, 5, 10] plus explicit no_trade and coverage_input_sha256 aab75574aa7faf18e65358353403e5ec1a2b89dd42424eb7b0e3329bf683b099. Do not alter the grid, frozen geometry, fee/slippage assumptions, eligible-market minimums, or min-edge grid after readiness acceptance.",
        "Do not treat READY=true as Gate B authorization. Until separate Gate B authorization exists, do not read Gate B labels/outcomes, write/freeze a Gate B plan or selection artifact, evaluate the final holdout, accept a V2 policy/model/calibration/edge/min-edge selection, or activate automatic promotion.",
        "Continue normal money-disabled prospective evidence collection under RESEARCH/live-disabled/zero-money safety. The optional two-hour readiness watcher remains merged engineering but is not production-installed and no longer needs installation for the current Gate B path.",
        "Phase 15, geographic bypass, live trading, and nonzero money remain separately blocked regardless of Gate B readiness."
    ]

    checkpoint.update(
        {
            "v2_gate_b_readiness_status": "PRODUCTION_PASS_READY_TRUE_GATE_B_UNAUTHORIZED",
            "v2_gate_b_readiness_production_check_performed": True,
            "v2_gate_b_readiness_last_checked_at": CHECKED_AT,
            "v2_gate_b_readiness_last_helper_head": HELPER_HEAD,
            "v2_gate_b_readiness_last_deployed_head": DEPLOYED_HEAD,
            "v2_gate_b_readiness_last_result": True,
            "v2_gate_b_readiness_last_market_count": 651,
            "v2_gate_b_readiness_last_market_start_at": "2026-09-02T12:20:00+00:00",
            "v2_gate_b_readiness_last_market_end_at": "2026-09-10T09:05:00+00:00",
            "v2_gate_b_readiness_last_available_span_hours": 188.75,
            "v2_gate_b_readiness_last_analysis_start_attempt_count": 84,
            "v2_gate_b_readiness_last_candidate_rejection_count": 83,
            "v2_gate_b_readiness_last_candidate_rejection_stage_counts": {
                "test": 14,
                "validation": 5,
                "train": 64,
            },
            "v2_gate_b_readiness_last_blocking_reason": None,
            "v2_gate_b_readiness_last_candidate_start": "2026-09-09T10:20:00+00:00",
            "v2_gate_b_readiness_last_analysis_start_at": "2026-09-09T10:20:00+00:00",
            "v2_gate_b_readiness_last_eligible_fold_count": 5,
            "v2_gate_b_readiness_last_final_holdout_market_count": 24,
            "v2_gate_b_readiness_last_labels_read": False,
            "v2_gate_b_readiness_last_plan_artifact_written": False,
            "v2_gate_b_readiness_last_selection_artifact_written": False,
            "v2_gate_b_readiness_holdout_touched": False,
            "v2_gate_b_readiness_last_would_plan_sha256": WOULD_PLAN_SHA256,
            "v2_gate_b_readiness_last_candidate_archive_sha256": "25cc1410a227a633cb653fb47af38fae74f305f6d973a39aa6d3030a6726744e",
            "v2_gate_b_readiness_last_transcript_sha256": TRANSCRIPT_SHA256,
            "v2_gate_b_readiness_last_sanitized_evidence": EVIDENCE,
            "v2_gate_b_readiness_feature_only_prerequisite_satisfied": True,
            "v2_gate_b_readiness_retry_required": False,
            "v2_gate_b_research_gate_b_authorized": False,
            "v2_gate_b_research_automatic_promotion": False,
            "v2_gate_b_readiness_watch_production_install_authorized": False,
            "v2_gate_b_readiness_watch_production_install_performed": False,
        }
    )
    checkpoint.pop("v2_gate_b_readiness_last_candidate_train_market_count", None)

    STATE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def replace_immediate_action() -> None:
    text = BUILD.read_text(encoding="utf-8")
    start = text.index("## Immediate next action")
    end = text.index("\n\nProduction preflight evidence", start)
    replacement = """## Immediate next action

**The Phase 14 V2 feature-only Gate B readiness prerequisite is satisfied. The fresh exact-main production check completed at `2026-09-10T09:07:50Z` on helper head `26e91498672618b5cafb7f2d901994103c5554b8` against deployed head `e9c7afc1536880e4612cb6e3d1a7282fa37c69f5` and returned `READY=true`: 651 V2 markets, accepted analysis start `2026-09-09T10:20:00Z`, 5 eligible folds, and 24 feature-only final-holdout markets. It read no labels, wrote no plan/selection/holdout artifact, and preserved `HOLDOUT_TOUCHED=false`. Stop readiness retries. Gate B itself is now the next explicit authorization boundary; `READY=true` is not authorization. Until separate Gate B authorization is granted, do not run prepare/evaluate-holdout, join Gate B labels/outcomes, freeze plan/selection artifacts, inspect the final holdout, accept a V2 policy/model/calibration/edge/min-edge choice, or activate automatic promotion. The optional readiness watcher remains uninstalled and does not need production installation for the current path. Research/live-disabled/zero-money controls remain unchanged. Sanitized evidence: `docs/evidence/phase-14-v2-gate-b-readiness-20260910T090750Z.json`.**"""
    BUILD.write_text(text[:start] + replacement + text[end:], encoding="utf-8")


def prepend_changelog() -> None:
    text = CHANGELOG.read_text(encoding="utf-8")
    marker = "# Changelog\n\n"
    if not text.startswith(marker):
        raise SystemExit("unexpected changelog header")
    if "## 0.14.123 — 10 September 2026" in text:
        return
    entry = """## 0.14.123 — 10 September 2026

The fresh production feature-only Phase 14 V2 Gate B readiness check passed at `2026-09-10T09:07:50Z` and crossed the readiness prerequisite: `READY=true`. Exact helper/main head `26e91498672618b5cafb7f2d901994103c5554b8` checked accepted deployed head `e9c7afc1536880e4612cb6e3d1a7282fa37c69f5` using the accepted partitioned-storage evidence. The report observed 651 immutable `core-v2-last-trade` markets through `2026-09-10T09:05:00Z`, accepted analysis start `2026-09-09T10:20:00Z`, produced 5 eligible folds against the required 3, and found 24 feature-only markets in the final-holdout window. The first 83 candidate starts remained rejected (64 train-count, 14 test-count, 5 validation-count failures); the 84th candidate satisfied the frozen planning geometry. The hypothetical plan SHA-256 is `a1c2dff984a1dc6a2720961781af83f0d1999fbb632e33d9932c5582e688d5e2`.

This was readiness only. `labels_read=false`, `plan_artifact_written=false`, `selection_artifact_written=false`, and `HOLDOUT_TOUCHED=false`; Gate B actions were not performed and the final holdout remains unread. The transcript SHA-256 is `6e56d007833c75c989350ce05f86ed81fb4225093a5de29700b71d2d6672192f`; sanitized evidence is `docs/evidence/phase-14-v2-gate-b-readiness-20260910T090750Z.json`.

`READY=true` satisfies only the feature-chronology prerequisite. Gate B is now the next separate explicit authorization boundary. No Gate B label/outcome join, plan freeze, policy selection, final-holdout evaluation, V2 acceptance, automatic promotion, Phase 15 transition, live trading, or nonzero money change is authorized by this result. The optional readiness watcher remains not production-installed; recurring readiness checks are no longer required for the current Gate B path.

"""
    CHANGELOG.write_text(marker + entry + text[len(marker):], encoding="utf-8")


def update_master() -> None:
    text = MASTER.read_text(encoding="utf-8")
    marker = "Gate B remains unauthorized; no policy acceptance, prospective V2 activation, paper execution, promotion, Phase 15, or live/money change has occurred, and `automatic_promotion=false` remains mandatory."
    if marker not in text:
        raise SystemExit("master Gate B boundary marker not found")
    insertion = (
        "A fresh exact-main feature-only readiness check then completed at `2026-09-10T09:07:50Z` on helper head `26e91498672618b5cafb7f2d901994103c5554b8` against deployed head `e9c7afc1536880e4612cb6e3d1a7282fa37c69f5` and returned `READY=true`: 651 markets, accepted analysis start `2026-09-09T10:20:00Z`, 5 eligible folds, and 24 feature-only final-holdout markets. The run preserved `labels_read=false`, wrote no plan/selection/holdout artifact, and kept `HOLDOUT_TOUCHED=false`; the final holdout remains unread. `READY=true` ends the evidence-collection readiness blocker but does not authorize Gate B. "
        + marker
    )
    text = text.replace(marker, insertion, 1)
    MASTER.write_text(text, encoding="utf-8")


def append_decision() -> None:
    text = DECISIONS.read_text(encoding="utf-8")
    if "## D-038 — READY=true ends the feature-chronology blocker but does not authorize Gate B" in text:
        return
    entry = """

## D-038 — READY=true ends the feature-chronology blocker but does not authorize Gate B
**Date:** 10 Sep 2026  
**Status:** Active

The accepted Phase 14 V2 feature-only readiness run at `2026-09-10T09:07:50Z` returned `READY=true` under the frozen Gate B planning contract. The exact-main helper observed 651 immutable V2 markets, accepted analysis start `2026-09-09T10:20:00Z`, produced five eligible ordinary folds, and identified 24 feature-only markets in the final-holdout time window. The readiness run read no labels, wrote no plan or selection artifact, did not evaluate final-holdout outcomes, and preserved `HOLDOUT_TOUCHED=false`.

This result closes only the feature-chronology readiness blocker. It does not authorize Gate B execution or permit labels/outcomes to be joined, a Gate B plan or selection to be frozen, the final holdout to be evaluated, or any V2 policy/model/calibration/edge/min-edge choice to be accepted. Those actions remain behind a separate explicit Gate B authorization. Automatic promotion remains false, Phase 15 remains blocked, live trading remains disabled, and real-money limits remain zero.

Because the readiness prerequisite is now satisfied, repeated readiness polling is no longer required for the current Gate B path. The optional readiness watcher remains uninstalled; no production installation should be inferred or performed from `READY=true`.
"""
    DECISIONS.write_text(text.rstrip() + entry + "\n", encoding="utf-8")


def main() -> None:
    update_state()
    replace_immediate_action()
    prepend_changelog()
    update_master()
    append_decision()


if __name__ == "__main__":
    main()
