from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bp_engine.features.v2_calculators import last_trade_features

from bp_engine.features.v2_models import LastTradeObservation

FEATURE_AT = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


def _observation() -> LastTradeObservation:
    return LastTradeObservation(
        compact_state_row_id=17,
        compact_state_bucket_at=FEATURE_AT - timedelta(seconds=1),
        compact_state_last_event_at=FEATURE_AT - timedelta(milliseconds=500),
        asset_id="up-token",
        price=Decimal("0.61"),
        size=Decimal("12.5"),
        side="BUY",
        source_at=FEATURE_AT - timedelta(seconds=4),
        received_at=FEATURE_AT - timedelta(seconds=3),
        event_dedupe_key="sha256:" + "a" * 64,
    )


def test_last_trade_features_preserve_price_and_two_distinct_ages() -> None:
    group = last_trade_features("pm_up", _observation(), FEATURE_AT)

    assert group.values == {
        "pm_up_last_trade_price": 0.61,
        "pm_up_last_trade_source_age_s": 4.0,
        "pm_up_last_trade_availability_age_s": 3.0,
    }
    assert group.missing_flags == {"pm_up_last_trade_missing": False}
    assert group.source_cutoffs == {
        "pm_up_last_trade_source": FEATURE_AT - timedelta(seconds=4),
        "pm_up_last_trade_received": FEATURE_AT - timedelta(seconds=3),
    }

    assert group.observations == (
        {
            "kind": "polymarket_last_trade",
            "asset_id": "up-token",
            "price": Decimal("0.61"),
            "size": Decimal("12.5"),
            "side": "BUY",
            "source_at": FEATURE_AT - timedelta(seconds=4),
            "received_at": FEATURE_AT - timedelta(seconds=3),
            "event_dedupe_key": "sha256:" + "a" * 64,
            "compact_state_row_id": 17,
            "compact_state_bucket_at": FEATURE_AT - timedelta(seconds=1),
            "compact_state_last_event_at": FEATURE_AT - timedelta(milliseconds=500),
        },
    )


def test_missing_last_trade_is_explicit_and_null() -> None:
    group = last_trade_features("pm_down", None, FEATURE_AT)

    assert group.values == {
        "pm_down_last_trade_price": None,
        "pm_down_last_trade_source_age_s": None,
        "pm_down_last_trade_availability_age_s": None,
    }
    assert group.missing_flags == {"pm_down_last_trade_missing": True}
    assert group.source_cutoffs == {}
    assert group.observations == ()


def test_last_trade_calculator_rejects_future_evidence() -> None:
    observation = _observation()
    future_source = LastTradeObservation(
        **{
            **observation.__dict__,
            "source_at": FEATURE_AT + timedelta(microseconds=1),
        }
    )
    future_receipt = LastTradeObservation(
        **{
            **observation.__dict__,
            "received_at": FEATURE_AT + timedelta(microseconds=1),
        }
    )

    for candidate in (future_source, future_receipt):
        try:
            last_trade_features("pm_up", candidate, FEATURE_AT)
        except ValueError:
            pass
        else:
            raise AssertionError("future last-trade evidence must fail closed")
