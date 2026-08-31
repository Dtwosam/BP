from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MASTER = ROOT / "docs" / "MASTER-SOURCE-OF-TRUTH.md"
DECISIONS = ROOT / "docs" / "DECISION-LOG.md"
CHANGELOG = ROOT / "docs" / "CHANGELOG.md"
STATE = ROOT / "PROJECT_STATE.json"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(
            f"expected exactly one governance anchor in {path}: {old[:100]!r}"
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> None:
    master_old = (
        "The runtime is permitted only in `RESEARCH` with `LIVE_TRADING_ENABLED=false`, "
        "`MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`. It exposes no wallet, signing, "
        "real-order, promotion, or live-enable path. Its exact-head host acceptance is deliberately "
        "non-deploying: it runs the candidate from a detached worktree, requires the existing paper "
        "worker and live predictor to be active, may append only canonical official-outcome/evaluation "
        "evidence and derivative paper settlements, verifies `/opt/bp` is unchanged, and performs no "
        "package installation, migration, service stop/restart, or daemon installation."
    )
    master_new = (
        "The runtime is permitted only in `RESEARCH` with `LIVE_TRADING_ENABLED=false`, "
        "`MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`. It exposes no wallet, signing, "
        "real-order, promotion, or live-enable path. Its exact-head host acceptance is deliberately "
        "non-deploying: it runs the candidate from a detached worktree, requires the existing paper "
        "worker to be active, records the existing live-predictor service state without requiring it "
        "to be active, requires that predictor state to be unchanged after acceptance, may append only "
        "canonical official-outcome/evaluation evidence and derivative paper settlements, verifies "
        "`/opt/bp` is unchanged, and performs no package installation, migration, service start/stop/"
        "restart, or daemon installation."
    )
    replace_once(MASTER, master_old, master_new)

    master_anchor = (
        "Implementation and exact-head CI are complete on the Phase 14 follow-up branch, but production "
        "host acceptance and permanent installation of the outcome-sync daemon are **not yet established**. "
        "The Master live-gate matrix remains unchanged: overall status is `fail`, Phase 15 remains blocked, "
        "and any future controlled live launch still requires every Section 4.3 gate to pass plus separate "
        "explicit real-money authorization."
    )
    master_replacement = (
        "On 31 August 2026, the first production-host acceptance attempt on candidate "
        "`c11000bf97bcfe93b91d17134c43bbd10a5791ef` failed closed before outcome processing with "
        "`REASON=predictor_service_not_active_before`. Investigation confirmed that Phase 10 host "
        "acceptance had created `bp-live-predictor.service` only as a temporary runtime unit under "
        "`/run/systemd/system` and removed it during cleanup; the canonical project record never "
        "established a permanent predictor installation. The failed attempt remains valid operational "
        "evidence. TDD corrected only this acceptance precondition: RED "
        "`040fc2b6a322abb58b1aa9e27025ad687b5502c5` produced exactly one intended deployment-contract "
        "failure with 784 existing tests passing, and GREEN "
        "`8d04a38c366370835a1c530c4aa542ed8521a3b2` passed all 785 tests plus deployment validation, "
        "health, dashboard checks, Historical Backfill Smoke #486, Live Recorder Smoke #593, and "
        "Recorder Short Soak #558.\n\n"
        "Implementation and the predictor-neutral acceptance correction are complete on the Phase 14 "
        "follow-up branch, but a passing production-host acceptance is **not yet established**. Permanent "
        "installation of the long-running research-only live predictor and prospective outcome-sync "
        "daemons is also **not yet established** and remains a separate fail-closed rollout step after "
        "host acceptance. The Master live-gate matrix remains unchanged: overall status is `fail`, Phase "
        "15 remains blocked, and any future controlled live launch still requires every Section 4.3 gate "
        "to pass plus separate explicit real-money authorization."
    )
    replace_once(MASTER, master_anchor, master_replacement)

    decision_old = (
        "The outcome-sync CLI exposes only bounded one-cycle or repeated research execution, reuses the "
        "existing research/live-disabled/zero-money safety guard, and contains no wallet, signing, "
        "order-submission, promotion, or live-enable path. Host acceptance must run an exact candidate "
        "from a detached worktree, require the existing paper and live-predictor services to be active, "
        "keep `/opt/bp` unchanged, and may append only official outcome/label/evaluation evidence plus "
        "paper settlements derived through the already-existing paper worker. It must not install packages, "
        "migrate production, stop/restart services, or install the new daemon. Permanent rollout remains a "
        "separate explicit step after host acceptance. The Master live gate remains unchanged and Phase 15 "
        "remains blocked."
    )
    decision_new = (
        "The outcome-sync CLI exposes only bounded one-cycle or repeated research execution, reuses the "
        "existing research/live-disabled/zero-money safety guard, and contains no wallet, signing, "
        "order-submission, promotion, or live-enable path. Host acceptance must run an exact candidate "
        "from a detached worktree, require the existing paper service to be active, record the live-predictor "
        "service state without requiring activity, require that predictor state to remain exactly unchanged, "
        "keep `/opt/bp` unchanged, and may append only official outcome/label/evaluation evidence plus paper "
        "settlements derived through the already-existing paper worker. It must not install packages, migrate "
        "production, start/stop/restart services, or install either prospective daemon. Permanent rollout "
        "remains a separate explicit step after host acceptance. The Master live gate remains unchanged and "
        "Phase 15 remains blocked."
    )
    replace_once(DECISIONS, decision_old, decision_new)

    decision_text = DECISIONS.read_text(encoding="utf-8")
    if "## D-030 —" in decision_text:
        raise RuntimeError("D-030 already exists")
    d030 = (
        "\n\n## D-030 — Outcome-sync acceptance is predictor-neutral; permanent prospective daemons require a separate install gate\n"
        "**Date:** 31 Aug 2026  \n"
        "**Status:** Active\n\n"
        "The first production-host outcome-sync acceptance attempt on candidate "
        "`c11000bf97bcfe93b91d17134c43bbd10a5791ef` failed closed before outcome processing with "
        "`REASON=predictor_service_not_active_before`. Investigation established that this was an invalid "
        "acceptance precondition, not evidence that the outcome chain itself had failed: Phase 10 acceptance "
        "used a temporary `/run/systemd/system/bp-live-predictor.service` runtime unit and cleaned it up, and "
        "the canonical state never recorded a permanent predictor installation.\n\n"
        "Outcome-sync host acceptance is therefore predictor-neutral. It may inspect and report the existing "
        "predictor service state, but it may not require that service to be active and may not start, stop, "
        "restart, install, or otherwise mutate it. The before/after predictor state must match exactly. The "
        "paper worker remains required active because the bounded acceptance deliberately exercises the "
        "already-installed money-disabled paper settlement path after canonical evaluations are appended.\n\n"
        "A permanent prospective runtime is a separate deployment decision after non-deploying host acceptance. "
        "That rollout must fail closed, preserve `RESEARCH`, `LIVE_TRADING_ENABLED=false`, "
        "`MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`, and explicitly establish both "
        "`bp-live-predictor.service` and `bp-prospective-outcomes.service` as the intended long-running "
        "research-only daemons. This correction changes no evidence threshold, promotion rule, Master live "
        "gate, or Phase 15 status."
    )
    DECISIONS.write_text(decision_text.rstrip("\n") + d030 + "\n", encoding="utf-8")

    changelog_old = (
        "A separate exact-head Google Cloud Shell acceptance helper is deliberately non-deploying. It requires "
        "the existing paper worker and live predictor to be active, validates research/live-disabled/zero-money "
        "settings, fetches and runs the exact candidate from a detached worktree with the existing permanent "
        "Python environment, performs one bounded prospective-outcome cycle followed by one bounded paper-"
        "execution cycle, verifies both existing services remain active, and proves the deployed `/opt/bp` "
        "checkout is unchanged. It performs no package installation, migration, service stop/restart, checkout/"
        "reset, or daemon installation. The helper may append canonical official snapshot/label/evaluation "
        "evidence and paper settlements derived by the existing paper worker; it cannot create real-money side "
        "effects."
    )
    changelog_new = (
        "A separate exact-head Google Cloud Shell acceptance helper is deliberately non-deploying. It requires "
        "the existing paper worker to be active, records the live-predictor service state without requiring "
        "activity, requires that predictor state to remain exactly unchanged, validates research/live-disabled/"
        "zero-money settings, fetches and runs the exact candidate from a detached worktree with the existing "
        "permanent Python environment, performs one bounded prospective-outcome cycle followed by one bounded "
        "paper-execution cycle, and proves the deployed `/opt/bp` checkout is unchanged. It performs no package "
        "installation, migration, service start/stop/restart, checkout/reset, or daemon installation. The helper "
        "may append canonical official snapshot/label/evaluation evidence and paper settlements derived by the "
        "existing paper worker; it cannot create real-money side effects."
    )
    replace_once(CHANGELOG, changelog_old, changelog_new)

    changelog_anchor = (
        "Production host acceptance of the new outcome-sync path has **not** yet been run, and the new daemon "
        "has **not** been permanently installed. No new prediction-evaluation count, settlement count, "
        "prospective P&L, calibration metric, or evidence-gate improvement is claimed by this entry. The existing "
        "0.14.1 host evidence remains authoritative until the new exact-head acceptance is executed. The Master "
        "live gate remains `fail`, Phase 15 remains blocked, `LIVE_TRADING_ENABLED=false`, and both real-money "
        "limits remain zero."
    )
    changelog_replacement = (
        "The first production-host acceptance attempt ran on candidate "
        "`c11000bf97bcfe93b91d17134c43bbd10a5791ef` on 31 August 2026 and failed closed before outcome "
        "processing with `REASON=predictor_service_not_active_before`. Investigation showed that Phase 10 had "
        "host-accepted the predictor through a temporary `/run/systemd/system` unit but had never established a "
        "permanent predictor installation. The failure is retained as operational evidence rather than erased. "
        "A corrective TDD cycle changed only the acceptance precondition: RED "
        "`040fc2b6a322abb58b1aa9e27025ad687b5502c5` passed Ruff and 784 existing tests while failing only the "
        "new predictor-neutral contract; GREEN `8d04a38c366370835a1c530c4aa542ed8521a3b2` passed all 785 tests "
        "in CI run `33382679725`, with deployment validation, health, dashboard checks, Historical Backfill "
        "Smoke #486 (`33382679677`), Live Recorder Smoke #593 (`33382679679`), and Recorder Short Soak #558 "
        "(`33382679682`) also passing.\n\n"
        "A passing production-host acceptance of the new outcome-sync path is therefore **not yet established**, "
        "and permanent installation of the live-predictor and prospective-outcome daemons as a pair has **not** "
        "been run. No new prediction-evaluation count, settlement count, prospective P&L, calibration metric, or "
        "evidence-gate improvement is claimed by this entry. The existing 0.14.1 host evidence remains "
        "authoritative until the corrected exact-head acceptance is executed. The Master live gate remains "
        "`fail`, Phase 15 remains blocked, `LIVE_TRADING_ENABLED=false`, and both real-money limits remain zero."
    )
    replace_once(CHANGELOG, changelog_anchor, changelog_replacement)

    state = json.loads(STATE.read_text(encoding="utf-8"))
    state["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )

    completed_item = (
        "Phase 14 outcome-sync first production acceptance failed closed on an invalid predictor-active "
        "precondition, then the non-deploying acceptance contract was corrected test-first to preserve rather "
        "than require predictor service state"
    )
    completed = state.setdefault("completed", [])
    if completed_item not in completed:
        completed.append(completed_item)

    next_actions = state["next_actions"]
    next_actions[0] = (
        "Rerun corrected predictor-neutral exact-head prospective outcome-sync host acceptance on the final "
        "governance CI-green head and record actual snapshot/label/evaluation/settlement counts without mutating "
        "the Master live gate automatically"
    )
    next_actions[1] = (
        "After successful non-deploying outcome-sync host acceptance, build and host-verify a separate fail-closed "
        "permanent install for both bp-live-predictor.service and bp-prospective-outcomes.service; do not start or "
        "install either daemon inside acceptance"
    )

    follow = state["phase_14_prospective_outcome_sync_followup"]
    follow.update(
        {
            "first_host_acceptance_attempt_date": "2026-08-31",
            "first_host_acceptance_attempt_commit": "c11000bf97bcfe93b91d17134c43bbd10a5791ef",
            "first_host_acceptance_attempt_status": "FAIL",
            "first_host_acceptance_attempt_reason": "predictor_service_not_active_before",
            "first_host_acceptance_attempt_outcome_processing_started": False,
            "phase10_permanent_predictor_install_established": False,
            "predictor_service_required_active": False,
            "predictor_service_state_must_remain_unchanged": True,
            "predictor_service_mutation_allowed_in_acceptance": False,
            "acceptance_precondition_correction_red_commit": "040fc2b6a322abb58b1aa9e27025ad687b5502c5",
            "acceptance_precondition_correction_red_ci_run_id": 33382522757,
            "acceptance_precondition_correction_red_result": "1 failed, 784 passed",
            "acceptance_precondition_correction_green_commit": "8d04a38c366370835a1c530c4aa542ed8521a3b2",
            "acceptance_precondition_correction_green_ci_run_id": 33382679725,
            "acceptance_precondition_correction_green_ci_passed": True,
            "acceptance_precondition_correction_green_test_count": 785,
            "acceptance_precondition_correction_smoke_runs": {
                "historical_backfill": 33382679677,
                "live_recorder": 33382679679,
                "recorder_short_soak": 33382679682,
            },
            "permanent_install_scope": [
                "bp-live-predictor.service",
                "bp-prospective-outcomes.service",
            ],
            "permanent_install_requires_separate_host_gate": True,
        }
    )

    STATE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
