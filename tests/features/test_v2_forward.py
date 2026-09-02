from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, insert

from bp_engine.features.v2_forward import (
    V2_FORWARD_EPOCH,
    discover_pending_v2_targets,
)
from bp_engine.features.v2_models import V2_FEATURE_VERSION
from bp_engine.storage import schema

EPOCH = datetime(2026, 9, 2, 12, 18, 2, tzinfo=UTC)
CYCLE_AT = datetime(2026, 9, 2, 13, 0, 0, tzinfo=UTC)


def _engine():
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    return engine


def _insert_market(
    connection,
    *,
    condition_id: str,
    start_at: datetime,
    horizon_seconds: int = 300,
) -> None:
    end_at = start_at + timedelta(seconds=horizon_seconds)
    connection.execute(
        insert(schema.polymarket_markets).values(
            gamma_market_id=f"gamma-{condition_id}",
            event_id=f"event-{condition_id}",
            condition_id=condition_id,
            slug=f"btc-updown-{horizon_seconds}-{condition_id}",
            question=f"market {condition_id}",
            horizon_seconds=horizon_seconds,
            start_at=start_at,
            end_at=end_at,
            up_token_id=f"up-{condition_id}",
            down_token_id=f"down-{condition_id}",
            resolution_source="Chainlink",
            rules_text="rules",
            rules_hash="sha256:" + "a" * 64,
            active=end_at > CYCLE_AT,
            closed=end_at <= CYCLE_AT,
            accepting_orders=end_at > CYCLE_AT,
            resolved_outcome=None,
            discovered_at=start_at - timedelta(minutes=1),
            updated_at=start_at,
        )
    )


def _insert_v2_feature(connection, *, condition_id: str, start_at: datetime, offset: int) -> None:
    feature_at = start_at + timedelta(seconds=offset)
    connection.execute(
        insert(schema.market_features).values(
            condition_id=condition_id,
            slug=f"btc-updown-300-{condition_id}",
            horizon_seconds=300,
            market_start_at=start_at,
            market_end_at=start_at + timedelta(seconds=300),
            feature_at=feature_at,
            feature_offset_seconds=offset,
            feature_version=V2_FEATURE_VERSION,
            features={},
            missing_flags={},
            source_cutoffs={},
            input_fingerprint="a" * 64,
            feature_hash="b" * 64,
            generated_at=CYCLE_AT,
        )
    )


def test_forward_epoch_is_the_proven_gate_a_rollout_boundary() -> None:
    assert V2_FORWARD_EPOCH == EPOCH


def test_pending_discovery_only_includes_completed_post_epoch_5m_markets() -> None:
    engine = _engine()
    eligible_start = datetime(2026, 9, 2, 12, 40, tzinfo=UTC)
    with engine.begin() as connection:
        _insert_market(
            connection,
            condition_id="pre-epoch",
            start_at=datetime(2026, 9, 2, 12, 10, tzinfo=UTC),
        )
        _insert_market(
            connection,
            condition_id="eligible",
            start_at=eligible_start,
        )
        _insert_market(
            connection,
            condition_id="fifteen-minute",
            start_at=datetime(2026, 9, 2, 12, 30, tzinfo=UTC),
            horizon_seconds=900,
        )
        _insert_market(
            connection,
            condition_id="active",
            start_at=datetime(2026, 9, 2, 12, 58, tzinfo=UTC),
        )
        # Fully ended, but still inside the explicit 15-second operational grace.
        _insert_market(
            connection,
            condition_id="inside-grace",
            start_at=datetime(2026, 9, 2, 12, 55, 10, tzinfo=UTC),
        )

        pending = discover_pending_v2_targets(connection, cycle_at=CYCLE_AT)

    assert [target.condition_id for target in pending] == ["eligible"]
    target = pending[0]
    assert target.market_start_at == eligible_start
    assert target.market_end_at == eligible_start + timedelta(seconds=300)


def test_complete_target_is_skipped_and_partial_target_remains_pending() -> None:
    engine = _engine()
    complete_start = datetime(2026, 9, 2, 12, 30, tzinfo=UTC)
    partial_start = datetime(2026, 9, 2, 12, 40, tzinfo=UTC)
    with engine.begin() as connection:
        _insert_market(connection, condition_id="complete", start_at=complete_start)
        _insert_market(connection, condition_id="partial", start_at=partial_start)
        for offset in (60, 120, 180, 240):
            _insert_v2_feature(
                connection,
                condition_id="complete",
                start_at=complete_start,
                offset=offset,
            )
        for offset in (60, 180):
            _insert_v2_feature(
                connection,
                condition_id="partial",
                start_at=partial_start,
                offset=offset,
            )

        pending = discover_pending_v2_targets(connection, cycle_at=CYCLE_AT)

    assert [target.condition_id for target in pending] == ["partial"]


def test_unexpected_v2_forward_offset_fails_closed() -> None:
    engine = _engine()
    start = datetime(2026, 9, 2, 12, 40, tzinfo=UTC)
    with engine.begin() as connection:
        _insert_market(connection, condition_id="bad-offset", start_at=start)
        _insert_v2_feature(
            connection,
            condition_id="bad-offset",
            start_at=start,
            offset=30,
        )

        with pytest.raises(RuntimeError, match="unexpected V2 forward feature offset"):
            discover_pending_v2_targets(connection, cycle_at=CYCLE_AT)
