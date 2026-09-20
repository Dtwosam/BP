import inspect
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, insert, select

from bp_engine.features.v3_models import V3_FEATURE_VERSION
from bp_engine.features.v4_models import V4_FEATURE_VERSION, V4FeatureTarget
from bp_engine.features.v4_service import (
    build_v4_feature,
    generate_v4_features,
    plan_v4_feature_times,
)
from bp_engine.storage import schema

START = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
END = START + timedelta(seconds=300)
FEATURE_AT = START + timedelta(seconds=180)
GENERATED_AT = END + timedelta(seconds=30)


def _engine():
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    return engine


def _target() -> V4FeatureTarget:
    return V4FeatureTarget(
        condition_id="condition-v4-service",
        slug="btc-updown-5m-v4-service",
        horizon_seconds=300,
        market_start_at=START,
        market_end_at=END,
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
    prices: dict[str, str],
    current_extra: dict[str, object] | None = None,
) -> None:
    times = {
        "60m": FEATURE_AT - timedelta(minutes=60, seconds=1),
        "15m": FEATURE_AT - timedelta(minutes=15, seconds=1),
        "5m": FEATURE_AT - timedelta(minutes=5, seconds=1),
        "start": START - timedelta(seconds=1),
        "120": FEATURE_AT - timedelta(seconds=121),
        "60": FEATURE_AT - timedelta(seconds=61),
        "30": FEATURE_AT - timedelta(seconds=31),
        "current": FEATURE_AT - timedelta(seconds=1),
    }
    for name, effective_at in times.items():
        _insert_state(
            connection,
            source=source,
            stream=stream,
            instrument=instrument,
            effective_at=effective_at,
            price=prices[name],
            state_extra=current_extra if name == "current" else None,
        )


def _seed_bull_inputs(connection) -> None:
    for source, stream, instrument, base in (
        ("coinbase", "spot", "BTC-USD", 100.0),
        ("bybit", "spot", "BTCUSDT", 200.0),
        ("bybit", "linear", "BTCUSDT", 201.0),
    ):
        prices = {
            "60m": str(base * 0.96),
            "15m": str(base * 0.98),
            "5m": str(base * 0.99),
            "start": str(base),
            "120": str(base * 1.01),
            "60": str(base * 1.02),
            "30": str(base * 1.03),
            "current": str(base * 1.04),
        }
        _seed_venue(
            connection,
            source=source,
            stream=stream,
            instrument=instrument,
            prices=prices,
            current_extra=(
                {"funding_rate": "0.0001", "open_interest": "12345"}
                if stream == "linear"
                else None
            ),
        )


def test_v4_planner_keeps_the_four_5m_decision_offsets() -> None:
    offsets = tuple(
        int((value - START).total_seconds())
        for value in plan_v4_feature_times(_target())
    )
    assert offsets == (60, 120, 180, 240)


def test_v4_feature_adds_long_horizon_regime_context_without_polymarket_predictors() -> None:
    engine = _engine()
    with engine.begin() as connection:
        _seed_bull_inputs(connection)
        feature = build_v4_feature(
            connection,
            _target(),
            FEATURE_AT,
            generated_at=GENERATED_AT,
        )

    assert feature.feature_version == V4_FEATURE_VERSION
    assert V4_FEATURE_VERSION != V3_FEATURE_VERSION
    assert feature.features["coinbase_return_5m"] > 0
    assert feature.features["coinbase_return_15m"] > 0
    assert feature.features["coinbase_return_60m"] > 0
    assert feature.features["regime_bull"] == 1.0
    assert feature.features["regime_bear"] == 0.0
    assert feature.features["regime_sideways_mixed"] == 0.0
    assert feature.features["regime_trend_score"] == 1.0
    assert all(value is False for value in feature.missing_flags.values())
    feature_at_z = FEATURE_AT.isoformat().replace("+00:00", "Z")
    assert all(value <= feature_at_z for value in feature.source_cutoffs.values())

    keys = set(feature.features) | set(feature.missing_flags) | set(feature.source_cutoffs)
    assert not any(key.startswith("pm_") for key in keys)
    assert not any("polymarket" in key.lower() for key in keys)


def test_v4_future_state_does_not_change_as_of_feature() -> None:
    engine = _engine()
    with engine.begin() as connection:
        _seed_bull_inputs(connection)
        before = build_v4_feature(
            connection,
            _target(),
            FEATURE_AT,
            generated_at=GENERATED_AT,
        )
        _insert_state(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            effective_at=FEATURE_AT + timedelta(seconds=1),
            price="999999",
        )
        after = build_v4_feature(
            connection,
            _target(),
            FEATURE_AT,
            generated_at=GENERATED_AT + timedelta(seconds=1),
        )

    assert before.feature_hash == after.feature_hash
    assert before.input_fingerprint == after.input_fingerprint
    assert before.features == after.features


def test_generate_v4_features_is_immutable_on_exact_rerun() -> None:
    engine = _engine()
    with engine.begin() as connection:
        _seed_bull_inputs(connection)
        first = generate_v4_features(
            connection,
            [_target()],
            generated_at=GENERATED_AT,
        )
        second = generate_v4_features(
            connection,
            [_target()],
            generated_at=GENERATED_AT + timedelta(seconds=1),
        )
        rows = connection.execute(
            select(schema.market_features.c.id).where(
                schema.market_features.c.feature_version == V4_FEATURE_VERSION
            )
        ).all()

    assert first.inserted == 4
    assert first.existing == 0
    assert second.inserted == 0
    assert second.existing == 4
    assert len(rows) == 4


def test_v4_service_does_not_read_labels_or_polymarket_prices() -> None:
    from bp_engine.features import v4_service

    source = inspect.getsource(v4_service).lower()
    forbidden = (
        "market_labels",
        "official_outcome",
        "polymarket_price",
        "pm_up",
        "pm_down",
        "build_v3_feature",
    )
    assert all(value not in source for value in forbidden)
