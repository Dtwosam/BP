from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, insert

from bp_engine.features.repository import MarketFeatureRepository
from bp_engine.features.v2_models import V2_FEATURE_VERSION, V2FeatureTarget
from bp_engine.features.v2_service import build_v2_feature, plan_v2_feature_times
from bp_engine.storage import schema

START = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
END = START + timedelta(seconds=300)
FEATURE_AT = START + timedelta(seconds=120)
UP_TOKEN = "up-token-v2-service"
DOWN_TOKEN = "down-token-v2-service"


def _engine():
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    return engine


def _target(*, horizon_seconds: int = 300) -> V2FeatureTarget:
    return V2FeatureTarget(
        condition_id="condition-v2-service",
        slug="btc-updown-5m-v2-service",
        horizon_seconds=horizon_seconds,
        market_start_at=START,
        market_end_at=START + timedelta(seconds=horizon_seconds),
        up_token_id=UP_TOKEN,
        down_token_id=DOWN_TOKEN,
    )


def _z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _insert_state(
    connection,
    *,
    asset_id: str,
    last_event_at: datetime,
    trade_source_at: datetime,
    trade_received_at: datetime,
    dedupe_key: str,
    price: str,
) -> None:
    connection.execute(
        insert(schema.market_state_1s).values(
            bucket_at=last_event_at,
            state_key=f"polymarket/market/condition-v2-service/{asset_id}",
            source="polymarket",
            stream="market",
            instrument="condition-v2-service",
            market_id="condition-v2-service",
            asset_id=asset_id,
            last_event_at=last_event_at,
            state={
                "last_trade_price": price,
                "last_trade_size": "5",
                "last_trade_side": "BUY",
                "last_trade_source_at": _z(trade_source_at),
                "last_trade_received_at": _z(trade_received_at),
                "last_trade_event_dedupe_key": dedupe_key,
                "best_bid": "0.60" if asset_id == UP_TOKEN else "0.38",
                "best_ask": "0.62" if asset_id == UP_TOKEN else "0.40",
                "bid_depth": "10",
                "ask_depth": "8",
            },
        )
    )


def _seed_inputs(connection, *, up_dedupe: str = "sha256:" + "a" * 64) -> None:
    _insert_state(
        connection,
        asset_id=UP_TOKEN,
        last_event_at=FEATURE_AT - timedelta(seconds=1),
        trade_source_at=FEATURE_AT - timedelta(seconds=25),
        trade_received_at=FEATURE_AT - timedelta(seconds=24),
        dedupe_key=up_dedupe,
        price="0.61",
    )
    _insert_state(
        connection,
        asset_id=DOWN_TOKEN,
        last_event_at=FEATURE_AT - timedelta(seconds=11),
        trade_source_at=FEATURE_AT - timedelta(seconds=5),
        trade_received_at=FEATURE_AT - timedelta(seconds=4),
        dedupe_key="sha256:" + "b" * 64,
        price="0.39",
    )


def test_v2_planner_is_exactly_four_5m_offsets() -> None:
    assert tuple(
        int((value - START).total_seconds()) for value in plan_v2_feature_times(_target())
    ) == (60, 120, 180, 240)

    with pytest.raises(ValueError, match="300"):
        plan_v2_feature_times(_target(horizon_seconds=900))


def test_build_v2_feature_is_narrow_timestamp_coherent_and_keeps_book_freshness() -> None:
    engine = _engine()
    with engine.begin() as connection:
        _seed_inputs(connection)
        feature = build_v2_feature(
            connection,
            _target(),
            FEATURE_AT,
            generated_at=FEATURE_AT + timedelta(seconds=30),
        )

    assert feature.feature_version == V2_FEATURE_VERSION
    assert feature.feature_offset_seconds == 120
    assert feature.features["pm_up_last_trade_price"] == 0.61
    assert feature.features["pm_up_last_trade_source_age_s"] == 25.0
    assert feature.features["pm_up_last_trade_availability_age_s"] == 24.0
    assert feature.missing_flags["pm_up_last_trade_missing"] is False
    assert feature.features["pm_up_best_bid"] == 0.60
    assert feature.missing_flags["pm_up_book_missing"] is False
    assert feature.missing_flags["pm_down_book_missing"] is True
    assert feature.missing_flags["pm_down_book_stale"] is True

    forbidden = (
        "pm_up_price",
        "pm_down_price",
        "price_history",
        "label",
        "outcome",
        "calibration",
        "edge",
    )
    payload_text = repr(feature.features).lower()
    assert all(name not in payload_text for name in forbidden)
    assert all(value <= _z(FEATURE_AT) for value in feature.source_cutoffs.values())


def test_provenance_changes_fingerprint_without_changing_feature_hash() -> None:
    features = []
    for dedupe_key in ("sha256:" + "c" * 64, "sha256:" + "d" * 64):
        engine = _engine()
        with engine.begin() as connection:
            _seed_inputs(connection, up_dedupe=dedupe_key)
            features.append(
                build_v2_feature(
                    connection,
                    _target(),
                    FEATURE_AT,
                    generated_at=FEATURE_AT + timedelta(seconds=30),
                )
            )

    first, second = features
    assert first.features == second.features
    assert first.missing_flags == second.missing_flags
    assert first.source_cutoffs == second.source_cutoffs
    assert first.feature_hash == second.feature_hash
    assert first.input_fingerprint != second.input_fingerprint


def test_exact_rerun_is_repository_noop() -> None:
    engine = _engine()
    repository = MarketFeatureRepository()
    with engine.begin() as connection:
        _seed_inputs(connection)
        feature = build_v2_feature(
            connection,
            _target(),
            FEATURE_AT,
            generated_at=FEATURE_AT + timedelta(seconds=30),
        )
        first = repository.store(connection, feature)
        second = repository.store(connection, feature)

    assert first.created is True
    assert second.created is False
