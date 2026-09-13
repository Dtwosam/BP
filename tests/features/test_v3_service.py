from datetime import UTC, datetime, timedelta
import inspect

import pytest
from sqlalchemy import create_engine, insert, select
from bp_engine.features.v3_service import (
    build_v3_feature,
    generate_v3_features,
    plan_v3_feature_times,
)

from bp_engine.features.v3_models import V3_FEATURE_VERSION, V3FeatureTarget
from bp_engine.storage import schema

START = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
END = START + timedelta(seconds=300)
FEATURE_AT = START + timedelta(seconds=180)
GENERATED_AT = END + timedelta(seconds=30)


def _engine():
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    return engine


def _target(
    *,
    horizon_seconds: int = 300,
    end_delta_seconds: int | None = None,
) -> V3FeatureTarget:
    end_delta = horizon_seconds if end_delta_seconds is None else end_delta_seconds
    return V3FeatureTarget(
        condition_id="condition-v3-service",
        slug="btc-updown-5m-v3-service",
        horizon_seconds=horizon_seconds,
        market_start_at=START,
        market_end_at=START + timedelta(seconds=end_delta),
    )


def _insert_state(
    connection,
    *,
    source: str,
    stream: str,
    instrument: str,
    effective_at: datetime,
    price: str,
    state_extra: dict[str, object] | None = None,
) -> None:
    state = {"last_price": price}
    state.update(state_extra or {})
    connection.execute(
        insert(schema.market_state_1s).values(
            bucket_at=effective_at,
            state_key=f"{source}/{stream}/{instrument}",
            source=source,
            stream=stream,
            instrument=instrument,
            market_id=None,
            asset_id=None,
            last_event_at=effective_at,
            state=state,
        )
    )


def _seed_venue(
    connection,
    *,
    source: str,
    stream: str,
    instrument: str,
    prices: tuple[str, str, str, str, str],
    current_extra: dict[str, object] | None = None,
) -> None:
    effective_times = (
        START - timedelta(seconds=1),
        START + timedelta(seconds=59),
        START + timedelta(seconds=119),
        START + timedelta(seconds=149),
        START + timedelta(seconds=179),
    )
    for index, (effective_at, price) in enumerate(zip(effective_times, prices, strict=True)):
        _insert_state(
            connection,
            source=source,
            stream=stream,
            instrument=instrument,
            effective_at=effective_at,
            price=price,
            state_extra=current_extra if index == 4 else None,
        )


def _seed_inputs(connection) -> None:
    _seed_venue(
        connection,
        source="coinbase",
        stream="spot",
        instrument="BTC-USD",
        prices=("100", "101", "102", "103", "104"),
    )
    _seed_venue(
        connection,
        source="bybit",
        stream="spot",
        instrument="BTCUSDT",
        prices=("200", "201", "202", "203", "204"),
    )
    _seed_venue(
        connection,
        source="bybit",
        stream="linear",
        instrument="BTCUSDT",
        prices=("200.5", "201.5", "202.5", "203.5", "205"),
        current_extra={"funding_rate": "-0.001", "open_interest": "12345.5"},
    )


def test_v3_planner_is_exactly_four_5m_offsets_and_rejects_other_windows() -> None:
    assert tuple(
        int((value - START).total_seconds()) for value in plan_v3_feature_times(_target())
    ) == (60, 120, 180, 240)

    with pytest.raises(ValueError, match="300"):
        plan_v3_feature_times(_target(horizon_seconds=900))
    with pytest.raises(ValueError, match="300"):
        plan_v3_feature_times(_target(end_delta_seconds=299))


def test_build_v3_feature_is_btc_only_timestamp_coherent_and_fingerprinted() -> None:
    engine = _engine()
    with engine.begin() as connection:
        _seed_inputs(connection)
        feature = build_v3_feature(
            connection,
            _target(),
            FEATURE_AT,
            generated_at=GENERATED_AT,
        )

    assert feature.feature_version == V3_FEATURE_VERSION
    assert feature.feature_offset_seconds == 180
    assert feature.features["seconds_elapsed"] == 180
    assert feature.features["seconds_remaining"] == 120
    assert feature.features["coinbase_return_from_market_start"] == pytest.approx(0.04)
    assert feature.features["coinbase_return_30s"] == pytest.approx(104 / 103 - 1)
    assert feature.features["coinbase_return_60s"] == pytest.approx(104 / 102 - 1)
    assert feature.features["coinbase_return_120s"] == pytest.approx(104 / 101 - 1)
    assert feature.features["bybit_linear_funding_rate"] == pytest.approx(-0.001)
    assert feature.features["bybit_linear_open_interest"] == pytest.approx(12345.5)
    assert all(value is False for value in feature.missing_flags.values())
    assert len(feature.source_cutoffs) == 15
    assert all(value <= FEATURE_AT.isoformat().replace("+00:00", "Z") for value in feature.source_cutoffs.values())
    assert len(feature.input_fingerprint) == 64
    assert len(feature.feature_hash) == 64

    payload_keys = set(feature.features) | set(feature.missing_flags) | set(feature.source_cutoffs)
    assert not any(key.startswith("pm_") for key in payload_keys)
    assert not any("polymarket" in key.lower() for key in payload_keys)


def test_build_v3_feature_rejects_non_planned_feature_time() -> None:
    engine = _engine()
    with engine.begin() as connection:
        with pytest.raises(ValueError, match="fixed"):
            build_v3_feature(
                connection,
                _target(),
                START + timedelta(seconds=90),
                generated_at=GENERATED_AT,
            )


def test_generate_v3_features_is_immutable_on_exact_rerun() -> None:
    engine = _engine()
    with engine.begin() as connection:
        _seed_inputs(connection)
        first = generate_v3_features(
            connection,
            [_target()],
            generated_at=GENERATED_AT,
        )
        second = generate_v3_features(
            connection,
            [_target()],
            generated_at=GENERATED_AT + timedelta(seconds=1),
        )
        row_count = connection.execute(
            select(schema.market_features.c.id).where(
                schema.market_features.c.feature_version == V3_FEATURE_VERSION
            )
        ).all()

    assert first.targets_considered == 1
    assert first.planned_rows == 4
    assert first.inserted == 4
    assert first.existing == 0
    assert second.inserted == 0
    assert second.existing == 4
    assert len(row_count) == 4


def test_v3_service_has_no_v1_v2_or_polymarket_builder_dependency() -> None:
    from bp_engine.features import v3_service

    source = inspect.getsource(v3_service).lower()
    forbidden = (
        "build_v2_feature",
        "plan_v2_feature_times",
        "last_trade_features",
        "book_state(",
        "polymarket_prices",
        "pm_up",
        "pm_down",
        "market_labels",
        "official_outcome",
    )
    assert all(value not in source for value in forbidden)
