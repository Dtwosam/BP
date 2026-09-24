from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine

from bp_engine.execution import canary
from bp_engine.storage import schema

BASE = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)


def _engine():
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    return engine


def _insert_decision(
    connection,
    *,
    prediction_id: str,
    reasons: list[str],
    created_at: datetime,
    suffix: str,
    eligible: bool = False,
) -> None:
    connection.execute(
        schema.live_risk_decisions.insert().values(
            decision_id=f"live-risk-{suffix}",
            prediction_id=prediction_id,
            prediction_semantic_sha256="a" * 64,
            policy_version=canary.CANARY_POLICY_VERSION,
            policy_sha256="b" * 64,
            eligible=eligible,
            reasons=reasons,
            rules=[],
            account_snapshot={},
            evidence={},
            semantic_sha256=("c" if suffix == "1" else "d") * 64,
            created_at=created_at,
        )
    )


def test_transient_liquidity_miss_does_not_blacklist_prediction() -> None:
    engine = _engine()
    prediction_id = "p" * 64
    with engine.begin() as connection:
        _insert_decision(
            connection,
            prediction_id=prediction_id,
            reasons=["liquidity_missing"],
            created_at=BASE,
            suffix="1",
        )
        evaluated = canary._evaluated_prediction_ids(connection)

    assert prediction_id not in evaluated


def test_multiple_transient_misses_remain_retryable() -> None:
    engine = _engine()
    prediction_id = "p" * 64
    with engine.begin() as connection:
        _insert_decision(
            connection,
            prediction_id=prediction_id,
            reasons=["liquidity_missing"],
            created_at=BASE,
            suffix="1",
        )
        _insert_decision(
            connection,
            prediction_id=prediction_id,
            reasons=["liquidity_below_minimum"],
            created_at=BASE + timedelta(seconds=2),
            suffix="2",
        )
        evaluated = canary._evaluated_prediction_ids(connection)

    assert prediction_id not in evaluated


def test_api_unhealthy_is_retryable() -> None:
    engine = _engine()
    prediction_id = "p" * 64
    with engine.begin() as connection:
        _insert_decision(
            connection,
            prediction_id=prediction_id,
            reasons=["api_unhealthy"],
            created_at=BASE,
            suffix="1",
        )
        evaluated = canary._evaluated_prediction_ids(connection)

    assert prediction_id not in evaluated


def test_combined_transient_reasons_remain_retryable() -> None:
    engine = _engine()
    prediction_id = "p" * 64
    with engine.begin() as connection:
        _insert_decision(
            connection,
            prediction_id=prediction_id,
            reasons=["liquidity_missing", "api_unhealthy"],
            created_at=BASE,
            suffix="1",
        )
        evaluated = canary._evaluated_prediction_ids(connection)

    assert prediction_id not in evaluated


def test_permanent_reason_stops_future_retries() -> None:
    engine = _engine()
    prediction_id = "p" * 64
    with engine.begin() as connection:
        _insert_decision(
            connection,
            prediction_id=prediction_id,
            reasons=["liquidity_missing"],
            created_at=BASE,
            suffix="1",
        )
        _insert_decision(
            connection,
            prediction_id=prediction_id,
            reasons=["liquidity_missing", "prediction_stale"],
            created_at=BASE + timedelta(seconds=30),
            suffix="2",
        )
        evaluated = canary._evaluated_prediction_ids(connection)

    assert prediction_id in evaluated


def test_eligible_decision_is_terminal_even_with_no_reasons() -> None:
    engine = _engine()
    prediction_id = "p" * 64
    with engine.begin() as connection:
        _insert_decision(
            connection,
            prediction_id=prediction_id,
            reasons=[],
            created_at=BASE,
            suffix="1",
            eligible=True,
        )
        evaluated = canary._evaluated_prediction_ids(connection)

    assert prediction_id in evaluated
