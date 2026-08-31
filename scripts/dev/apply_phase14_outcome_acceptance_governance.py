from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MASTER = ROOT / "docs" / "MASTER-SOURCE-OF-TRUTH.md"
DECISIONS = ROOT / "docs" / "DECISION-LOG.md"
CHANGELOG = ROOT / "docs" / "CHANGELOG.md"
STATE = ROOT / "PROJECT_STATE.json"

CANDIDATE = "94afff004fcbc2ed37af0297d37c51ab50ba7098"
DEPLOYED = "0189ff70fc628c71ab7c503bac369c34bf5ce8bc"
EVIDENCE = "docs/evidence/phase-14-prospective-outcome-sync-host-acceptance-20260831.json"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"expected exactly one governance anchor in {path}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    MASTER,
    "Implementation and the predictor-neutral acceptance correction are complete on the Phase 14 follow-up branch, but a passing production-host acceptance is **not yet established**. Permanent installation of the long-running research-only live predictor and prospective outcome-sync daemons is also **not yet established** and remains a separate fail-closed rollout step after host acceptance. The Master live-gate matrix remains unchanged: overall status is `fail`, Phase 15 remains blocked, and any future controlled live launch still requires every Section 4.3 gate to pass plus separate explicit real-money authorization.",
    "On 31 August 2026, corrected exact-head production-host acceptance on candidate `94afff004fcbc2ed37af0297d37c51ab50ba7098` returned `PROSPECTIVE_OUTCOME_SYNC_HOST_ACCEPTANCE=PASS`. All 54 ended unevaluated candidates resolved through official Gamma; the bounded cycle appended 54 immutable Gamma snapshots, 54 canonical `official-outcome-v1` labels, and 54 immutable live-prediction evaluations, with zero pending markets. The deployed `/opt/bp` checkout remained unchanged at `0189ff70fc628c71ab7c503bac369c34bf5ce8bc`, the paper worker remained active, the predictor remained inactive before and after acceptance, `MODE=research`, `LIVE_TRADING_ENABLED=false`, and both real-money limits remained zero. The bounded paper cycle observed four existing settlements and created none during that explicit pass; economic and calibration interpretation is intentionally deferred to the separate read-only prospective-evidence reporter. Sanitized acceptance evidence is stored at `docs/evidence/phase-14-prospective-outcome-sync-host-acceptance-20260831.json`.\n\nA passing non-deploying production-host acceptance of the outcome/evaluation sync is now established. The prospective-evidence reporter rerun and permanent installation of the long-running research-only live predictor and prospective outcome-sync daemons remain **not yet established**. The Master live-gate matrix remains unchanged: overall status is `fail`, Phase 15 remains blocked, and any future controlled live launch still requires every Section 4.3 gate to pass plus separate explicit real-money authorization."
)

replace_once(
    DECISIONS,
    "A permanent prospective runtime is a separate deployment decision after non-deploying host acceptance. That rollout must fail closed, preserve `RESEARCH`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`, and explicitly establish both `bp-live-predictor.service` and `bp-prospective-outcomes.service` as the intended long-running research-only daemons. This correction changes no evidence threshold, promotion rule, Master live gate, or Phase 15 status.",
    "A permanent prospective runtime is a separate deployment decision after non-deploying host acceptance. That rollout must fail closed, preserve `RESEARCH`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`, and explicitly establish both `bp-live-predictor.service` and `bp-prospective-outcomes.service` as the intended long-running research-only daemons. This correction changes no evidence threshold, promotion rule, Master live gate, or Phase 15 status.\n\nThe corrected predictor-neutral host acceptance subsequently passed on exact candidate `94afff004fcbc2ed37af0297d37c51ab50ba7098`. It exercised 54 ended candidates, resolved all 54 through official Gamma, appended 54 snapshots, 54 canonical labels, and 54 immutable evaluations, preserved the inactive predictor state and deployed checkout, and kept all real-money controls disabled. This validates the acceptance boundary and the canonical outcome/evaluation ingestion path; it does not itself establish profitability, calibration quality, prospective sample sufficiency, or live eligibility."
)

replace_once(
    CHANGELOG,
    "A passing production-host acceptance of the new outcome-sync path is therefore **not yet established**, and permanent installation of the live-predictor and prospective-outcome daemons as a pair has **not** been run. No new prediction-evaluation count, settlement count, prospective P&L, calibration metric, or evidence-gate improvement is claimed by this entry. The existing 0.14.1 host evidence remains authoritative until the corrected exact-head acceptance is executed. The Master live gate remains `fail`, Phase 15 remains blocked, `LIVE_TRADING_ENABLED=false`, and both real-money limits remain zero.",
    "Corrected exact-head production-host acceptance then ran on candidate `94afff004fcbc2ed37af0297d37c51ab50ba7098` and returned `PROSPECTIVE_OUTCOME_SYNC_HOST_ACCEPTANCE=PASS`. The sync observed 54 ended unevaluated candidates, resolved all 54 through official Gamma, and appended 54 new immutable snapshots, 54 new canonical labels, and 54 new immutable evaluations with zero pending markets. The deployed `/opt/bp` checkout remained unchanged at `0189ff70fc628c71ab7c503bac369c34bf5ce8bc`; the paper service remained active; the predictor was inactive both before and after; `MODE=research`; `LIVE_TRADING_ENABLED=false`; and both real-money limits remained zero. The bounded paper cycle examined 54 predictions, created no new orders/fills/terminal events/settlements, and observed three existing orders, three existing terminal events, four existing settlements, 51 skipped predictions, and cash `92.207577336709000000`. Sanitized host evidence is stored in `docs/evidence/phase-14-prospective-outcome-sync-host-acceptance-20260831.json`.\n\nThis PASS proves the canonical post-resolution ingestion path executes on production evidence; it is not a profitability, calibration, sample-sufficiency, or live-readiness claim. The read-only prospective-evidence reporter must be rerun against the newly populated evaluation/settlement ledgers before any economic interpretation. Permanent installation of the live-predictor and prospective-outcome daemons as a pair has **not** been run. The Master live gate remains `fail`, Phase 15 remains blocked, `LIVE_TRADING_ENABLED=false`, and both real-money limits remain zero."
)

state = json.loads(STATE.read_text(encoding="utf-8"))
state["updated_at"] = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
completed = state["completed"]
completed_entry = (
    "Phase 14 prospective outcome corrected exact-head production-host acceptance passed with 54/54 official Gamma resolutions, "
    "54 snapshots, 54 canonical labels, 54 immutable evaluations, unchanged deployed checkout, unchanged inactive predictor state, "
    "active paper worker, and real-money interlocks still zero/disabled"
)
if completed_entry not in completed:
    completed.append(completed_entry)

state["next_actions"] = [
    "Rerun the read-only prospective-evidence reporter on the final governance CI-green head and record actual settled sample, after-cost expectancy with uncertainty, calibration, and reconciliation without inventing thresholds or retuning frozen holdouts",
    "After the read-only reporter rerun is recorded, build and host-verify a separate fail-closed permanent install for both bp-live-predictor.service and bp-prospective-outcomes.service; do not combine permanent installation with evidence reporting",
    "Re-check Polymarket geographic/compliance eligibility only through official permitted mechanisms; do not bypass restrictions",
    "Keep LIVE_TRADING_ENABLED=false and real-money limits at zero until every Master live-gate item passes",
    "Re-run the complete Master live gate before any Phase 15 transition; explicit real-money authorization remains separately required",
]

followup = state["phase_14_prospective_outcome_sync_followup"]
followup.update(
    {
        "host_acceptance_status": "PASS",
        "host_acceptance_date": "2026-08-31",
        "host_acceptance_candidate_commit": CANDIDATE,
        "host_acceptance_deployed_head_unchanged": DEPLOYED,
        "host_acceptance_candidates": 54,
        "host_acceptance_pending_markets": 0,
        "host_acceptance_resolved_markets": 54,
        "host_acceptance_created_snapshots": 54,
        "host_acceptance_existing_snapshots": 0,
        "host_acceptance_created_labels": 54,
        "host_acceptance_existing_labels": 10,
        "host_acceptance_created_evaluations": 54,
        "host_acceptance_existing_evaluations": 0,
        "host_acceptance_paper_examined_predictions": 54,
        "host_acceptance_paper_created_orders": 0,
        "host_acceptance_paper_existing_orders": 3,
        "host_acceptance_paper_created_fills": 0,
        "host_acceptance_paper_existing_fills": 0,
        "host_acceptance_paper_created_terminal_events": 0,
        "host_acceptance_paper_existing_terminal_events": 3,
        "host_acceptance_paper_created_settlements": 0,
        "host_acceptance_paper_existing_settlements": 4,
        "host_acceptance_paper_skipped_predictions": 51,
        "host_acceptance_paper_current_cash_usd": "92.207577336709000000",
        "host_acceptance_paper_service": "active",
        "host_acceptance_predictor_service_before": "inactive",
        "host_acceptance_predictor_service_after": "inactive",
        "host_acceptance_predictor_service_unchanged": True,
        "host_acceptance_mode": "research",
        "host_acceptance_live_trading_enabled": False,
        "host_acceptance_max_trade_size_usd": 0,
        "host_acceptance_max_daily_loss_usd": 0,
        "host_acceptance_sanitized_evidence": EVIDENCE,
        "prospective_evidence_rerun_status": "PENDING",
    }
)
STATE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
