from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MASTER = ROOT / "docs" / "MASTER-SOURCE-OF-TRUTH.md"
DECISIONS = ROOT / "docs" / "DECISION-LOG.md"
CHANGELOG = ROOT / "docs" / "CHANGELOG.md"
STATE = ROOT / "PROJECT_STATE.json"

REPORT_HEAD = "de907d324c7ee4ec46e2dfef1eb516dbb3fa8348"
DEPLOYED_HEAD = "0189ff70fc628c71ab7c503bac369c34bf5ce8bc"
EVIDENCE = "docs/evidence/phase-14-prospective-evidence-host-report-post-outcome-sync-20260831.json"

MASTER_SECTION = f"""

---

# 33. Phase 14 post-outcome-sync prospective evidence result

On 31 August 2026, the read-only prospective-evidence reporter was rerun on exact candidate `{REPORT_HEAD}` after the accepted outcome sync had populated the immutable evaluation ledger. The report returned `PROSPECTIVE_EVIDENCE_HOST_REPORT=PASS`; the deployed `/opt/bp` checkout remained unchanged at `{DEPLOYED_HEAD}`, the paper service remained active, `LIVE_TRADING_ENABLED=false`, and both real-money limits remained zero.

The rerun observed 54 immutable prediction evaluations and two settled prospective paper trades. Realized after-cost P&L was `-7.792422663291` USD total and `-3.8962113316455` USD mean per settled trade. The deterministic 10,000-resample bootstrap 95% interval for mean realized P&L was `[-4.285508316075, -3.506914347216]`, entirely below zero. The reporter therefore classifies `positive_after_cost_profitability=fail`. This is direct prospective negative economic evidence; it must not be hidden by the larger evaluation count or by post-hoc threshold retuning.

Across all 54 evaluated predictions, raw Brier/log-loss means were `0.11328198148148148` / `0.3669084283864382` and calibrated Brier/log-loss means were `0.10868378084722523` / `0.35286272448721295`. Because no approved prospective numerical calibration acceptance threshold exists, `calibration_acceptable` remains `insufficient_evidence`. Because Section 4.3 deliberately defines no fixed prospective sample count, `sufficiently_large_live_paper_sample_with_uncertainty` also remains `insufficient_evidence`; the observed size and uncertainty must be reported rather than converted into an invented pass/fail sample threshold.

Paper reconciliation remained `OK` with zero violations across three paper orders, three trade signals, and 51 no-trade signals, so `order_execution_and_reconciliation_tested=pass`. `automatic_promotion=false`. The Master live gate remains `fail`: prospective profitability fails, sample/calibration and walk-forward stability remain insufficient, geographic eligibility fails, and explicit real-money authorization is absent. Phase 15 remains blocked. Sanitized evidence is stored at `{EVIDENCE}`.

This negative prospective result does not prevent the separate research-only permanent installation of `bp-live-predictor.service` and `bp-prospective-outcomes.service` for continued immutable evidence collection. Such installation is an operational continuity step only; it cannot promote a model, change an evidence gate, or authorize live trading.
"""

DECISION = f"""

## D-031 — Negative prospective profitability remains a fail; research daemons may continue collecting evidence without promotion
**Date:** 31 Aug 2026  
**Status:** Active

After the canonical outcome sync populated 54 immutable live-prediction evaluations, the read-only prospective-evidence reporter was rerun on exact candidate `{REPORT_HEAD}`. It observed two settled prospective paper trades with realized after-cost total P&L `-7.792422663291` USD and mean `-3.8962113316455` USD. The deterministic 10,000-resample bootstrap 95% interval for mean realized P&L was `[-4.285508316075, -3.506914347216]`, entirely below zero. Therefore prospective `positive_after_cost_profitability` is `fail`; this result must not be reframed as positive because the evaluation count is larger, nor retuned away post hoc using the same prospective evidence.

Calibration over 54 evaluations improved numerically after the frozen calibrator (Brier `0.11328198148148148` to `0.10868378084722523`; log loss `0.3669084283864382` to `0.35286272448721295`), but no approved prospective calibration threshold exists, so `calibration_acceptable` remains `insufficient_evidence`. No fixed prospective sample-size threshold exists either, so sample sufficiency remains `insufficient_evidence`. Reconciliation is `OK` with zero violations and remains `pass`.

This evidence does not authorize promotion or Phase 15. The Master live gate remains `fail`, `automatic_promotion=false`, and all real-money controls remain disabled/zero. A separate permanent installation of the already-approved research-only predictor and prospective-outcome daemons may proceed solely to preserve prospective evidence continuity; successful installation must not be treated as economic validation or live-gate progress.
"""

CHANGELOG_PARAGRAPHS = f"""

After the outcome-sync acceptance, the read-only prospective-evidence reporter was rerun on exact candidate `{REPORT_HEAD}`. It returned `PROSPECTIVE_EVIDENCE_HOST_REPORT=PASS` with the deployed checkout still at `{DEPLOYED_HEAD}`, the paper service active, live trading disabled, and both real-money limits zero. The newly populated evidence ledger contained 54 prediction evaluations and two settled prospective paper trades. Realized after-cost P&L was `-7.792422663291` USD total and `-3.8962113316455` USD mean, with a deterministic 10,000-resample bootstrap 95% mean interval `[-4.285508316075, -3.506914347216]`; prospective profitability therefore remains `fail`. Raw/calibrated Brier were `0.11328198148148148` / `0.10868378084722523` and raw/calibrated log loss were `0.3669084283864382` / `0.35286272448721295`, but calibration remains `insufficient_evidence` because no prospective numerical acceptance threshold is approved. Sample sufficiency also remains `insufficient_evidence`; reconciliation remained `OK` with zero violations and stays `pass`. Sanitized evidence is stored in `{EVIDENCE}`. The Master live gate remains `fail`, Phase 15 remains blocked, and no promotion or live activation occurred.
"""


def append_once(path: Path, marker: str, block: str) -> None:
    text = path.read_text(encoding="utf-8")
    if marker in text:
        return
    path.write_text(text.rstrip() + block.rstrip() + "\n", encoding="utf-8")


def insert_before_once(path: Path, marker: str, sentinel: str, block: str) -> None:
    text = path.read_text(encoding="utf-8")
    if sentinel in text:
        return
    if text.count(marker) != 1:
        raise SystemExit(f"expected exactly one marker in {path}: {marker!r}")
    text = text.replace(marker, block.rstrip() + "\n\n" + marker, 1)
    path.write_text(text, encoding="utf-8")


append_once(MASTER, "# 33. Phase 14 post-outcome-sync prospective evidence result", MASTER_SECTION)
append_once(DECISIONS, "## D-031 — Negative prospective profitability", DECISION)
insert_before_once(
    CHANGELOG,
    "## 0.14.1 — 30–31 August 2026",
    "After the outcome-sync acceptance, the read-only prospective-evidence reporter was rerun",
    CHANGELOG_PARAGRAPHS,
)

state = json.loads(STATE.read_text(encoding="utf-8"))
state["updated_at"] = "2026-08-31T11:39:00Z"

completed = state["completed"]
completion = (
    "Phase 14 post-outcome-sync prospective-evidence reporter rerun passed read-only host safety checks with 54 evaluations, two settled trades, negative after-cost P&L and entirely negative mean-P&L bootstrap interval, calibration/sample sufficiency still insufficient, reconciliation OK, and live gate unchanged"
)
if completion not in completed:
    completed.append(completion)

old_next = [
    "Rerun the read-only prospective-evidence reporter on the final governance CI-green head and record actual settled sample, after-cost expectancy with uncertainty, calibration, and reconciliation without inventing thresholds or retuning frozen holdouts",
    "After the read-only reporter rerun is recorded, build and host-verify a separate fail-closed permanent install for both bp-live-predictor.service and bp-prospective-outcomes.service; do not combine permanent installation with evidence reporting",
]
new_next = [
    "Build and host-verify a separate fail-closed permanent install for both bp-live-predictor.service and bp-prospective-outcomes.service in RESEARCH with live trading false and both real-money limits zero; installation is evidence-continuity infrastructure, not live-gate progress",
    "Continue collecting immutable prospective predictions, official outcomes, evaluations, paper settlements, and uncertainty without retuning frozen policy thresholds to the same prospective evidence; current prospective after-cost profitability is fail",
]
next_actions = state["next_actions"]
for item in old_next:
    if item in next_actions:
        next_actions.remove(item)
for item in reversed(new_next):
    if item not in next_actions:
        next_actions.insert(0, item)

follow = state["phase_14_prospective_evidence_followup"]
follow["post_outcome_sync_rerun"] = {
    "candidate_commit": REPORT_HEAD,
    "report_date": "2026-08-31",
    "host_report_status": "PASS",
    "deployed_head_unchanged": DEPLOYED_HEAD,
    "paper_service": "active",
    "settled_trade_count": 2,
    "evaluation_count": 54,
    "realized_total_after_cost_pnl_usd": "-7.792422663291000000",
    "realized_mean_after_cost_pnl_usd": "-3.896211331645500000",
    "mean_95pct_ci_usd": {
        "lower": -4.285508316075,
        "upper": -3.506914347216,
        "method": "deterministic_bootstrap_percentile",
        "resamples": 10000,
        "seed": 14,
    },
    "raw_brier_mean": 0.11328198148148148,
    "raw_log_loss_mean": 0.3669084283864382,
    "calibrated_brier_mean": 0.10868378084722523,
    "calibrated_log_loss_mean": 0.35286272448721295,
    "sample_sufficiency": "insufficient_evidence",
    "positive_after_cost_profitability": "fail",
    "calibration_acceptable": "insufficient_evidence",
    "order_execution_and_reconciliation_tested": "pass",
    "reconciliation_status": "OK",
    "reconciliation_violation_count": 0,
    "automatic_promotion": False,
    "master_live_gate_mutated": False,
    "live_trading_enabled": False,
    "max_trade_size_usd": 0,
    "max_daily_loss_usd": 0,
    "phase15_permitted": False,
    "sanitized_evidence": EVIDENCE,
}

outcome = state["phase_14_prospective_outcome_sync_followup"]
outcome["prospective_evidence_rerun_status"] = "PASS"
outcome["prospective_evidence_rerun_candidate_commit"] = REPORT_HEAD
outcome["prospective_evidence_rerun_sanitized_evidence"] = EVIDENCE
outcome["prospective_profitability_after_rerun"] = "fail"
outcome["permanent_install_status"] = "NOT_RUN"

STATE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
