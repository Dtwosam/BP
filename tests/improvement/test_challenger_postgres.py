from __future__ import annotations

import importlib
import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, delete

from bp_engine.calibration.models import EdgePolicyMetrics, EdgePolicySelection
from bp_engine.improvement.models import (
    EXPERIMENT_VERSION,
    ChampionRef,
    ChangeFamily,
    EvidenceItem,
    EvidenceRole,
    ImprovementExperimentSpec,
    PolicyMetrics,
)
from bp_engine.improvement.repository import ImprovementExperimentRepository
from bp_engine.storage import schema

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)

ACCEPTED_RUN_ID = "phase9-300-c9f0e00eb7836af08008c66909f8f179"
ACCEPTED_CALIBRATION_SHA = "c9f0e00eb7836af08008c66909f8f179f03089413426508469353c75bcbcae24"
BACKTEST_RUN_ID = "phase8-300-efdf493067e9d56419afc4d88452bec6"
BACKTEST_SHA = "efdf493067e9d56419afc4d88452bec6effb871482664d19f109b3bbe4dd1d93"
TRAINING_RUN_ID = "phase7-300-0a822e17ceced11742bf6d3bc8214f44"
TRAINING_SHA = "0a822e17ceced11742bf6d3bc8214f44f4755c7bc23bb1d3f2dcfa897f3edcc0"
START = datetime(2026, 8, 24, tzinfo=UTC)
END = datetime(2026, 8, 25, tzinfo=UTC)
FREEZE = datetime(2026, 8, 29, 8, 0, tzinfo=UTC)
EVALUATED = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)


def _module():
    return importlib.import_module("bp_engine.improvement.challenger")


def _champion() -> ChampionRef:
    return ChampionRef(
        calibration_run_id=ACCEPTED_RUN_ID,
        calibration_semantic_sha256=ACCEPTED_CALIBRATION_SHA,
        backtest_run_id=BACKTEST_RUN_ID,
        backtest_semantic_sha256=BACKTEST_SHA,
        training_run_id=TRAINING_RUN_ID,
        training_semantic_sha256=TRAINING_SHA,
    )


def _phase9_report() -> dict[str, object]:
    return {
        "run_id": ACCEPTED_RUN_ID,
        "semantic_sha256": ACCEPTED_CALIBRATION_SHA,
        "source_backtest_run_id": BACKTEST_RUN_ID,
        "source_backtest_semantic_sha256": BACKTEST_SHA,
        "source_training_run_id": TRAINING_RUN_ID,
        "source_training_semantic_sha256": TRAINING_SHA,
        "dataset_version": "supervised-core-v1",
        "feature_version": "core-v1",
        "label_version": "official-outcome-v1",
        "horizon_seconds": 300,
        "start": "2026-08-24T00:00:00Z",
        "end": "2026-08-25T00:00:00Z",
        "dataset_sha256": "d5d2843ea2882aebe1cd3612e4345062067430d060824209b955a30590d8a6c2",
        "config": {
            "fee_rate": 0.07,
            "slippage_buffer": 0.01,
            "min_edge_grid": [0.0, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15],
            "min_validation_trades": 3,
            "max_spread": None,
        },
        "source_fold_membership_sha256": ["1" * 64, "2" * 64, "3" * 64],
        "final_holdout": {
            "holdout_condition_ids": ["legacy-final-1", "legacy-final-2"]
        },
    }


def _metrics(*, pnl: float, trade_count: int = 3) -> PolicyMetrics:
    return PolicyMetrics(
        calibrated_log_loss=0.40,
        calibrated_brier=0.15,
        ece=0.03,
        accuracy=0.80,
        trade_count=trade_count,
        trade_coverage=0.30,
        total_net_pnl=pnl,
        mean_net_pnl_per_trade=pnl / trade_count if trade_count else 0.0,
        max_drawdown=0.05,
        max_losing_streak=1,
        unresolved_count=0,
        missing_unexecutable_count=0,
    )


def _edge_metrics(
    *,
    pnl: float,
    edge_sum: float,
    trades: int,
) -> EdgePolicyMetrics:
    return EdgePolicyMetrics(
        prediction_markets=20,
        market_probability_observed_markets=20,
        executable_markets=20,
        trade_count=trades,
        no_fill_markets=0,
        abstained_edge_markets=20 - trades,
        reason_counts={},
        trade_coverage=trades / 20,
        average_observed_ask=0.55,
        average_observed_spread=0.03,
        correct_trades=trades,
        traded_accuracy=1.0 if trades else None,
        raw_expected_edge_sum=edge_sum + 0.01 * trades,
        mean_raw_expected_edge=(edge_sum + 0.01 * trades) / trades if trades else None,
        fee_sum=0.0,
        slippage_sum=0.01 * trades,
        cost_adjusted_expected_edge_sum=edge_sum,
        mean_cost_adjusted_expected_edge=edge_sum / trades if trades else None,
        gross_realized_pnl_before_costs=pnl + 0.01 * trades,
        realized_pnl_after_assumed_costs=pnl,
        mean_realized_pnl_after_assumed_costs=pnl / trades if trades else None,
    )


def _selection(*, pnl: float, edge_sum: float, trades: int) -> EdgePolicySelection:
    metrics = _edge_metrics(pnl=pnl, edge_sum=edge_sum, trades=trades)
    return EdgePolicySelection(
        policy="trade_threshold" if trades else "no_trade",
        min_edge=0.02 if trades else None,
        validation_metrics=metrics,
        candidates=(),
    )


def _experiment() -> ImprovementExperimentSpec:
    return ImprovementExperimentSpec.build(
        experiment_version=EXPERIMENT_VERSION,
        hypothesis="A max-spread abstention guard improves executable economics.",
        horizon_seconds=300,
        change_family=ChangeFamily.ABSTENTION,
        champion=_champion(),
        challenger={
            "kind": "spread_guard_v1",
            "max_spread_grid": [0.02, 0.04, 0.06, 0.08, 0.10, None],
        },
        source_versions={
            "dataset": "supervised-core-v1",
            "feature": "core-v1",
            "label": "official-outcome-v1",
        },
        research_start=START,
        research_end=END,
        selection_policy={"allowed_roles": ["development_validation"]},
        confirmation_policy={"allowed_roles": ["fresh_holdout", "prospective_paper"]},
        cost_assumptions={"fee_rate": 0.07, "slippage_buffer": 0.01},
        primary_metric="net_pnl_delta",
        guardrail_metrics=("calibrated_log_loss", "calibrated_brier"),
        legacy_confirmation_identifiers=("legacy-final-1", "legacy-final-2"),
        created_at=FREEZE,
    )


def _setup_engine():
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(delete(schema.improvement_promotion_decisions))
        connection.execute(delete(schema.improvement_evaluations))
        connection.execute(delete(schema.improvement_experiments))
    return engine


def test_build_spread_guard_experiment_freezes_exact_accepted_champion(monkeypatch) -> None:
    module = _module()
    calls: list[str] = []

    def fake_champion(_connection, run_id):
        calls.append(run_id)
        return _champion()

    monkeypatch.setattr(module, "load_champion_ref", fake_champion)
    monkeypatch.setattr(module, "load_phase9_report", lambda _c, _r: _phase9_report())

    experiment = module.build_spread_guard_experiment(object(), created_at=FREEZE)

    assert calls == [ACCEPTED_RUN_ID]
    assert module.ACCEPTED_PHASE9_5M_RUN_ID == ACCEPTED_RUN_ID
    assert module.MAX_SPREAD_GRID == (0.02, 0.04, 0.06, 0.08, 0.10, None)
    assert experiment.champion == _champion()
    assert experiment.horizon_seconds == 300
    assert experiment.challenger["max_spread_grid"] == [0.02, 0.04, 0.06, 0.08, 0.10, None]
    assert experiment.cost_assumptions == {"fee_rate": 0.07, "slippage_buffer": 0.01}
    assert set(experiment.legacy_confirmation_identifiers) == {
        "legacy-final-1",
        "legacy-final-2",
    }


def test_build_spread_guard_experiment_rejects_wrong_champion_identity(monkeypatch) -> None:
    module = _module()
    wrong = ChampionRef(
        calibration_run_id="phase9-300-wrong",
        calibration_semantic_sha256=ACCEPTED_CALIBRATION_SHA,
        backtest_run_id=BACKTEST_RUN_ID,
        backtest_semantic_sha256=BACKTEST_SHA,
        training_run_id=TRAINING_RUN_ID,
        training_semantic_sha256=TRAINING_SHA,
    )
    monkeypatch.setattr(module, "load_champion_ref", lambda _c, _r: wrong)
    monkeypatch.setattr(module, "load_phase9_report", lambda _c, _r: _phase9_report())

    with pytest.raises(module.ChallengerIntegrityError, match="accepted Phase 9 champion"):
        module.build_spread_guard_experiment(object(), created_at=FREEZE)


def test_validation_max_spread_selection_uses_frozen_tie_break_order() -> None:
    module = _module()

    best_pnl = {
        0.02: _selection(pnl=0.10, edge_sum=0.20, trades=4),
        0.04: _selection(pnl=0.11, edge_sum=0.01, trades=8),
    }
    assert module.select_validation_max_spread(best_pnl) == 0.04

    best_edge = {
        0.02: _selection(pnl=0.10, edge_sum=0.20, trades=4),
        0.04: _selection(pnl=0.10, edge_sum=0.21, trades=8),
    }
    assert module.select_validation_max_spread(best_edge) == 0.04

    fewer_trades = {
        0.02: _selection(pnl=0.10, edge_sum=0.20, trades=3),
        0.04: _selection(pnl=0.10, edge_sum=0.20, trades=4),
    }
    assert module.select_validation_max_spread(fewer_trades) == 0.02

    tighter_spread = {
        0.02: _selection(pnl=0.10, edge_sum=0.20, trades=3),
        0.04: _selection(pnl=0.10, edge_sum=0.20, trades=3),
        None: _selection(pnl=0.10, edge_sum=0.20, trades=3),
    }
    assert module.select_validation_max_spread(tighter_spread) == 0.02


def test_legacy_source_evaluation_cannot_claim_independent_confirmation(monkeypatch) -> None:
    module = _module()
    engine = _setup_engine()
    experiment = _experiment()

    evidence = (
        EvidenceItem(
            identifier="ordinary-oos-1",
            role=EvidenceRole.ORDINARY_OOS,
            condition_id="ordinary-oos-1",
            prediction_id=None,
            observed_at=datetime(2026, 8, 25, tzinfo=UTC),
            resolved=True,
            net_pnl=0.10,
        ),
        EvidenceItem(
            identifier="legacy-final-1",
            role=EvidenceRole.ORDINARY_OOS,
            condition_id="legacy-final-1",
            prediction_id=None,
            observed_at=datetime(2026, 8, 25, tzinfo=UTC),
            resolved=True,
            net_pnl=0.10,
        ),
    )
    analysis = module.SpreadGuardAnalysis(
        challenger_semantic_sha256="d" * 64,
        challenger_config={
            "kind": "spread_guard_v1",
            "max_spread_grid": [0.02, 0.04, 0.06, 0.08, 0.10, None],
            "selected_fold_max_spreads": [0.04],
        },
        evidence_manifest=evidence,
        champion_metrics=_metrics(pnl=0.10),
        challenger_metrics=_metrics(pnl=0.20),
        paired_net_pnl=(("ordinary-oos-1", 0.0, 0.10),),
    )
    monkeypatch.setattr(module, "_analyze_legacy_source", lambda *_args, **_kwargs: analysis)

    with engine.begin() as connection:
        ImprovementExperimentRepository().store(connection, experiment)
        report = module.evaluate_spread_guard_challenger(
            connection,
            experiment_id=experiment.experiment_id,
            created_at=EVALUATED,
        )

    assert report.promotion_eligible is False
    assert report.ineligibility_reasons == ("independent_confirmation_missing",)
    assert report.comparison["independent_confirmation_present"] is False
    assert {item.role for item in report.evidence_manifest} == {EvidenceRole.ORDINARY_OOS}
    assert "legacy-final-1" in {item.identifier for item in report.evidence_manifest}


def test_challenger_semantics_include_grid_ties_champion_and_fold_sources(monkeypatch) -> None:
    module = _module()
    semantic_payloads: list[dict[str, object]] = []
    original_hash = module.semantic_sha256

    def capture_hash(payload):
        if isinstance(payload, dict) and payload.get("kind") == "spread_guard_v1":
            semantic_payloads.append(payload)
        return original_hash(payload)

    monkeypatch.setattr(module, "semantic_sha256", capture_hash)
    monkeypatch.setattr(module, "load_champion_ref", lambda _c, _r: _champion())
    monkeypatch.setattr(module, "load_phase9_report", lambda _c, _r: _phase9_report())

    module.build_spread_guard_experiment(object(), created_at=FREEZE)

    assert semantic_payloads
    payload = semantic_payloads[-1]
    assert payload["max_spread_grid"] == [0.02, 0.04, 0.06, 0.08, 0.10, None]
    assert payload["tie_break_rules"] == [
        "highest_validation_realized_pnl_after_assumed_costs",
        "highest_validation_cost_adjusted_expected_edge_sum",
        "lower_validation_trade_count",
        "tighter_max_spread_none_last",
    ]
    assert payload["accepted_champion_semantic_sha256"] == ACCEPTED_CALIBRATION_SHA
    assert payload["source_fold_membership_sha256"] == ["1" * 64, "2" * 64, "3" * 64]
    assert payload["cost_assumptions"] == {"fee_rate": 0.07, "slippage_buffer": 0.01}
