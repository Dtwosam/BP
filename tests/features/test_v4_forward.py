import inspect
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, insert

from bp_engine.features import v4_forward
from bp_engine.features.v4_models import V4_FEATURE_VERSION
from bp_engine.storage import schema

EPOCH = datetime(2026, 9, 20, 12, 40, 53, tzinfo=UTC)
CYCLE_AT = datetime(2026, 9, 20, 13, 30, tzinfo=UTC)


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


def _insert_v4_feature(connection, *, condition_id: str, start_at: datetime, offset: int) -> None:
    connection.execute(
        insert(schema.market_features).values(
            condition_id=condition_id,
            slug=f"btc-updown-300-{condition_id}",
            horizon_seconds=300,
            market_start_at=start_at,
            market_end_at=start_at + timedelta(seconds=300),
            feature_at=start_at + timedelta(seconds=offset),
            feature_offset_seconds=offset,
            feature_version=V4_FEATURE_VERSION,
            features={},
            missing_flags={},
            source_cutoffs={},
            input_fingerprint="a" * 64,
            feature_hash="b" * 64,
            generated_at=CYCLE_AT,
        )
    )


def test_v4_forward_epoch_is_v4_merge_boundary() -> None:
    assert v4_forward.V4_FORWARD_EPOCH == EPOCH


def test_pending_discovery_is_only_completed_post_v4_epoch_5m_markets() -> None:
    engine = _engine()
    eligible_start = datetime(2026, 9, 20, 12, 45, tzinfo=UTC)
    with engine.begin() as connection:
        _insert_market(
            connection,
            condition_id="pre-v4",
            start_at=datetime(2026, 9, 20, 12, 40, tzinfo=UTC),
        )
        _insert_market(connection, condition_id="eligible", start_at=eligible_start)
        _insert_market(
            connection,
            condition_id="fifteen-minute",
            start_at=datetime(2026, 9, 20, 12, 45, tzinfo=UTC),
            horizon_seconds=900,
        )
        _insert_market(
            connection,
            condition_id="active",
            start_at=datetime(2026, 9, 20, 13, 28, tzinfo=UTC),
        )
        _insert_market(
            connection,
            condition_id="inside-grace",
            start_at=datetime(2026, 9, 20, 13, 24, 50, tzinfo=UTC),
        )

        pending = v4_forward.discover_pending_v4_targets(
            connection,
            cycle_at=CYCLE_AT,
        )

    assert [target.condition_id for target in pending] == ["eligible"]
    assert pending[0].market_start_at == eligible_start


def test_complete_target_is_skipped_and_partial_target_remains_pending() -> None:
    engine = _engine()
    complete_start = datetime(2026, 9, 20, 12, 45, tzinfo=UTC)
    partial_start = datetime(2026, 9, 20, 12, 50, tzinfo=UTC)
    with engine.begin() as connection:
        _insert_market(connection, condition_id="complete", start_at=complete_start)
        _insert_market(connection, condition_id="partial", start_at=partial_start)
        for offset in (60, 120, 180, 240):
            _insert_v4_feature(
                connection,
                condition_id="complete",
                start_at=complete_start,
                offset=offset,
            )
        for offset in (60, 180):
            _insert_v4_feature(
                connection,
                condition_id="partial",
                start_at=partial_start,
                offset=offset,
            )

        pending = v4_forward.discover_pending_v4_targets(
            connection,
            cycle_at=CYCLE_AT,
        )

    assert [target.condition_id for target in pending] == ["partial"]


def test_unexpected_v4_forward_offset_fails_closed() -> None:
    engine = _engine()
    start = datetime(2026, 9, 20, 12, 45, tzinfo=UTC)
    with engine.begin() as connection:
        _insert_market(connection, condition_id="bad-offset", start_at=start)
        _insert_v4_feature(
            connection,
            condition_id="bad-offset",
            start_at=start,
            offset=30,
        )
        with pytest.raises(RuntimeError, match="unexpected V4 forward feature offset"):
            v4_forward.discover_pending_v4_targets(connection, cycle_at=CYCLE_AT)


def test_forward_cycle_fails_closed_on_coverage_invariant(monkeypatch) -> None:
    engine = _engine()
    monkeypatch.setattr(v4_forward, "discover_pending_v4_targets", lambda *args, **kwargs: ())
    monkeypatch.setattr(
        v4_forward,
        "generate_v4_features",
        lambda *args, **kwargs: type(
            "Stats",
            (),
            {"inserted": 0, "existing": 0, "planned_rows": 0},
        )(),
    )
    monkeypatch.setattr(
        v4_forward,
        "build_v4_coverage_report",
        lambda *args, **kwargs: {
            "row_count": 0,
            "market_count": 0,
            "regime": {
                "bull": {"market_count": 0},
                "bear": {"market_count": 0},
                "sideways_mixed": {"market_count": 0},
                "unknown": {"market_count": 0},
            },
            "future_cutoff_violation_count": 1,
            "polymarket_predictor_key_count": 0,
            "regime_invariant_violation_count": 0,
            "policy_selected": False,
            "training_run": False,
            "automatic_promotion": False,
        },
    )

    with engine.begin() as connection:
        with pytest.raises(RuntimeError, match="V4 forward coverage invariant violation"):
            v4_forward.run_v4_forward_cycle(connection, cycle_at=CYCLE_AT)


def test_forward_module_is_outcome_blind_and_has_no_trading_path() -> None:
    source = inspect.getsource(v4_forward).lower()
    forbidden = (
        "official_outcome",
        "market_labels",
        "paper_settlements",
        "pnl",
        "calibration",
        "edge_policy",
        "execution",
    )
    assert all(term not in source for term in forbidden)
