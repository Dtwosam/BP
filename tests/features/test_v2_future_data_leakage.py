from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, insert

from bp_engine.features.v2_models import V2FeatureTarget
from bp_engine.features.v2_service import build_v2_feature
from bp_engine.storage import schema

START = datetime(2026, 9, 2, 11, 0, tzinfo=UTC)
FEATURE_AT = START + timedelta(seconds=180)
END = START + timedelta(seconds=300)
CONDITION_ID = "condition-v2-leakage"
UP_TOKEN = "up-token-v2-leakage"
DOWN_TOKEN = "down-token-v2-leakage"


def _z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _target() -> V2FeatureTarget:
    return V2FeatureTarget(
        condition_id=CONDITION_ID,
        slug="btc-updown-5m-v2-leakage",
        horizon_seconds=300,
        market_start_at=START,
        market_end_at=END,
        up_token_id=UP_TOKEN,
        down_token_id=DOWN_TOKEN,
    )


def _state(*, price: str, source_at: datetime, received_at: datetime, dedupe: str) -> dict:
    return {
        "last_trade_price": price,
        "last_trade_size": "2",
        "last_trade_side": "BUY",
        "last_trade_source_at": _z(source_at),
        "last_trade_received_at": _z(received_at),
        "last_trade_event_dedupe_key": dedupe,
        "best_bid": str(float(price) - 0.01),
        "best_ask": str(float(price) + 0.01),
        "bid_depth": "7",
        "ask_depth": "6",
    }


def _insert(connection, *, asset_id: str, bucket_at: datetime, last_event_at: datetime, state: dict) -> None:
    connection.execute(
        insert(schema.market_state_1s).values(
            bucket_at=bucket_at,
            state_key=f"polymarket/market/{CONDITION_ID}/{asset_id}/{bucket_at.isoformat()}",
            source="polymarket",
            stream="market",
            instrument=CONDITION_ID,
            market_id=CONDITION_ID,
            asset_id=asset_id,
            last_event_at=last_event_at,
            state=state,
        )
    )


def test_post_feature_state_cannot_perturb_v2_snapshot_at_t() -> None:
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)

    with engine.begin() as connection:
        _insert(
            connection,
            asset_id=UP_TOKEN,
            bucket_at=FEATURE_AT - timedelta(seconds=1),
            last_event_at=FEATURE_AT - timedelta(seconds=1),
            state=_state(
                price="0.61",
                source_at=FEATURE_AT - timedelta(seconds=4),
                received_at=FEATURE_AT - timedelta(seconds=3),
                dedupe="sha256:" + "a" * 64,
            ),
        )
        _insert(
            connection,
            asset_id=DOWN_TOKEN,
            bucket_at=FEATURE_AT - timedelta(seconds=1),
            last_event_at=FEATURE_AT - timedelta(seconds=1),
            state=_state(
                price="0.39",
                source_at=FEATURE_AT - timedelta(seconds=5),
                received_at=FEATURE_AT - timedelta(seconds=4),
                dedupe="sha256:" + "b" * 64,
            ),
        )
        before = build_v2_feature(
            connection,
            _target(),
            FEATURE_AT,
            generated_at=FEATURE_AT + timedelta(seconds=10),
        )

        _insert(
            connection,
            asset_id=UP_TOKEN,
            bucket_at=FEATURE_AT + timedelta(seconds=1),
            last_event_at=FEATURE_AT + timedelta(seconds=1),
            state=_state(
                price="0.91",
                source_at=FEATURE_AT + timedelta(milliseconds=500),
                received_at=FEATURE_AT + timedelta(milliseconds=700),
                dedupe="sha256:" + "c" * 64,
            ),
        )
        after = build_v2_feature(
            connection,
            _target(),
            FEATURE_AT,
            generated_at=FEATURE_AT + timedelta(seconds=20),
        )

    assert after.features == before.features
    assert after.missing_flags == before.missing_flags
    assert after.source_cutoffs == before.source_cutoffs
    assert after.input_fingerprint == before.input_fingerprint
    assert after.feature_hash == before.feature_hash
