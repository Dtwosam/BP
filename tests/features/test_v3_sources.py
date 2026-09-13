from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, insert

from bp_engine.features.v3_models import V3_FEATURE_VERSION, BTCStateObservation
from bp_engine.features.v3_sources import V3FeatureSourceReader
from bp_engine.storage import schema

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


def _engine():
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    return engine


def _insert_state(
    connection,
    *,
    source: str,
    stream: str,
    instrument: str,
    bucket_at: datetime,
    last_event_at: datetime,
    price: object = "100000.0",
    state_extra: dict[str, object] | None = None,
) -> int:
    state = {"last_price": price}
    state.update(state_extra or {})
    result = connection.execute(
        insert(schema.market_state_1s).values(
            bucket_at=bucket_at,
            state_key=f"{source}/{stream}/{instrument}",
            source=source,
            stream=stream,
            instrument=instrument,
            market_id=None,
            asset_id=None,
            last_event_at=last_event_at,
            state=state,
        )
    )
    return int(result.inserted_primary_key[0])


def test_v3_feature_version_is_separate() -> None:
    assert V3_FEATURE_VERSION == "core-v3-btc-native"


def test_latest_btc_state_returns_exact_coinbase_observation() -> None:
    engine = _engine()
    with engine.begin() as connection:
        row_id = _insert_state(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            bucket_at=NOW - timedelta(seconds=1),
            last_event_at=NOW - timedelta(milliseconds=250),
            price="101000.25",
            state_extra={"best_bid": "101000.0", "best_ask": "101000.5"},
        )
        observation = V3FeatureSourceReader().latest_btc_state(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            as_of=NOW,
        )

    assert observation == BTCStateObservation(
        row_id=row_id,
        bucket_at=NOW - timedelta(seconds=1),
        last_event_at=NOW - timedelta(milliseconds=250),
        source="coinbase",
        stream="spot",
        instrument="BTC-USD",
        price=Decimal("101000.25"),
        state={
            "last_price": "101000.25",
            "best_bid": "101000.0",
            "best_ask": "101000.5",
        },
        fresh=True,
        age_seconds=0.25,
    )


def test_future_bucket_or_event_is_never_selected() -> None:
    engine = _engine()
    with engine.begin() as connection:
        old_id = _insert_state(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            bucket_at=NOW - timedelta(seconds=2),
            last_event_at=NOW - timedelta(seconds=1),
            price="100000",
        )
        _insert_state(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            bucket_at=NOW + timedelta(milliseconds=1),
            last_event_at=NOW - timedelta(milliseconds=1),
            price="200000",
        )
        _insert_state(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            bucket_at=NOW - timedelta(milliseconds=1),
            last_event_at=NOW + timedelta(milliseconds=1),
            price="300000",
        )
        observation = V3FeatureSourceReader().latest_btc_state(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            as_of=NOW,
        )

    assert observation is not None
    assert observation.row_id == old_id
    assert observation.price == Decimal("100000")


def test_coinbase_bybit_spot_and_linear_identities_are_isolated() -> None:
    engine = _engine()
    cases = (
        ("coinbase", "spot", "BTC-USD", "101000"),
        ("bybit", "spot", "BTCUSDT", "102000"),
        ("bybit", "linear", "BTCUSDT", "103000"),
    )
    with engine.begin() as connection:
        for source, stream, instrument, price in cases:
            _insert_state(
                connection,
                source=source,
                stream=stream,
                instrument=instrument,
                bucket_at=NOW - timedelta(seconds=1),
                last_event_at=NOW - timedelta(milliseconds=100),
                price=price,
            )
        observations = [
            V3FeatureSourceReader().latest_btc_state(
                connection,
                source=source,
                stream=stream,
                instrument=instrument,
                as_of=NOW,
            )
            for source, stream, instrument, _ in cases
        ]

    assert [value.price for value in observations if value is not None] == [
        Decimal("101000"),
        Decimal("102000"),
        Decimal("103000"),
    ]


def test_latest_eligible_bucket_is_selected_deterministically() -> None:
    engine = _engine()
    with engine.begin() as connection:
        _insert_state(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            bucket_at=NOW - timedelta(seconds=2),
            last_event_at=NOW - timedelta(seconds=2),
            price="100000",
        )
        newest_id = _insert_state(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            bucket_at=NOW - timedelta(seconds=1),
            last_event_at=NOW - timedelta(milliseconds=100),
            price="100100",
        )
        observation = V3FeatureSourceReader().latest_btc_state(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            as_of=NOW,
        )

    assert observation is not None
    assert observation.row_id == newest_id
    assert observation.price == Decimal("100100")


def test_invalid_or_missing_price_makes_anchor_unavailable() -> None:
    invalid_prices = (None, "", "nan", "inf", "0", "-1", "not-a-number")
    for price in invalid_prices:
        engine = _engine()
        with engine.begin() as connection:
            _insert_state(
                connection,
                source="coinbase",
                stream="spot",
                instrument="BTC-USD",
                bucket_at=NOW - timedelta(seconds=1),
                last_event_at=NOW - timedelta(milliseconds=100),
                price=price,
            )
            observation = V3FeatureSourceReader().latest_btc_state(
                connection,
                source="coinbase",
                stream="spot",
                instrument="BTC-USD",
                as_of=NOW,
            )
        assert observation is None, price


def test_freshness_and_age_use_existing_state_semantics() -> None:
    engine = _engine()
    with engine.begin() as connection:
        _insert_state(
            connection,
            source="bybit",
            stream="linear",
            instrument="BTCUSDT",
            bucket_at=NOW - timedelta(seconds=15),
            last_event_at=NOW - timedelta(seconds=12),
            price="100500",
        )
        observation = V3FeatureSourceReader().latest_btc_state(
            connection,
            source="bybit",
            stream="linear",
            instrument="BTCUSDT",
            as_of=NOW,
        )

    assert observation is not None
    assert observation.fresh is False
    assert observation.age_seconds == 12.0


def test_later_insert_does_not_change_historical_as_of_selection() -> None:
    engine = _engine()
    with engine.begin() as connection:
        original_id = _insert_state(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            bucket_at=NOW - timedelta(seconds=1),
            last_event_at=NOW - timedelta(milliseconds=100),
            price="100000",
        )
        before = V3FeatureSourceReader().latest_btc_state(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            as_of=NOW,
        )
        _insert_state(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            bucket_at=NOW + timedelta(seconds=1),
            last_event_at=NOW + timedelta(seconds=1),
            price="110000",
        )
        after = V3FeatureSourceReader().latest_btc_state(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            as_of=NOW,
        )

    assert before is not None and after is not None
    assert before.row_id == original_id
    assert after == before
