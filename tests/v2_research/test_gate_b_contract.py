from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert

from bp_engine.modeling.models import SupervisedRow
from bp_engine.storage import schema
from bp_engine.v2_research.config import (
    FROZEN_COVERAGE_INPUT_SHA256,
    FROZEN_FRESHNESS_CANDIDATES_SECONDS,
    FROZEN_INCLUDE_NO_TRADE,
    V2_DATASET_VERSION,
)
from bp_engine.v2_research.models import GateBPlanConfig, GateBResearchConfig
from bp_engine.v2_research.plan import assess_gate_b_readiness, build_gate_b_plan
from bp_engine.v2_research.policy import edge_decision_v2
from bp_engine.v2_research.service import (
    GateBResearchIntegrityError,
    evaluate_gate_b_holdout,
    prepare_gate_b,
)

ROOT_START = datetime(2026, 9, 2, 12, 20, tzinfo=UTC)
OFFSETS = (60, 120, 180, 240)


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    schema.metadata.create_all(engine)
    return engine


def _feature(condition_index: int, offset: int) -> dict[str, object]:
    market_start = ROOT_START + timedelta(minutes=5 * condition_index)
    feature_at = market_start + timedelta(seconds=offset)
    up_price = 0.72 if condition_index % 2 else 0.28
    return {
        "condition_id": f"condition-{condition_index:03d}",
        "slug": f"btc-updown-5m-{condition_index:03d}",
        "horizon_seconds": 300,
        "market_start_at": market_start,
        "market_end_at": market_start + timedelta(minutes=5),
        "feature_at": feature_at,
        "feature_offset_seconds": offset,
        "feature_version": "core-v2-last-trade",
        "features": {
            "seconds_elapsed": offset,
            "seconds_remaining": 300 - offset,
            "fraction_elapsed": offset / 300,
            "horizon_seconds": 300,
            "pm_up_last_trade_price": up_price,
            "pm_up_last_trade_source_age_s": 1.2,
            "pm_up_last_trade_availability_age_s": 0.8,
            "pm_down_last_trade_price": 1.0 - up_price,
            "pm_down_last_trade_source_age_s": 1.4,
            "pm_down_last_trade_availability_age_s": 0.9,
            "pm_up_best_bid": max(up_price - 0.03, 0.01),
            "pm_up_best_ask": min(up_price + 0.01, 0.99),
            "pm_up_mid": up_price - 0.01,
            "pm_up_spread": 0.04,
            "pm_up_bid_depth": 10.0,
            "pm_up_ask_depth": 10.0,
            "pm_up_book_imbalance": 0.0,
            "pm_down_best_bid": max((1.0 - up_price) - 0.03, 0.01),
            "pm_down_best_ask": min((1.0 - up_price) + 0.01, 0.99),
            "pm_down_mid": (1.0 - up_price) - 0.01,
            "pm_down_spread": 0.04,
            "pm_down_bid_depth": 10.0,
            "pm_down_ask_depth": 10.0,
            "pm_down_book_imbalance": 0.0,
        },
        "missing_flags": {
            "pm_up_last_trade_missing": False,
            "pm_down_last_trade_missing": False,
            "pm_up_book_missing": False,
            "pm_up_book_stale": False,
            "pm_down_book_missing": False,
            "pm_down_book_stale": False,
        },
        "source_cutoffs": {
            "pm_up_last_trade_source": (feature_at - timedelta(seconds=1.2)).isoformat(),
            "pm_up_last_trade_received": (feature_at - timedelta(seconds=0.8)).isoformat(),
            "pm_down_last_trade_source": (feature_at - timedelta(seconds=1.4)).isoformat(),
            "pm_down_last_trade_received": (feature_at - timedelta(seconds=0.9)).isoformat(),
            "pm_up_book_state": (feature_at - timedelta(seconds=0.5)).isoformat(),
            "pm_down_book_state": (feature_at - timedelta(seconds=0.5)).isoformat(),
        },
        "input_fingerprint": f"{condition_index % 10}" * 64,
        "feature_hash": f"{(condition_index + offset) % 10}" * 64,
        "generated_at": market_start + timedelta(minutes=6),
    }


def _label(condition_index: int) -> dict[str, object]:
    market_start = ROOT_START + timedelta(minutes=5 * condition_index)
    outcome = "Up" if condition_index % 2 else "Down"
    return {
        "condition_id": f"condition-{condition_index:03d}",
        "gamma_market_id": f"gamma-{condition_index:03d}",
        "slug": f"btc-updown-5m-{condition_index:03d}",
        "horizon_seconds": 300,
        "market_start_at": market_start,
        "market_end_at": market_start + timedelta(minutes=5),
        "official_outcome": outcome,
        "start_reference": None,
        "end_reference": None,
        "resolution_source": "chainlink",
        "rules_hash": f"sha256:rules-{condition_index}",
        "label_source": "polymarket_gamma_snapshot",
        "label_version": "official-outcome-v1",
        "source_snapshot_sha256": f"sha256:snapshot-{condition_index}",
        "source_observed_at": market_start + timedelta(minutes=6),
        "generated_at": market_start + timedelta(minutes=7),
    }


def _plan_config() -> GateBPlanConfig:
    return GateBPlanConfig(
        train_duration=timedelta(minutes=20),
        validation_duration=timedelta(minutes=10),
        test_duration=timedelta(minutes=10),
        step_duration=timedelta(minutes=10),
        final_holdout_duration=timedelta(minutes=10),
        embargo_markets=0,
        min_train_markets=2,
        min_validation_markets=1,
        min_test_markets=2,
    )


def _research_config() -> GateBResearchConfig:
    return GateBResearchConfig(
        fee_rate=0.0,
        slippage_buffer=0.0,
        min_edge_grid=(0.0,),
        min_validation_trades=1,
        min_train_eligible_markets=2,
        min_validation_eligible_markets=1,
    )


def test_gate_b_defaults_reuse_accepted_phase8_phase9_search_geometry() -> None:
    plan = GateBPlanConfig()
    research = GateBResearchConfig()

    assert plan.train_duration == timedelta(hours=8)
    assert plan.validation_duration == timedelta(hours=2)
    assert plan.test_duration == timedelta(hours=2)
    assert plan.step_duration == timedelta(hours=2)
    assert plan.final_holdout_duration == timedelta(hours=2)
    assert plan.embargo_markets == 1
    assert plan.min_train_markets == 24
    assert plan.min_validation_markets == 6
    assert plan.min_test_markets == 6

    assert research.fee_rate == 0.07
    assert research.slippage_buffer == 0.01
    assert research.min_edge_grid == (
        0.0,
        0.01,
        0.02,
        0.03,
        0.05,
        0.075,
        0.10,
        0.15,
    )


def test_preregistration_constants_match_immutable_evidence() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "evidence"
        / "phase-14-v2-freshness-preregistration-20260909.json"
    )
    evidence = json.loads(path.read_text(encoding="utf-8"))
    prereg = evidence["freshness_preregistration"]

    assert FROZEN_COVERAGE_INPUT_SHA256 == evidence["coverage_input_sha256"]
    assert list(FROZEN_FRESHNESS_CANDIDATES_SECONDS) == prereg[
        "max_last_trade_age_seconds_candidates"
    ]
    assert FROZEN_INCLUDE_NO_TRADE is prereg["include_no_trade"]
    assert max(FROZEN_FRESHNESS_CANDIDATES_SECONDS) <= 10
    assert V2_DATASET_VERSION == "supervised-core-v2-last-trade-v1"


def test_gate_b_readiness_is_feature_only_repeatable_and_reports_geometry() -> None:
    engine = _engine()
    rows = [_feature(index, offset) for index in range(10) for offset in OFFSETS]
    with engine.begin() as connection:
        connection.execute(insert(schema.market_features), rows)
        first = assess_gate_b_readiness(
            connection, _plan_config(), _research_config()
        )
        second = assess_gate_b_readiness(
            connection, _plan_config(), _research_config()
        )

    assert first == second
    assert first["ready"] is False
    assert first["labels_read"] is False
    assert first["plan_artifact_written"] is False
    assert first["selection_artifact_written"] is False
    assert first["holdout_touched"] is False
    assert first["minimum_contiguous_epoch_seconds"] == 70 * 60
    assert first["required_ordinary_folds"] == 3
    assert first["market_count"] == 10
    assert first["blocking_reason"]
    assert first["analysis_start_at"] is None


def test_gate_b_readiness_reports_earliest_viable_epoch_without_freezing_plan() -> None:
    engine = _engine()
    rows = [_feature(index, offset) for index in range(16) for offset in OFFSETS]
    with engine.begin() as connection:
        connection.execute(insert(schema.market_features), rows)
        report = assess_gate_b_readiness(
            connection, _plan_config(), _research_config()
        )

    assert report["ready"] is True
    assert report["labels_read"] is False
    assert report["analysis_start_at"] == ROOT_START.isoformat()
    assert report["eligible_fold_count"] == 4
    assert report["final_holdout_market_count"] == 2
    assert report["plan_artifact_written"] is False
    assert report["selection_artifact_written"] is False
    assert report["holdout_touched"] is False
    assert report["would_plan_sha256"]
    assert report["blocking_reason"] is None


def test_default_gate_b_readiness_geometry_requires_eighteen_hours() -> None:
    plan = GateBPlanConfig()
    required_seconds = (
        plan.train_duration
        + plan.validation_duration
        + plan.test_duration
        + (2 * plan.step_duration)
        + plan.final_holdout_duration
    ).total_seconds()

    assert required_seconds == 18 * 60 * 60


def test_gate_b_plan_is_feature_only_deterministic_and_holds_out_latest_markets() -> None:
    engine = _engine()
    rows = [_feature(index, offset) for index in range(16) for offset in OFFSETS]
    with engine.begin() as connection:
        connection.execute(insert(schema.market_features), rows)
        first = build_gate_b_plan(connection, _plan_config(), _research_config())
        second = build_gate_b_plan(connection, _plan_config(), _research_config())

    assert first == second
    assert first["feature_version"] == "core-v2-last-trade"
    assert first["market_count"] == 16
    assert len(first["folds"]) == 4
    assert first["final"]["holdout_condition_ids"] == [
        "condition-014",
        "condition-015",
    ]
    assert first["coverage_input_sha256"] == FROZEN_COVERAGE_INPUT_SHA256
    assert first["freshness_candidates_seconds"] == [1, 2, 5, 10]
    assert first["include_no_trade"] is True
    assert first["labels_read"] is False


def test_gate_b_plan_moves_past_sparse_feature_only_prefix_without_skipping_folds() -> None:
    engine = _engine()
    rows = [
        _feature(index, offset)
        for index in range(20)
        if index != 6
        for offset in OFFSETS
    ]
    with engine.begin() as connection:
        connection.execute(insert(schema.market_features), rows)
        plan = build_gate_b_plan(connection, _plan_config(), _research_config())

    assert plan["analysis_start_at"] == (
        ROOT_START + timedelta(minutes=10)
    ).isoformat()
    assert plan["excluded_prefix_condition_ids"] == [
        "condition-000",
        "condition-001",
    ]
    assert plan["analysis_start_attempt_count"] == 2
    assert len(plan["folds"]) == 5
    for current, following in zip(plan["folds"], plan["folds"][1:], strict=False):
        assert current["test"]["end"] == following["validation"]["end"]
        assert following["test"]["start"] == current["test"]["end"]


def test_prepare_gate_b_does_not_require_final_holdout_labels() -> None:
    engine = _engine()
    rows = [_feature(index, offset) for index in range(16) for offset in OFFSETS]
    with engine.begin() as connection:
        connection.execute(insert(schema.market_features), rows)
        # Intentionally omit labels for the two final-holdout markets.
        connection.execute(
            insert(schema.market_labels), [_label(index) for index in range(14)]
        )
        plan = build_gate_b_plan(connection, _plan_config(), _research_config())
        report = prepare_gate_b(
            connection,
            plan=plan,
            config=_research_config(),
        )

    assert report["dataset_version"] == V2_DATASET_VERSION
    assert report["holdout_labels_read"] is False
    assert report["final"]["holdout_condition_ids"] == [
        "condition-014",
        "condition-015",
    ]
    assert "holdout_metrics" not in report["final"]
    assert report["coverage_input_sha256"] == FROZEN_COVERAGE_INPUT_SHA256
    assert report["freshness_candidates_seconds"] == [1, 2, 5, 10]

    with engine.begin() as connection:
        with pytest.raises(
            GateBResearchIntegrityError,
            match="cannot change the feature-only research config",
        ):
            prepare_gate_b(
                connection,
                plan=plan,
                config=GateBResearchConfig(
                    fee_rate=0.01,
                    slippage_buffer=0.0,
                    min_edge_grid=(0.0,),
                    min_validation_trades=1,
                    min_train_eligible_markets=2,
                    min_validation_eligible_markets=1,
                ),
            )


def test_v2_policy_stale_or_missing_last_trade_is_explicit_no_trade() -> None:
    row = SupervisedRow(
        condition_id="condition-x",
        slug="btc-updown-5m-x",
        horizon_seconds=300,
        market_start_at=ROOT_START,
        market_end_at=ROOT_START + timedelta(minutes=5),
        feature_at=ROOT_START + timedelta(seconds=60),
        feature_offset_seconds=60,
        predictors={
            "pm_up_last_trade_price": 0.70,
            "pm_up_last_trade_availability_age_s": 5.1,
            "pm_up_last_trade_source_age_s": 6.0,
            "missing__pm_up_last_trade_missing": 0.0,
            "pm_up_best_bid": 0.64,
            "pm_up_best_ask": 0.66,
            "missing__pm_up_book_missing": 0.0,
            "missing__pm_up_book_stale": 0.0,
            "pm_down_best_bid": 0.34,
            "pm_down_best_ask": 0.36,
            "missing__pm_down_book_missing": 0.0,
            "missing__pm_down_book_stale": 0.0,
        },
        target=1,
        feature_hash="a" * 64,
        input_fingerprint="b" * 64,
    )

    stale = edge_decision_v2(
        row,
        calibrated_probability_up=0.70,
        max_last_trade_age_seconds=5,
        fee_rate=0.07,
        slippage_buffer=0.01,
        min_edge=0.0,
    )
    assert stale.trade is False
    assert stale.executable is False
    assert stale.reason == "last_trade_stale"

    missing_predictors = dict(row.predictors)
    missing_predictors["pm_up_last_trade_price"] = None
    missing_predictors["missing__pm_up_last_trade_missing"] = 1.0
    missing = row.__class__(**{**row.__dict__, "predictors": missing_predictors})
    decision = edge_decision_v2(
        missing,
        calibrated_probability_up=None,
        max_last_trade_age_seconds=10,
        fee_rate=0.07,
        slippage_buffer=0.01,
        min_edge=0.0,
    )
    assert decision.trade is False
    assert decision.executable is False
    assert decision.reason == "last_trade_missing"


def test_holdout_evaluation_is_separate_and_bound_to_frozen_selection() -> None:
    engine = _engine()
    rows = [_feature(index, offset) for index in range(16) for offset in OFFSETS]
    with engine.begin() as connection:
        connection.execute(insert(schema.market_features), rows)
        connection.execute(insert(schema.market_labels), [_label(index) for index in range(14)])
        plan = build_gate_b_plan(connection, _plan_config(), _research_config())
        selection = prepare_gate_b(
            connection,
            plan=plan,
            config=_research_config(),
        )
        connection.execute(insert(schema.market_labels), [_label(14), _label(15)])
        holdout = evaluate_gate_b_holdout(
            connection,
            plan=plan,
            selection=selection,
        )

    assert selection["holdout_labels_read"] is False
    assert selection["holdout_evaluated"] is False
    assert holdout["holdout_labels_read"] is True
    assert holdout["holdout_evaluated_once"] is True
    assert holdout["holdout_condition_ids"] == ["condition-014", "condition-015"]
    assert holdout["gate_b_authorized"] is False
    assert holdout["automatic_promotion"] is False

    changed = dict(selection)
    changed["final"] = {**selection["final"], "holdout_condition_ids": ["condition-999"]}
    with engine.begin() as connection:
        with pytest.raises(
            GateBResearchIntegrityError, match="selection_sha256 mismatch"
        ):
            evaluate_gate_b_holdout(
                connection,
                plan=plan,
                selection=changed,
            )


def test_gate_b_package_has_no_v1_probability_fallback_or_database_write_path() -> None:
    from bp_engine.v2_research import cli, policy, service

    source = "\n".join(
        (
            inspect.getsource(policy),
            inspect.getsource(service),
            inspect.getsource(cli),
        )
    ).lower()
    for forbidden in (
        '"pm_up_price"',
        "marketpricebaseline",
        "priorbaseline",
        "modeltrainingrunrepository",
        "backtestrunrepository",
        "calibrationedgerunrepository",
        "metadata.create_all",
        ".store(",
        "live_trading_enabled=true",
        "automatic_promotion=true",
    ):
        assert forbidden not in source


def test_gate_b_artifact_paths_are_no_clobber(tmp_path: Path) -> None:
    from bp_engine.v2_research.cli import _write_exclusive

    destination = tmp_path / "holdout.json"
    _write_exclusive(str(destination), {"first": True})
    assert json.loads(destination.read_text(encoding="utf-8")) == {"first": True}

    with pytest.raises(FileExistsError):
        _write_exclusive(str(destination), {"first": False})

    assert json.loads(destination.read_text(encoding="utf-8")) == {"first": True}


def test_project_state_locks_gate_b_engineering_boundary() -> None:
    state_path = Path(__file__).resolve().parents[2] / "PROJECT_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    checkpoint = state["phase_14_market_price_v2_followup"]

    assert checkpoint["v2_gate_b_research_engineering_status"] == (
        "MERGED_MAIN_ENGINEERING_READY_NOT_RUN"
    )
    assert checkpoint["v2_gate_b_research_pr"] == 154
    assert checkpoint["v2_gate_b_research_final_branch_head"] == (
        "777d3dd1cc4bcf9b18cf6bfb929ffb16d83cc56e"
    )
    assert checkpoint["v2_gate_b_research_merge_commit"] == (
        "09cc6a10ae5ae675a3e2dd55ad12ab3108504551"
    )
    assert checkpoint["v2_gate_b_research_post_merge_ci_run_id"] == 34361755746
    assert checkpoint["v2_gate_b_research_post_merge_ci_passed"] is True
    assert checkpoint["v2_gate_b_research_post_merge_test_count"] == 1014
    assert checkpoint["v2_gate_b_research_production_run_performed"] is False
    assert checkpoint["v2_gate_b_research_coverage_input_sha256"] == (
        FROZEN_COVERAGE_INPUT_SHA256
    )
    assert checkpoint["v2_gate_b_research_freshness_candidates_seconds"] == [
        1,
        2,
        5,
        10,
    ]
    assert checkpoint["v2_gate_b_research_default_train_duration_hours"] == 8
    assert checkpoint["v2_gate_b_research_default_validation_duration_hours"] == 2
    assert checkpoint["v2_gate_b_research_default_test_duration_hours"] == 2
    assert checkpoint["v2_gate_b_research_default_step_duration_hours"] == 2
    assert checkpoint["v2_gate_b_research_default_final_holdout_duration_hours"] == 2
    assert checkpoint["v2_gate_b_research_default_min_edge_grid"] == [
        0.0,
        0.01,
        0.02,
        0.03,
        0.05,
        0.075,
        0.1,
        0.15,
    ]
    assert checkpoint["v2_gate_b_research_database_mutations"] is False
    assert checkpoint["v2_gate_b_research_gate_b_authorized"] is False
    assert checkpoint["v2_gate_b_research_automatic_promotion"] is False
    assert checkpoint["v2_gate_b_research_production_run_attempted"] is True
    assert checkpoint["v2_gate_b_research_first_production_attempt_result"] == (
        "FAIL_PRE_LABEL_PLAN"
    )
    assert checkpoint["v2_gate_b_research_first_production_attempt_reason"] == (
        "test requires at least 6 markets; found 4"
    )
    assert checkpoint["v2_gate_b_research_first_production_attempt_plan_written"] is False
    assert (
        checkpoint["v2_gate_b_research_first_production_attempt_selection_written"]
        is False
    )
    assert (
        checkpoint["v2_gate_b_research_first_production_attempt_holdout_written"]
        is False
    )
    assert (
        checkpoint["v2_gate_b_research_first_production_attempt_holdout_touched"]
        is False
    )
    assert checkpoint["v2_gate_b_sparse_prefix_recovery_status"] == (
        "MERGED_MAIN_PROVEN_INSUFFICIENT_CONTIGUOUS_EVIDENCE"
    )
    assert checkpoint["v2_gate_b_sparse_prefix_recovery_pr"] == 156
    assert checkpoint["v2_gate_b_sparse_prefix_recovery_final_branch_head"] == (
        "393effa09fbb7a686cce9db2031822d3c3c40a1b"
    )
    assert checkpoint["v2_gate_b_sparse_prefix_recovery_merge_commit"] == (
        "184b725724d46d7ed757dd715a88dafddbdd45e1"
    )
    assert checkpoint["v2_gate_b_sparse_prefix_recovery_post_merge_ci_run_id"] == (
        34366077039
    )
    assert checkpoint["v2_gate_b_sparse_prefix_recovery_post_merge_ci_passed"] is True
    assert checkpoint["v2_gate_b_sparse_prefix_recovery_post_merge_test_count"] == 1015
    assert checkpoint["v2_gate_b_sparse_prefix_recovery_retry_safe"] is False
    assert checkpoint["v2_gate_b_research_second_production_attempt_result"] == (
        "FAIL_PRE_LABEL_INSUFFICIENT_CONTIGUOUS_EVIDENCE"
    )
    assert checkpoint["v2_gate_b_research_second_production_attempt_plan_present"] is False
    assert checkpoint["v2_gate_b_research_second_production_attempt_selection_present"] is False
    assert checkpoint["v2_gate_b_research_second_production_attempt_holdout_present"] is False
    assert checkpoint["v2_gate_b_research_second_production_attempt_summary_present"] is False
    assert checkpoint["v2_gate_b_research_second_production_attempt_holdout_touched"] is False
    assert checkpoint["v2_gate_b_research_minimum_contiguous_epoch_hours"] == 18
    assert (
        checkpoint["v2_gate_b_research_retry_blocked_until_feature_only_readiness"]
        is True
    )
    assert checkpoint["v2_gate_b_sparse_prefix_recovery_preserves_phase8_minimums"] is True
    assert checkpoint["v2_gate_b_sparse_prefix_recovery_skips_individual_folds"] is False
    assert (
        checkpoint["v2_gate_b_sparse_prefix_recovery_reads_labels_for_epoch_selection"]
        is False
    )
