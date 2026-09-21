from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = (
    "docs/superpowers/specs/"
    "2026-09-14-phase-14-v3-gate-b-successor-preregistration.md"
)
PLAN_PATH = (
    "docs/superpowers/plans/"
    "2026-09-14-phase-14-v3-gate-b-successor-preregistration.md"
)
EVIDENCE_PATH = (
    "docs/evidence/phase-14-v3-gate-b-successor-preregistration-20260914.json"
)
SUCCESSOR_PLAN_VERSION = "v3-gate-b-preregister-v2"
SUCCESSOR_EPOCH_START = "2026-09-16T13:45:00Z"
SUCCESSOR_EPOCH_END = "2026-09-19T13:45:00Z"
RETIRED_PLAN_VERSION = "v3-gate-b-preregister-v1"
RUNTIME_CHECKPOINT = "659d9524fe8bfeba182b7cf7c8d9b664280f7562"
RUNTIME_CI_RUN = 34841954823


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_successor_preregistration_evidence_freezes_clean_future_epoch() -> None:
    evidence = json.loads(_text(EVIDENCE_PATH))

    assert evidence["status"] == "APPROVED_SUCCESSOR_PREREGISTRATION_DESIGN"
    retired = evidence["retired_attempt"]
    assert retired["research_plan_version"] == RETIRED_PLAN_VERSION
    assert retired["policy_selection_executable"] is False
    assert retired["diagnosis_identity_recoverable"] is False
    assert retired["epoch_data_policy"] == "engineering_coverage_only"

    successor = evidence["successor"]
    assert successor["research_plan_version"] == SUCCESSOR_PLAN_VERSION
    assert successor["dataset_version"] == "supervised-core-v3-btc-native-v1"
    assert successor["feature_version"] == "core-v3-btc-native"
    assert successor["label_version"] == "official-outcome-v1"
    assert successor["epoch_start"] == SUCCESSOR_EPOCH_START
    assert successor["epoch_end"] == SUCCESSOR_EPOCH_END
    assert successor["ordinary_fold_count"] == 5
    assert successor["final_holdout_hours"] == 12
    assert successor["eligibility"] == (
        "market_start_at >= epoch_start AND market_start_at < epoch_end"
    )
    assert successor["pre_epoch_markets_eligible"] is False
    assert successor["diagnosis_manifest_runtime_required"] is False
    assert successor["consumed_v2_manifest_runtime_required"] is False
    assert successor["historical_contamination_evidence_preserved"] is True
    assert successor["readiness_outcome_blind"] is True
    assert successor["planning_feature_only"] is True
    assert successor["final_holdout_separate_authorization_required"] is True

    safety = evidence["safety"]
    assert safety["training_authorized"] is False
    assert safety["final_holdout_access_authorized"] is False
    assert safety["model_activation_authorized"] is False
    assert safety["production_mutation_authorized"] is False
    assert safety["live_trading_enabled"] is False
    assert safety["max_trade_size_usd"] == 0
    assert safety["max_daily_loss_usd"] == 0


def test_successor_spec_and_plan_preserve_frozen_search_contract() -> None:
    spec = _text(SPEC_PATH)
    plan = _text(PLAN_PATH)

    for content in (spec, plan):
        assert RETIRED_PLAN_VERSION in content
        assert SUCCESSOR_PLAN_VERSION in content
        assert SUCCESSOR_EPOCH_START in content
        assert SUCCESSOR_EPOCH_END in content
        assert "core-v3-btc-native" in content
        assert "official-outcome-v1" in content
        assert "24h" in content
        assert "6h" in content
        assert "12h" in content
        assert "five ordinary folds" in content.lower()
        assert "final holdout" in content.lower()
        assert "outcome-blind" in content.lower()
        assert "feature-only" in content.lower()
        assert "pre-epoch" in content.lower()
        assert "diagnosis" in content.lower()
        assert "48" in content
        assert "no model fitting" in content.lower()
        assert "no final-holdout access" in content.lower()


def test_successor_runtime_source_truth_records_completed_consumed_holdout() -> None:
    state = json.loads(_text("PROJECT_STATE.json"))
    assert state["source_of_truth_version"] == "0.14.158"
    successor = state["phase_14_btc_first_v3_gate_a"]["successor_gate_b"]
    assert successor["research_plan_version"] == SUCCESSOR_PLAN_VERSION
    assert successor["epoch_start"] == SUCCESSOR_EPOCH_START
    assert successor["epoch_end"] == SUCCESSOR_EPOCH_END
    assert successor["eligibility"] == (
        "market_start_at >= epoch_start AND market_start_at < epoch_end"
    )
    assert successor["runtime_implementation_checkpoint"] == RUNTIME_CHECKPOINT
    assert successor["runtime_ci_run_id"] == RUNTIME_CI_RUN
    assert successor["runtime_ci_passed"] is True
    assert successor["historical_manifests_runtime_required"] is False
    assert successor["retired_v1_policy_selection_executable"] is False
    assert successor["retired_v1_epoch_data_policy"] == "engineering_coverage_only"
    assert successor["posthoc_diagnosis_reconstruction_allowed"] is False
    assert successor["consumed_v2_historical_exclusion_count"] == 48
    assert successor["readiness_performed"] is True
    assert successor["plan_performed"] is True
    assert successor["training_performed"] is True
    assert successor["final_holdout_access_performed"] is True
    assert successor["final_holdout_evaluated"] is True
    assert successor["final_holdout_market_count"] == 144
    assert successor["final_holdout_reusable"] is False
    assert successor["automatic_promotion"] is False
    assert successor["activation_performed"] is False

    start = _text("START-HERE.md")
    build = _text("docs/BUILD-ORDER.md")
    master = _text("docs/MASTER-SOURCE-OF-TRUTH.md")
    decisions = _text("docs/DECISION-LOG.md")
    changelog = _text("docs/CHANGELOG.md")

    for content in (start, master, decisions, changelog):
        assert SUCCESSOR_PLAN_VERSION in content
        assert SUCCESSOR_EPOCH_START in content
        assert SUCCESSOR_EPOCH_END in content

    next_task = start.split("## Immediate next task", 1)[1].lower()
    assert "frozen v3 paper trading is now **production pass and active**" in next_task
    assert "124627e15cab3997b8abe54ec5237450d976ab5682953f45a1399a76b6dae0e7" in next_task
    assert "v4 regime-aware forward collector remains active" in next_task
    assert "prospective observation only" in next_task

    build_next = build.split("## Immediate next action", 1)[1].lower()
    assert "frozen v3 paper trading is production pass and active" in build_next
    assert "min_edge=0.075" in build_next
    assert "$100 virtual starting cash" in build_next
    assert "$5 virtual target notional" in build_next
    assert "v4 regime-aware feature collection" in build_next

    assert "## D-048 —" in decisions
    assert "## D-049 —" in decisions
    assert "## D-050 —" in decisions
    assert "## D-051 —" in decisions
    assert "## D-052 —" in decisions
    assert "## D-053 —" in decisions
    assert "## 0.14.145 — 20 September 2026" in changelog
    assert RUNTIME_CHECKPOINT in changelog
    assert str(RUNTIME_CI_RUN) in changelog
