from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = "d2b2d515a4b982c691360fa1c6c46a461a665ff9"
OLD_HEAD = "0189ff70fc628c71ab7c503bac369c34bf5ce8bc"
FAILED_CANDIDATE = "196519555bed8f68d37654bd171dac23f681fd52"
ACCEPTED_AT = "2026-08-31T13:10:03Z"
EVIDENCE_REL = "docs/evidence/phase-14-prospective-runtime-install-host-acceptance-20260831.json"
HOST_EVIDENCE = "/var/lib/bp/evidence/phase14-prospective-runtime-install-20260831T131003Z.txt"


def append_once(path: Path, marker: str, text: str) -> None:
    current = path.read_text()
    if marker in current:
        return
    if not current.endswith("\n"):
        current += "\n"
    path.write_text(current + text)


def record_evidence() -> None:
    path = ROOT / EVIDENCE_REL
    evidence = {
        "schema_version": 1,
        "evidence_type": "phase14_prospective_runtime_install_host_acceptance",
        "acceptance_date": "2026-08-31",
        "accepted_at": ACCEPTED_AT,
        "candidate_head": CANDIDATE,
        "previous_deployed_head": OLD_HEAD,
        "deployed_head": CANDIDATE,
        "host_acceptance_verdict": "PASS",
        "first_install_attempt": {
            "candidate_head": FAILED_CANDIDATE,
            "verdict": "FAIL",
            "reason": "deployed_checkout_not_clean",
            "mutation_started": False,
            "interpretation": "The first permanent-install attempt failed closed before checkout, unit, or service mutation. Read-only inspection showed established dashboard-generated runtime residue rather than arbitrary source edits.",
        },
        "runtime_services": {
            "bp-live-predictor.service": {"active": True, "enabled": True},
            "bp-prospective-outcomes.service": {"active": True, "enabled": True},
            "bp-recorder.service": {"active": True},
            "bp-postgres.service": {"active": True},
            "bp-dashboard-api.service": {"active": True},
            "bp-dashboard-web.service": {"active": True},
            "bp-paper-execution.service": {"active": True},
        },
        "runtime_safety": {
            "safety_env_file": "/etc/bp/bp-prospective-runtime-safety.env",
            "safety_env_file_previous_mode": "absent",
            "mode": "research",
            "live_trading_enabled": False,
            "max_trade_size_usd": 0,
            "max_daily_loss_usd": 0,
        },
        "host_evidence_file": HOST_EVIDENCE,
        "interpretation": "The exact-head permanent research-runtime install succeeded after a fail-closed dashboard-residue correction. Both prospective daemons are now active and enabled, all five pre-existing core services remained active, and the effective runtime boundary remains RESEARCH with live trading disabled and both real-money limits zero. This is evidence-continuity infrastructure only: it does not improve or override the negative prospective profitability evidence, does not promote a model, does not change any Master live-gate item, and does not permit Phase 15.",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2) + "\n")


def update_master() -> None:
    path = ROOT / "docs/MASTER-SOURCE-OF-TRUTH.md"
    marker = "# 34. Phase 14 permanent prospective research runtime installation"
    text = f"""

---

# 34. Phase 14 permanent prospective research runtime installation

On 31 August 2026, the separate research-only permanent runtime rollout authorized by D-030/D-031 was completed for `bp-live-predictor.service` and `bp-prospective-outcomes.service`. This rollout exists only to continue immutable prospective prediction, official-outcome, evaluation, and money-disabled paper evidence collection. It does not constitute economic validation, model promotion, live-gate progress, or real-money authorization.

The first production install attempt on exact candidate `{FAILED_CANDIDATE}` failed closed before any checkout, unit, or service mutation with `REASON=deployed_checkout_not_clean`. Read-only host inspection established that `/opt/bp` contained the dashboard build/runtime residue produced by the already-established dashboard deployment: modified tracked `apps/dashboard/next-env.d.ts` and `apps/dashboard/tsconfig.json`, plus untracked `.node/`, `apps/dashboard/.next/`, `apps/dashboard/node_modules/`, and `apps/dashboard/tsconfig.tsbuildinfo`. The correction did not delete or reset those artifacts. Instead, a test-first installer change permits only that explicit generated residue, rejects every other tracked or untracked checkout status entry, rejects candidate commits that collide with the preserved runtime paths, and preserves/restores the two tolerated tracked generated files during rollback.

Corrected exact-head pre-host verification passed on candidate `{CANDIDATE}`: CI #1661 / run `33394458434`, Historical Backfill Smoke #528 / run `33394458466`, Live Recorder Smoke #635 / run `33394458523`, and Recorder Short Soak #600 / run `33394458454` all succeeded. The associated residue-regression RED checkpoint was `d731d2896e476ee082e6d39d47305fe08ecc97b3`; the final corrected candidate also includes the follow-up status-classification and documentation consistency fixes.

The corrected production-host run returned `PHASE14_PROSPECTIVE_RUNTIME_INSTALL=PASS`. `/opt/bp` moved from `{OLD_HEAD}` to exact candidate `{CANDIDATE}`. `bp-live-predictor.service` is active and enabled, `bp-prospective-outcomes.service` is active and enabled, and all five established core services remained active: recorder, PostgreSQL, dashboard API, dashboard web, and paper execution. The root-controlled safety file is `/etc/bp/bp-prospective-runtime-safety.env`; its previous state was absent. Effective safety remained `MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`. Sanitized repository evidence is `{EVIDENCE_REL}`; the host-local evidence file is `{HOST_EVIDENCE}`.

This operational PASS does not alter Section 4.3. Prospective after-cost profitability remains `fail`; prospective sample sufficiency and calibration remain `insufficient_evidence`; geographic/compliance eligibility remains `fail`; explicit real-money authorization remains `fail`; `automatic_promotion=false`; the overall Master live gate remains `fail`; and Phase 15 remains blocked.
"""
    append_once(path, marker, text)


def update_decisions() -> None:
    path = ROOT / "docs/DECISION-LOG.md"
    marker = "## D-032 — Permanent prospective research daemons are operational continuity, not live-gate progress"
    text = f"""

## D-032 — Permanent prospective research daemons are operational continuity, not live-gate progress
**Date:** 31 Aug 2026  
**Status:** Active

The separately authorized permanent research runtime for `bp-live-predictor.service` and `bp-prospective-outcomes.service` is now established on production. Its sole purpose is to continue collecting immutable prospective predictions, official Gamma outcomes, canonical labels/evaluations, and money-disabled paper evidence. Installing or running these daemons cannot count as economic validation, model promotion, live-gate progress, or real-money authorization.

The first install attempt on candidate `{FAILED_CANDIDATE}` failed closed before mutation because the deployed checkout contained established dashboard-generated build/runtime residue. The approved correction does not clean or reset that production state. It allows only the explicitly identified dashboard runtime paths, fails closed on every other checkout change, rejects candidate/runtime path collisions, and preserves the tolerated tracked generated dashboard files for rollback.

Corrected exact-head install candidate `{CANDIDATE}` passed CI #1661 plus Historical Backfill Smoke #528, Live Recorder Smoke #635, and Recorder Short Soak #600, then passed production installation. The deployed head became `{CANDIDATE}`; both prospective daemons are active and enabled; recorder, PostgreSQL, dashboard API/web, and paper execution remained active; and the root-controlled runtime boundary is still `RESEARCH`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`.

D-031's negative prospective profitability result is unchanged and remains canonical. No prospective threshold was retuned, no evidence gate was upgraded by the installation, `automatic_promotion=false`, the Master live gate remains `fail`, and Phase 15 remains blocked. Sanitized evidence: `{EVIDENCE_REL}`.
"""
    append_once(path, marker, text)


def update_changelog() -> None:
    path = ROOT / "docs/CHANGELOG.md"
    current = path.read_text()
    marker = "Permanent research-runtime installation subsequently passed on exact candidate"
    if marker in current:
        return
    anchor = "\n## 0.14.1 — 30–31 August 2026"
    if anchor not in current:
        raise RuntimeError("0.14.1 changelog anchor not found")
    addition = f"""

The separate permanent research-runtime rollout then proceeded under D-030/D-031. Its first production attempt on candidate `{FAILED_CANDIDATE}` failed closed before mutation with `REASON=deployed_checkout_not_clean`. Read-only inspection showed established dashboard-generated runtime residue: tracked `apps/dashboard/next-env.d.ts` and `apps/dashboard/tsconfig.json` modifications plus untracked `.node/`, `apps/dashboard/.next/`, `apps/dashboard/node_modules/`, and `apps/dashboard/tsconfig.tsbuildinfo`. A test-first correction permits only those explicit generated paths, rejects every other tracked/untracked checkout status entry and any candidate collision with preserved runtime paths, and restores the two tolerated tracked generated files during rollback. The residue-regression RED checkpoint was `d731d2896e476ee082e6d39d47305fe08ecc97b3`; the corrected final pre-host candidate `{CANDIDATE}` passed CI #1661 (`33394458434`), Historical Backfill Smoke #528 (`33394458466`), Live Recorder Smoke #635 (`33394458523`), and Recorder Short Soak #600 (`33394458454`).

Permanent research-runtime installation subsequently passed on exact candidate `{CANDIDATE}` with `/opt/bp` advanced from `{OLD_HEAD}` to the exact candidate. `bp-live-predictor.service` and `bp-prospective-outcomes.service` are both active and enabled; recorder, PostgreSQL, dashboard API, dashboard web, and paper execution all remained active. The root-controlled safety environment remains `MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`. Host-local evidence is `{HOST_EVIDENCE}` and sanitized repository evidence is `{EVIDENCE_REL}`. This is operational evidence-continuity infrastructure only: the negative prospective profitability evidence remains unchanged, no model was promoted, the Master live gate remains `fail`, and Phase 15 remains blocked.
"""
    path.write_text(current.replace(anchor, addition + anchor, 1))


def update_state() -> None:
    path = ROOT / "PROJECT_STATE.json"
    state = json.loads(path.read_text())
    state["updated_at"] = ACCEPTED_AT
    followup = state["phase_14_prospective_outcome_sync_followup"]
    followup.update(
        {
            "permanent_install_status": "PASS",
            "phase10_permanent_predictor_install_established": True,
            "permanent_install_date": "2026-08-31",
            "permanent_install_accepted_at": ACCEPTED_AT,
            "permanent_install_candidate_commit": CANDIDATE,
            "permanent_install_previous_deployed_head": OLD_HEAD,
            "permanent_install_deployed_head": CANDIDATE,
            "permanent_install_first_attempt_commit": FAILED_CANDIDATE,
            "permanent_install_first_attempt_status": "FAIL",
            "permanent_install_first_attempt_reason": "deployed_checkout_not_clean",
            "permanent_install_first_attempt_mutation_started": False,
            "permanent_install_residue_allowlist_is_narrow": True,
            "permanent_install_predictor_active": True,
            "permanent_install_predictor_enabled": True,
            "permanent_install_outcome_sync_active": True,
            "permanent_install_outcome_sync_enabled": True,
            "permanent_install_core_services_active": {
                "bp-recorder.service": True,
                "bp-postgres.service": True,
                "bp-dashboard-api.service": True,
                "bp-dashboard-web.service": True,
                "bp-paper-execution.service": True,
            },
            "permanent_install_safety_env_file": "/etc/bp/bp-prospective-runtime-safety.env",
            "permanent_install_safety_env_file_previous_mode": "absent",
            "permanent_install_mode": "research",
            "permanent_install_live_trading_enabled": False,
            "permanent_install_max_trade_size_usd": 0,
            "permanent_install_max_daily_loss_usd": 0,
            "permanent_install_host_evidence_file": HOST_EVIDENCE,
            "permanent_install_sanitized_evidence": EVIDENCE_REL,
            "permanent_install_master_live_gate_mutated": False,
            "permanent_install_phase15_permitted": False,
        }
    )

    completed_item = (
        "Phase 14 permanent research runtime installation passed on exact candidate "
        f"{CANDIDATE} with both prospective daemons active+enabled, all five core services active, "
        "and RESEARCH/live-disabled/zero-money safety unchanged"
    )
    if completed_item not in state["completed"]:
        state["completed"].append(completed_item)

    install_action = (
        "Build and host-verify a separate fail-closed permanent install for both bp-live-predictor.service and "
        "bp-prospective-outcomes.service in RESEARCH with live trading false and both real-money limits zero; "
        "installation is evidence-continuity infrastructure, not live-gate progress"
    )
    state["next_actions"] = [a for a in state["next_actions"] if a != install_action]
    continuation = (
        "Keep the permanently installed research-only predictor and outcome-sync daemons under operational observation while continuing immutable prospective evidence collection; this runtime is evidence-continuity infrastructure, not live-gate progress"
    )
    if continuation not in state["next_actions"]:
        state["next_actions"].insert(0, continuation)

    if state["live_trading_enabled"] is not False:
        raise RuntimeError("top-level live trading unexpectedly enabled")
    if state["phase_14_checkpoint"]["overall_live_gate"] != "fail":
        raise RuntimeError("Master live gate unexpectedly changed")
    if state["phase_14_checkpoint"]["phase15_permitted"] is not False:
        raise RuntimeError("Phase 15 unexpectedly permitted")

    path.write_text(json.dumps(state, indent=2) + "\n")


def main() -> None:
    record_evidence()
    update_master()
    update_decisions()
    update_changelog()
    update_state()


if __name__ == "__main__":
    main()
