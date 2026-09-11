from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, insert

from bp_engine.storage import schema
from bp_engine.v2_research.models import GateBPlanConfig, GateBResearchConfig
from bp_engine.v2_research.plan import assess_gate_b_readiness, build_gate_b_plan

ROOT_START = datetime(2026, 9, 11, 12, 20, tzinfo=UTC)
OFFSETS = (60, 120, 180, 240)


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    schema.metadata.create_all(engine)
    return engine


def _feature(condition_index: int, offset: int) -> dict[str, object]:
    market_start = ROOT_START + timedelta(minutes=5 * condition_index)
    feature_at = market_start + timedelta(seconds=offset)
    return {
        "condition_id": f"condition-{condition_index:03d}",
        "slug": f"btc-updown-5m-{condition_index:03d}",
        "horizon_seconds": 300,
        "market_start_at": market_start,
        "market_end_at": market_start + timedelta(minutes=5),
        "feature_at": feature_at,
        "feature_offset_seconds": offset,
        "feature_version": "core-v2-last-trade",
        "features": {},
        "missing_flags": {},
        "source_cutoffs": {},
        "input_fingerprint": f"{condition_index % 10}" * 64,
        "feature_hash": f"{(condition_index + offset) % 10}" * 64,
        "generated_at": market_start + timedelta(minutes=6),
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


def test_fresh_planning_epoch_excludes_all_pre_boundary_markets() -> None:
    engine = _engine()
    rows = [_feature(index, offset) for index in range(30) for offset in OFFSETS]
    planning_epoch_start = ROOT_START + timedelta(minutes=40)

    with engine.begin() as connection:
        connection.execute(insert(schema.market_features), rows)
        readiness = assess_gate_b_readiness(
            connection,
            _plan_config(),
            _research_config(),
            planning_epoch_start=planning_epoch_start,
        )
        plan = build_gate_b_plan(
            connection,
            _plan_config(),
            _research_config(),
            planning_epoch_start=planning_epoch_start,
        )

    assert readiness["planning_epoch_start_at"] == planning_epoch_start.isoformat()
    assert plan["planning_epoch_start_at"] == planning_epoch_start.isoformat()
    assert plan["analysis_start_at"] >= planning_epoch_start.isoformat()

    membership_ids = {
        condition_id
        for fold in plan["folds"]
        for partition in ("train", "validation", "test")
        for condition_id in fold[partition]["condition_ids"]
    }
    membership_ids.update(plan["final"]["train_condition_ids"])
    membership_ids.update(plan["final"]["validation_condition_ids"])
    membership_ids.update(plan["final"]["holdout_condition_ids"])

    assert membership_ids
    assert all(int(condition_id.rsplit("-", 1)[1]) >= 8 for condition_id in membership_ids)
    assert readiness["market_start_at"] == planning_epoch_start.isoformat()
    assert plan["market_start_at"] == planning_epoch_start.isoformat()


def test_fresh_planning_epoch_rejects_naive_boundary() -> None:
    engine = _engine()
    rows = [_feature(index, offset) for index in range(30) for offset in OFFSETS]

    with engine.begin() as connection:
        connection.execute(insert(schema.market_features), rows)
        with pytest.raises(ValueError, match="planning_epoch_start must be timezone-aware"):
            build_gate_b_plan(
                connection,
                _plan_config(),
                _research_config(),
                planning_epoch_start=datetime(2026, 9, 11, 13, 0),
            )
