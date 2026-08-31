from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MASTER = ROOT / "docs" / "MASTER-SOURCE-OF-TRUTH.md"
STATE = ROOT / "PROJECT_STATE.json"
CHANGELOG = ROOT / "docs" / "CHANGELOG.md"

MASTER_SECTION = """

---

# 32. Phase 14 prospective outcome/evaluation sync follow-up

The 31 August 2026 prospective-evidence host report established that the reporting path itself was healthy while the prospective evidence sample remained empty: zero live prediction evaluations and zero paper settlements were present. Root-cause tracing showed that prospective paper settlement depends on an immutable live-prediction evaluation, that evaluation depends on the canonical `official-outcome-v1` label, and that label depends on a preserved resolved Polymarket Gamma snapshot. The production runtime did not have an always-on post-resolution snapshot-ingestion path for newly completed prospective predictions.

The approved follow-up is a separate **money-disabled prospective outcome sync** that closes only that evidence-ingestion gap. For ended immutable predictions that still lack evaluation, it may fetch the exact market by slug from official Polymarket Gamma. Missing or unresolved markets remain pending and produce no write. Before any resolved snapshot is stored, the returned condition ID, slug, horizon, market start/end timestamps, and Up/Down token IDs must match the immutable prediction exactly; any mismatch fails closed before persistence.

Resolved evidence must reuse the existing canonical chain rather than introduce a parallel outcome source:

1. store the official Gamma payload through the existing immutable historical market-snapshot repository and provenance contract;
2. run the existing `official-outcome-v1` canonical label generator under D-017;
3. append the existing immutable live-prediction evaluation;
4. allow the existing paper-execution worker to create any eligible paper settlement from that evaluation on its normal or explicitly bounded paper cycle.

The outcome sync must not rewrite predictions, labels, evaluations, paper orders/fills/settlements, historical snapshots, research records, or live-readiness evidence. Completed evaluations are idempotent and must not trigger repeated Gamma fetching. Historical snapshot digests may carry the established `sha256:` prefix; the evaluation boundary may normalize only that optional prefix while still requiring an exact 64-character lowercase hexadecimal digest. No hash tolerance or weakening is allowed.

The runtime is permitted only in `RESEARCH` with `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`. It exposes no wallet, signing, real-order, promotion, or live-enable path. Its exact-head host acceptance is deliberately non-deploying: it runs the candidate from a detached worktree, requires the existing paper worker and live predictor to be active, may append only canonical official-outcome/evaluation evidence and derivative paper settlements, verifies `/opt/bp` is unchanged, and performs no package installation, migration, service stop/restart, or daemon installation.

Implementation and exact-head CI are complete on the Phase 14 follow-up branch, but production host acceptance and permanent installation of the outcome-sync daemon are **not yet established**. The Master live-gate matrix remains unchanged: overall status is `fail`, Phase 15 remains blocked, and any future controlled live launch still requires every Section 4.3 gate to pass plus separate explicit real-money authorization.
"""

COMPLETED_ADDITIONS = [
    "Phase 14 prospective outcome sync implemented over the canonical Gamma snapshot → official-outcome-v1 label → immutable evaluation chain",
    "Phase 14 snapshot-provenance hash compatibility fixed by normalizing only the established optional sha256: prefix without weakening digest validation",
    "Phase 14 prospective outcome exact-head non-deploying host acceptance helper and hardened research-only service unit implemented",
]

FOLLOWUP_OBJECT = """  \"phase_14_prospective_outcome_sync_followup\": {
    \"root_cause\": \"Prospective predictions had no always-on post-resolution Gamma snapshot ingestion, so canonical labels/evaluations and paper settlements could remain absent.\",
    \"implementation_branch\": \"phase14-prospective-outcome-sync\",
    \"implementation_commit\": \"22663baa8105ca9c5768bc6defb0f51605eb2130\",
    \"implementation_ci_run_id\": 33378572573,
    \"implementation_ci_passed\": true,
    \"implementation_test_count\": 784,
    \"host_acceptance_status\": \"PENDING\",
    \"permanent_install_status\": \"NOT_RUN\",
    \"automatic_promotion\": false,
    \"master_live_gate_mutated\": false,
    \"predictor_service_required_active\": true,
    \"paper_service_required_active\": true,
    \"live_trading_enabled\": false,
    \"max_trade_size_usd\": 0,
    \"max_daily_loss_usd\": 0,
    \"phase15_permitted\": false,
    \"host_acceptance_helper\": \"scripts/deploy/phase14_prospective_outcome_sync_cloudshell.sh\",
    \"service_unit\": \"deploy/bp-prospective-outcomes.service\"
  }"""


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def update_master() -> None:
    text = MASTER.read_text()
    if "# 32. Phase 14 prospective outcome/evaluation sync follow-up" in text:
        return
    if not text.rstrip().endswith(
        "Any Phase 15 transition still requires the complete Master live gate to pass and separate explicit real-money authorization."
    ):
        raise SystemExit("Master tail changed unexpectedly")
    MASTER.write_text(text.rstrip() + MASTER_SECTION + "\n")


def update_state() -> None:
    text = STATE.read_text()
    original_gate = """    \"master_live_gate\": {
      \"historical_pipeline_reproducible\": \"pass\",
      \"no_known_target_leakage\": \"pass\",
      \"time_ordered_splits\": \"pass\",
      \"walk_forward_results_stable_enough\": \"insufficient_evidence\",
      \"sufficiently_large_live_paper_sample_with_uncertainty\": \"insufficient_evidence\",
      \"positive_after_cost_profitability\": \"fail\",
      \"calibration_acceptable\": \"insufficient_evidence\",
      \"risk_limits_and_kill_switch_tested\": \"pass\",
      \"order_execution_and_reconciliation_tested\": \"pass\",
      \"geographic_compliance_eligible\": \"fail\",
      \"explicit_user_live_authorization\": \"fail\"
    },
    \"overall_live_gate\": \"fail\""""
    if original_gate not in text:
        raise SystemExit("Master live-gate snapshot changed unexpectedly")

    text = replace_once(
        text,
        '  "source_of_truth_version": "0.14.1",',
        '  "source_of_truth_version": "0.14.2",',
        label="source_of_truth_version",
    )
    old_updated = next(
        line for line in text.splitlines() if line.startswith('  "updated_at": ')
    )
    timestamp = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    text = replace_once(
        text,
        old_updated,
        f'  "updated_at": "{timestamp}",',
        label="updated_at",
    )

    completed_anchor = (
        '    "Phase 14 prospective-evidence production-host report passed read-only safety/integrity checks with deployed checkout unchanged, reconciliation OK, and real-money interlocks still zero/disabled"\n'
        '  ],\n'
        '  "not_completed": ['
    )
    completed_lines = (
        '    "Phase 14 prospective-evidence production-host report passed read-only safety/integrity checks with deployed checkout unchanged, reconciliation OK, and real-money interlocks still zero/disabled",\n'
        + ",\n".join(f'    "{item}"' for item in COMPLETED_ADDITIONS)
        + '\n  ],\n  "not_completed": ['
    )
    text = replace_once(
        text,
        completed_anchor,
        completed_lines,
        label="completed additions",
    )

    old_next = (
        '    "Continue money-disabled prospective paper predictions, fills, settlements, and official-outcome evaluation to build a sufficiently large sample with uncertainty; the 31 August host report observed zero settled trades and zero evaluations",\n'
        '    "Re-evaluate walk-forward stability, prospective calibration, and after-cost expectancy without retuning on frozen holdouts",'
    )
    new_next = (
        '    "Run exact-head prospective outcome-sync host acceptance on the final governance CI-green head and record actual snapshot/label/evaluation/settlement counts without mutating the Master live gate automatically",\n'
        '    "If the required live predictor service is not active on the host, treat that as a separate deployment gap and build a dedicated safe install cycle rather than starting or installing it inside acceptance",\n'
        '    "After successful outcome-sync host acceptance, rerun the read-only prospective-evidence reporter and reassess sample, after-cost expectancy, calibration, and reconciliation without inventing thresholds or retuning frozen holdouts",'
    )
    text = replace_once(text, old_next, new_next, label="next actions")

    if '"phase_14_prospective_outcome_sync_followup"' not in text:
        final_anchor = """    \"runbook\": \"docs/PHASE-14-PROSPECTIVE-EVIDENCE.md\"
  }
}
"""
        replacement = """    \"runbook\": \"docs/PHASE-14-PROSPECTIVE-EVIDENCE.md\"
  },
""" + FOLLOWUP_OBJECT + "\n}\n"
        text = replace_once(text, final_anchor, replacement, label="followup object")

    if original_gate not in text:
        raise SystemExit("Master live-gate snapshot was mutated")
    if '  "live_trading_enabled": false,' not in text:
        raise SystemExit("live trading safety state changed")
    STATE.write_text(text)


def update_changelog_rounding() -> None:
    text = CHANGELOG.read_text()
    old_5m = (
        "Across six ordinary folds it evaluated 144 OOS markets at 0.8263888888888888 accuracy, 0.33444472531803504 log loss, and 0.10629913368055556 Brier score. Observed selected-side best-ask execution coverage was 0.4305555555555556 and gross P&L before costs was -1.4649999999999994. The untouched final holdout was 0.8333333333333334 accurate, with 0.8333333333333334 execution coverage and gross P&L -0.2599999999999999."
    )
    new_5m = (
        "Across six ordinary folds it evaluated 144 OOS markets at 0.8264 accuracy, 0.3344 log loss, and 0.1063 Brier score. Observed selected-side best-ask execution coverage was 0.4306 and gross P&L before costs was -1.465. The untouched final holdout was 0.8333 accurate, with 0.8333 execution coverage and gross P&L -0.26."
    )
    old_15m = (
        "Across six ordinary folds it evaluated 48 OOS markets at 0.9791666666666666 accuracy, 0.10683797056431854 log loss, and 0.02922243229166666 Brier score. Observed-ask execution coverage was only 0.20833333333333334 and gross P&L before costs was +0.381."
    )
    new_15m = (
        "Across six ordinary folds it evaluated 48 OOS markets at 0.9792 accuracy, 0.1068 log loss, and 0.0292 Brier score. Observed-ask execution coverage was only 0.2083 and gross P&L before costs was +0.381."
    )
    if old_5m in text:
        text = text.replace(old_5m, new_5m, 1)
    if old_15m in text:
        text = text.replace(old_15m, new_15m, 1)
    CHANGELOG.write_text(text)


def main() -> None:
    update_master()
    update_state()
    update_changelog_rounding()


if __name__ == "__main__":
    main()
