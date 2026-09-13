from datetime import UTC, datetime, timedelta

from bp_engine.features.v3_service import build_v3_feature
from sqlalchemy import create_engine, insert

from bp_engine.features.v3_models import V3FeatureTarget
from bp_engine.storage import schema

START = datetime(2026, 9, 13, 13, 0, tzinfo=UTC)
FEATURE_AT = START + timedelta(seconds=180)
END = START + timedelta(seconds=300)
GENERATED_AT = END + timedelta(seconds=30)


def _engine():
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    return engine


def _target() -> V3FeatureTarget:
    return V3FeatureTarget(
        condition_id="condition-v3-future-leakage",
        slug="btc-updown-5m-v3-future-leakage",
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


def _seed_historical_inputs(connection) -> None:
    identities = (
        ("coinbase", "spot", "BTC-USD", 100.0),
        ("bybit", "spot", "BTCUSDT", 200.0),
        ("bybit", "linear", "BTCUSDT", 200.5),
    )
    offsets = (-1, 59, 119, 149, 179)
    for source, stream, instrument, base in identities:
        for index, offset in enumerate(offsets):
            extra = None
            if stream == "linear" and index == len(offsets) - 1:
                extra = {"funding_rate": "0.0005", "open_interest": "5000"}
            _insert_state(
                connection,
                source=source,
                stream=stream,
                instrument=instrument,
                effective_at=START + timedelta(seconds=offset),
                price=str(base + index),
                state_extra=extra,
            )


def _insert_future_perturbations(connection) -> None:
    for source, stream, instrument in (
        ("coinbase", "spot", "BTC-USD"),
        ("bybit", "spot", "BTCUSDT"),
        ("bybit", "linear", "BTCUSDT"),
    ):
        _insert_state(
            connection,
            source=source,
            stream=stream,
            instrument=instrument,
            effective_at=FEATURE_AT + timedelta(seconds=1),
            price="999999",
            state_extra={
                "funding_rate": "9.9",
                "open_interest": "999999999",
            }
            if stream == "linear"
            else None,
        )


def test_future_state_rows_cannot_change_historical_v3_feature() -> None:
    engine = _engine()
    with engine.begin() as connection:
        _seed_historical_inputs(connection)
        before = build_v3_feature(
            connection,
            _target(),
            FEATURE_AT,
            generated_at=GENERATED_AT,
        )
        _insert_future_perturbations(connection)
        after = build_v3_feature(
            connection,
            _target(),
            FEATURE_AT,
            generated_at=GENERATED_AT + timedelta(seconds=1),
        )

    assert after.features == before.features
    assert after.missing_flags == before.missing_flags
    assert after.source_cutoffs == before.source_cutoffs
    assert after.input_fingerprint == before.input_fingerprint
    assert after.feature_hash == before.feature_hash
