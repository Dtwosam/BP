from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, insert

from bp_engine.features.v2_models import LastTradeObservation, V2_FEATURE_VERSION
from bp_engine.features.v2_sources import V2FeatureSourceReader
from bp_engine.storage import schema

NOW = datetime(2026, 9, 2, 9, 20, tzinfo=UTC)
CONDITION_ID = "condition-v2"
UP_TOKEN = "up-token-v2"
DOWN_TOKEN = "down-token-v2"


def _z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _engine():
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    return engine


def _state(
    *,
    price: str = "0.61",
    source_at: datetime | str | None = None,
    received_at: datetime | str | None = None,
    dedupe_key: str | None = "sha256:" + "a" * 64,
    size: str | None = "12.5",
    side: str | None = "BUY",
) -> dict[str, object]:
    source_value = NOW - timedelta(seconds=8) if source_at is None else source_at
    received_value = NOW - timedelta(seconds=7) if received_at is None else received_at
    return {
        "last_trade_price": price,
        "last_trade_size": size,
        "last_trade_side": side,
        "last_trade_source_at": (
            _z(source_value) if isinstance(source_value, datetime) else source_value
        ),
        "last_trade_received_at": (
            _z(received_value) if isinstance(received_value, datetime) else received_value
        ),
        "last_trade_event_dedupe_key": dedupe_key,
        "best_bid": "0.60",
        "best_ask": "0.62",
    }


def _insert_state(
    connection,
    *,
    asset_id: str,
    bucket_at: datetime,
    last_event_at: datetime,
    state: dict[str, object],
) -> int:
    result = connection.execute(
        insert(schema.market_state_1s).values(
            bucket_at=bucket_at,
            state_key=f"polymarket/market/{CONDITION_ID}/{asset_id}",
            source="polymarket",
            stream="market",
            instrument=CONDITION_ID,
            market_id=CONDITION_ID,
            asset_id=asset_id,
            last_event_at=last_event_at,
            state=state,
        )
    )
    return int(result.inserted_primary_key[0])


def test_v2_feature_version_is_separate_from_core_v1() -> None:
    assert V2_FEATURE_VERSION == "core-v2-last-trade"


def test_latest_last_trade_returns_complete_exact_token_observation() -> None:
    engine = _engine()
    source_at = NOW - timedelta(seconds=8)
    received_at = NOW - timedelta(seconds=7)

    with engine.begin() as connection:
        up_id = _insert_state(
            connection,
            asset_id=UP_TOKEN,
            bucket_at=NOW - timedelta(seconds=1),
            last_event_at=NOW - timedelta(milliseconds=250),
            state=_state(source_at=source_at, received_at=received_at),
        )
        _insert_state(
            connection,
            asset_id=DOWN_TOKEN,
            bucket_at=NOW - timedelta(seconds=1),
            last_event_at=NOW - timedelta(milliseconds=200),
            state=_state(
                price="0.39",
                source_at=NOW - timedelta(seconds=3),
                received_at=NOW - timedelta(seconds=2),
                dedupe_key="sha256:" + "b" * 64,
            ),
        )

        observation = V2FeatureSourceReader().latest_polymarket_last_trade(
            connection,
            condition_id=CONDITION_ID,
            asset_id=UP_TOKEN,
            feature_at=NOW,
        )

    assert observation == LastTradeObservation(
        compact_state_row_id=up_id,
        compact_state_bucket_at=NOW - timedelta(seconds=1),
        compact_state_last_event_at=NOW - timedelta(milliseconds=250),
        asset_id=UP_TOKEN,
        price=Decimal("0.61"),
        size=Decimal("12.5"),
        side="BUY",
        source_at=source_at,
        received_at=received_at,
        event_dedupe_key="sha256:" + "a" * 64,
    )


def test_generic_fresh_activity_does_not_refresh_dedicated_trade_time() -> None:
    engine = _engine()
    source_at = NOW - timedelta(seconds=45)
    received_at = NOW - timedelta(seconds=44)

    with engine.begin() as connection:
        _insert_state(
            connection,
            asset_id=UP_TOKEN,
            bucket_at=NOW - timedelta(seconds=1),
            last_event_at=NOW - timedelta(milliseconds=100),
            state=_state(source_at=source_at, received_at=received_at),
        )

        observation = V2FeatureSourceReader().latest_polymarket_last_trade(
            connection,
            condition_id=CONDITION_ID,
            asset_id=UP_TOKEN,
            feature_at=NOW,
        )

    assert observation is not None
    assert observation.source_at == source_at
    assert observation.received_at == received_at
    assert observation.compact_state_last_event_at == NOW - timedelta(milliseconds=100)
    assert (NOW - observation.received_at).total_seconds() == 44.0


def test_incomplete_dedicated_trade_provenance_fails_closed() -> None:
    required_keys = (
        "last_trade_price",
        "last_trade_source_at",
        "last_trade_received_at",
        "last_trade_event_dedupe_key",
    )

    for missing_key in required_keys:
        engine = _engine()
        state = _state()
        state.pop(missing_key)
        with engine.begin() as connection:
            _insert_state(
                connection,
                asset_id=UP_TOKEN,
                bucket_at=NOW - timedelta(seconds=1),
                last_event_at=NOW - timedelta(milliseconds=100),
                state=state,
            )
            observation = V2FeatureSourceReader().latest_polymarket_last_trade(
                connection,
                condition_id=CONDITION_ID,
                asset_id=UP_TOKEN,
                feature_at=NOW,
            )
        assert observation is None, missing_key


def test_null_provider_timestamp_is_not_v2_probability_evidence() -> None:
    engine = _engine()
    state = _state()
    state["last_trade_source_at"] = None

    with engine.begin() as connection:
        _insert_state(
            connection,
            asset_id=UP_TOKEN,
            bucket_at=NOW - timedelta(seconds=1),
            last_event_at=NOW - timedelta(milliseconds=100),
            state=state,
        )
        observation = V2FeatureSourceReader().latest_polymarket_last_trade(
            connection,
            condition_id=CONDITION_ID,
            asset_id=UP_TOKEN,
            feature_at=NOW,
        )

    assert observation is None


def test_malformed_or_naive_serialized_trade_timestamp_fails_closed() -> None:
    invalid_values = ("not-a-time", "2026-09-02T09:19:52")

    for invalid in invalid_values:
        engine = _engine()
        state = _state()
        state["last_trade_source_at"] = invalid
        with engine.begin() as connection:
            _insert_state(
                connection,
                asset_id=UP_TOKEN,
                bucket_at=NOW - timedelta(seconds=1),
                last_event_at=NOW - timedelta(milliseconds=100),
                state=state,
            )
            observation = V2FeatureSourceReader().latest_polymarket_last_trade(
                connection,
                condition_id=CONDITION_ID,
                asset_id=UP_TOKEN,
                feature_at=NOW,
            )
        assert observation is None, invalid


def test_future_dedicated_source_or_receipt_time_fails_closed() -> None:
    future_cases = (
        _state(source_at=NOW + timedelta(milliseconds=1)),
        _state(received_at=NOW + timedelta(milliseconds=1)),
    )

    for state in future_cases:
        engine = _engine()
        with engine.begin() as connection:
            _insert_state(
                connection,
                asset_id=UP_TOKEN,
                bucket_at=NOW - timedelta(seconds=1),
                last_event_at=NOW - timedelta(milliseconds=100),
                state=state,
            )
            observation = V2FeatureSourceReader().latest_polymarket_last_trade(
                connection,
                condition_id=CONDITION_ID,
                asset_id=UP_TOKEN,
                feature_at=NOW,
            )
        assert observation is None


def test_post_feature_compact_row_cannot_change_as_of_last_trade_selection() -> None:
    engine = _engine()
    old_source_at = NOW - timedelta(seconds=8)
    old_received_at = NOW - timedelta(seconds=7)

    with engine.begin() as connection:
        old_id = _insert_state(
            connection,
            asset_id=UP_TOKEN,
            bucket_at=NOW - timedelta(seconds=1),
            last_event_at=NOW - timedelta(milliseconds=100),
            state=_state(source_at=old_source_at, received_at=old_received_at),
        )
        _insert_state(
            connection,
            asset_id=UP_TOKEN,
            bucket_at=NOW + timedelta(seconds=1),
            last_event_at=NOW + timedelta(seconds=1, milliseconds=100),
            state=_state(
                price="0.99",
                source_at=NOW + timedelta(milliseconds=500),
                received_at=NOW + timedelta(milliseconds=600),
                dedupe_key="sha256:" + "c" * 64,
            ),
        )

        observation = V2FeatureSourceReader().latest_polymarket_last_trade(
            connection,
            condition_id=CONDITION_ID,
            asset_id=UP_TOKEN,
            feature_at=NOW,
        )

    assert observation is not None
    assert observation.compact_state_row_id == old_id
    assert observation.price == Decimal("0.61")
    assert observation.source_at == old_source_at
    assert observation.received_at == old_received_at


def test_invalid_last_trade_price_fails_closed() -> None:
    for price in ("-0.01", "1.01", "NaN", "Infinity", "bad"):
        engine = _engine()
        with engine.begin() as connection:
            _insert_state(
                connection,
                asset_id=UP_TOKEN,
                bucket_at=NOW - timedelta(seconds=1),
                last_event_at=NOW - timedelta(milliseconds=100),
                state=_state(price=price),
            )
            observation = V2FeatureSourceReader().latest_polymarket_last_trade(
                connection,
                condition_id=CONDITION_ID,
                asset_id=UP_TOKEN,
                feature_at=NOW,
            )
        assert observation is None, price
