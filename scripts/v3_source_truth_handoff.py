from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COVERAGE = "32c283a7769681ebe5b2e0d1fe255ad6c38aa5b0301303f8fe86f4e7b2278ffb"
DESIGN = "c9e179c91ea990ca4a25a13f69fc5932811fb32a"
IMPLEMENTATION = "39a887e138398215ac97dc45f8099e3515a90fe2"
IMPLEMENTATION_CI = 34775202056
EPOCH_START = "2026-09-13T13:45:00Z"
EPOCH_END = "2026-09-16T13:45:00Z"
GATE_A_EVIDENCE = "docs/evidence/phase-14-v3-gate-a-production-20260913.json"


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def patch_project_state() -> None:
    path = ROOT / "PROJECT_STATE.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["source_of_truth_version"] == "0.14.136"
    v3 = state["phase_14_btc_first_v3_gate_a"]
    assert v3["implementation_status"] == "REPOSITORY_IMPLEMENTED_AWAITING_COVERAGE_ACCEPTANCE"
    assert v3["feature_version"] == "core-v3-btc-native"

    state["source_of_truth_version"] = "0.14.138"
    v3.update(
        {
            "implementation_status": "GATE_A_PASS_V3_GATE_B_PREREGISTRATION_FROZEN",
            "merged_main_head": "8b2d983ec75692de7ed39043c4a0e19dcf691942",
            "production_materialization_performed": True,
            "production_materialization_passed": True,
            "coverage_acceptance": "PASS",
            "coverage_start": EPOCH_START,
            "coverage_end": "2026-09-13T15:10:00Z",
            "target_markets": 17,
            "feature_rows_before": 0,
            "feature_rows_inserted": 68,
            "feature_rows_after": 68,
            "coverage_input_sha256": COVERAGE,
            "future_cutoff_violation_count": 0,
            "polymarket_predictor_key_count": 0,
            "evidence": GATE_A_EVIDENCE,
            "preregistration_frozen": True,
            "preregistration_design_commit": DESIGN,
            "preregistration_issue": 193,
            "preregistration_implementation_head": IMPLEMENTATION,
            "preregistration_ci_run_id": IMPLEMENTATION_CI,
            "preregistration_ci_passed": True,
            "research_plan_version": "v3-gate-b-preregister-v1",
            "prospective_epoch_start": EPOCH_START,
            "prospective_epoch_end": EPOCH_END,
            "ordinary_fold_count": 5,
            "final_holdout_hours": 12,
            "v2_adaptive_training_paused": True,
            "training_performed": False,
            "production_migration_performed": False,
            "model_activation_performed": False,
            "paper_activation_performed": False,
            "final_holdout_access_performed": False,
            "automatic_promotion": False,
            "live_trading_enabled": False,
            "max_trade_size_usd": 0,
            "max_daily_loss_usd": 0,
            "next_action": (
                "After the frozen prospective epoch completes at "
                f"{EPOCH_END}, run outcome-blind V3 Gate B readiness with the two "
                "frozen historical exclusion manifests; if ready, write the fixed "
                "five-fold feature-only plan. No labeled modeling or final-holdout "
                "access is part of this action."
            ),
        }
    )
    _write_json(path, state)


def patch_handoff() -> None:
    path = ROOT / "docs/evidence/phase-14-v3-preregistration-handoff-20260913.json"
    handoff = json.loads(path.read_text(encoding="utf-8"))
    assert handoff["status"] == "GATE_A_PASS_V3_GATE_B_PREREGISTRATION_FROZEN"
    prereg = handoff["preregistration"]
    assert prereg["design_commit"] == DESIGN
    prereg["implementation_head"] = IMPLEMENTATION
    prereg["implementation_ci_run_id"] = IMPLEMENTATION_CI
    prereg["implementation_ci_passed"] = True
    prereg["review_fixes"] = [
        "complete_future_search_contract_frozen_and_hash_bound",
        "prospective_epoch_exclusion_intersection_rejected",
        "canonical_source_truth_restored",
    ]
    _write_json(path, handoff)


def patch_master() -> None:
    path = ROOT / "docs/MASTER-SOURCE-OF-TRUTH.md"
    text = path.read_text(encoding="utf-8")
    heading = (
        "## Phase 14 BTC-first V3 Gate A production acceptance + "
        "Gate B preregistration — 13 September 2026"
    )
    assert heading not in text
    text += f"""

{heading}

This section supersedes the earlier Phase 14 V3 wording that left Gate A awaiting production coverage acceptance. The repository implementation remains `core-v3-btc-native` with `official-outcome-v1`, a 300-second horizon, and feature offsets 60/120/180/240 seconds.

**Gate A production coverage: PASS.** The accepted evidence is `{GATE_A_EVIDENCE}`: 17 markets / 68 feature rows from `{EPOCH_START}` through `2026-09-13T15:10:00Z`, coverage input SHA-256 `{COVERAGE}`, zero future-cutoff violations, zero Polymarket predictor keys, and complete current-state availability for Coinbase spot, Bybit spot, and Bybit linear. The accepted step performed no model training, model activation, paper activation, final-holdout access, automatic promotion, or live-trading change.

**Gate B preregistration is frozen.** The approved design is commit `{DESIGN}` and implementation follow-up is Issue #193. The audited implementation checkpoint is `{IMPLEMENTATION}`; CI run `{IMPLEMENTATION_CI}` passed the full Python suite, lint, deployment-asset validation, health check, dashboard test/typecheck/build, and the PR smoke workflows. The future search contract is frozen and hash-bound, including the predictor family, forecast candidates, validation selection/tie-break rules, calibration candidates, offset candidates, fee/slippage assumptions, edge grid/no-trade candidate, freshness limit, and validation trade/PnL gates.

The two historical contamination sets remain required as hash-bound `diagnosis` and `consumed_v2_final_holdout` exclusion manifests before any labeled research work. The readiness implementation rejects any supplied exclusion condition ID that occurs inside the frozen prospective V3 epoch, preventing post-hoc removal of prospective markets. No historical cohort IDs are invented by this handoff.

The prospective epoch is fixed at `{EPOCH_START}` through `{EPOCH_END}`. After the epoch completes, the only authorized next sequence is **outcome-blind readiness → fixed five-fold feature-only plan**. No labeled modeling, model fitting, final-holdout label access/evaluation, model activation, automatic promotion, production migration, paper activation, or live-trading change is authorized by this handoff.
"""
    path.write_text(text, encoding="utf-8")


def patch_build_order() -> None:
    path = ROOT / "docs/BUILD-ORDER.md"
    text = path.read_text(encoding="utf-8")
    marker = "## Immediate next action"
    assert text.count(marker) == 1
    prefix, old_next = text.split(marker, 1)
    assert "Collect outcome-blind `core-v3-btc-native` coverage" in old_next
    new_next = f"""{marker}

Gate A production coverage for `core-v3-btc-native` is accepted PASS under coverage input SHA-256 `{COVERAGE}`. The Gate B preregistration design is frozen at `{DESIGN}` and the prospective epoch ends at `{EPOCH_END}`.

After `{EPOCH_END}`, run the read-only **outcome-blind readiness** command using the two frozen historical exclusion manifests (`diagnosis` and `consumed_v2_final_holdout`). If and only if readiness passes, write the fixed five-fold **feature-only plan**. No labeled modeling, no final-holdout access, no model activation, and no production mutation is part of this next action.

Preserve the paused V2 adaptive path and all V1/V2 evidence unchanged.
"""
    path.write_text(prefix + new_next, encoding="utf-8")


def patch_decisions() -> None:
    path = ROOT / "docs/DECISION-LOG.md"
    text = path.read_text(encoding="utf-8")
    assert "## D-046 — BTC-first V3 Gate A production coverage accepted" not in text
    assert "## D-047 — V3 Gate B preregistration frozen" not in text
    text += f"""

## D-046 — BTC-first V3 Gate A production coverage accepted

**Status:** Active

Production materialization/coverage for `core-v3-btc-native` is accepted PASS from `{GATE_A_EVIDENCE}`: 17 markets / 68 rows, coverage input SHA-256 `{COVERAGE}`, zero future-cutoff violations, zero Polymarket predictor keys, and complete current-state availability across the three BTC sources. This acceptance did not perform model training, model activation, final-holdout access, automatic promotion, or live-trading change.

## D-047 — V3 Gate B preregistration frozen

**Status:** Active

Freeze the approved V3 Gate B preregistration design at `{DESIGN}` and implementation checkpoint `{IMPLEMENTATION}` (Issue #193, CI `{IMPLEMENTATION_CI}`). The prospective epoch is `{EPOCH_START}` through `{EPOCH_END}`, with exactly five ordinary folds and a separate 12-hour final reserved window. The complete future search contract is frozen and hash-bound. Historical `diagnosis` and `consumed_v2_final_holdout` exclusion manifests remain mandatory, and supplied exclusions may not intersect the prospective V3 epoch. After epoch completion, proceed only through outcome-blind readiness and then, if ready, a fixed feature-only plan. No labeled modeling or final-holdout access is authorized here.
"""
    path.write_text(text, encoding="utf-8")


def patch_changelog() -> None:
    path = ROOT / "docs/CHANGELOG.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Changelog\n")
    assert "## 0.14.138 — 13 September 2026" not in text
    assert "## 0.14.137 — 13 September 2026" not in text
    entries = f"""
## 0.14.138 — 13 September 2026

- Froze the V3 Gate B preregistration from approved design `{DESIGN}` through implementation checkpoint `{IMPLEMENTATION}` / CI `{IMPLEMENTATION_CI}`. The full future search contract is now frozen and hash-bound, prospective-epoch exclusions are rejected, and the next sequence after `{EPOCH_END}` is outcome-blind readiness followed only by a fixed five-fold feature-only plan. **No training** or final-holdout access is authorized. Gate A coverage remains bound to `{COVERAGE}`.

## 0.14.137 — 13 September 2026

- Accepted `core-v3-btc-native` Gate A production coverage as PASS from `{GATE_A_EVIDENCE}`: 17 markets / 68 rows, coverage input SHA-256 `{COVERAGE}`, zero future-cutoff violations, zero Polymarket predictor keys, and no training/model activation/final-holdout access. The frozen prospective epoch ends at `{EPOCH_END}`.
"""
    path.write_text("# Changelog\n" + entries + text[len("# Changelog\n"):], encoding="utf-8")


def main() -> None:
    patch_project_state()
    patch_handoff()
    patch_master()
    patch_build_order()
    patch_decisions()
    patch_changelog()


if __name__ == "__main__":
    main()
