from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bp_engine.features.v3_calculators import (
    BTCAnchorSet,
    btc_cross_venue_group,
    btc_return_group,
)
from bp_engine.features.v3_models import BTCStateObservation

NOW = datetime(2026, 9, 13, 12, 4, tzinfo=UTC)


def _observation(
    row_id: int,
    *,
    price: str,
    source: str = "coinbase",
    stream: str = "spot",
    instrument: str = "BTC-USD",
    effective_seconds_ago: int = 0,
    fresh: bool = True,
    state_extra: dict[str, object] | None = None,
) -> BTCStateObservation:
    state = {"last_price": price}
    state.update(state_extra or {})
    effective_at = NOW - timedelta(seconds=effective_seconds_ago)
    return BTCStateObservation(
        row_id=row_id,
        bucket_at=effective_at,
        last_event_at=effective_at,
        source=source,
        stream=stream,
        instrument=instrument,
        price=Decimal(price),
        state=state,
        fresh=fresh,
        age_seconds=float(effective_seconds_ago),
    )


def _anchors(
    *,
    prefix: str = "coinbase",
    current: str = "101",
    market_start: str = "100",
    trailing_120s: str = "99",
    trailing_60s: str = "102",
    trailing_30s: str = "101.5",
    linear_extra: dict[str, object] | None = None,
) -> BTCAnchorSet:
    if prefix == "coinbase":
        source, stream, instrument = "coinbase", "spot", "BTC-USD"
    elif prefix == "bybit_spot":
        source, stream, instrument = "bybit", "spot", "BTCUSDT"
    else:
        source, stream, instrument = "bybit", "linear", "BTCUSDT"
    return BTCAnchorSet(
        market_start=_observation(
            1,
            price=market_start,
            source=source,
            stream=stream,
            instrument=instrument,
            effective_seconds_ago=240,
        ),
        trailing_120s=_observation(
            2,
            price=trailing_120s,
            source=source,
            stream=stream,
            instrument=instrument,
            effective_seconds_ago=120,
        ),
        trailing_60s=_observation(
            3,
            price=trailing_60s,
            source=source,
            stream=stream,
            instrument=instrument,
            effective_seconds_ago=60,
        ),
        trailing_30s=_observation(
            4,
            price=trailing_30s,
            source=source,
            stream=stream,
            instrument=instrument,
            effective_seconds_ago=30,
        ),
        current=_observation(
            5,
            price=current,
            source=source,
            stream=stream,
            instrument=instrument,
            state_extra=linear_extra,
        ),
    )


def test_return_group_uses_exact_market_and_trailing_anchors() -> None:
    group = btc_return_group("coinbase", _anchors())

    assert group.values == {
        "coinbase_return_from_market_start": pytest.approx(0.01),
        "coinbase_return_30s": pytest.approx(101 / 101.5 - 1),
        "coinbase_return_60s": pytest.approx(101 / 102 - 1),
        "coinbase_return_120s": pytest.approx(101 / 99 - 1),
    }
    assert all(value is False for value in group.missing_flags.values())


def test_return_group_suppresses_missing_or_stale_anchor_without_fallback() -> None:
    anchors = _anchors()
    stale_60s = BTCStateObservation(
        **{
            **anchors.trailing_60s.__dict__,
            "fresh": False,
            "age_seconds": 11.0,
        }
    )
    partial = BTCAnchorSet(
        market_start=None,
        trailing_120s=anchors.trailing_120s,
        trailing_60s=stale_60s,
        trailing_30s=anchors.trailing_30s,
        current=anchors.current,
    )

    group = btc_return_group("coinbase", partial)

    assert group.values["coinbase_return_from_market_start"] is None
    assert group.values["coinbase_return_60s"] is None
    assert group.values["coinbase_return_30s"] is not None
    assert group.values["coinbase_return_120s"] is not None
    assert group.missing_flags["coinbase_market_start_missing"] is True
    assert group.missing_flags["coinbase_trailing_60s_stale"] is True


def test_stale_current_suppresses_every_return() -> None:
    anchors = _anchors()
    stale_current = BTCStateObservation(
        **{
            **anchors.current.__dict__,
            "fresh": False,
            "age_seconds": 12.0,
        }
    )
    group = btc_return_group(
        "coinbase",
        BTCAnchorSet(
            market_start=anchors.market_start,
            trailing_120s=anchors.trailing_120s,
            trailing_60s=anchors.trailing_60s,
            trailing_30s=anchors.trailing_30s,
            current=stale_current,
        ),
    )

    assert all(value is None for value in group.values.values())
    assert group.missing_flags["coinbase_current_stale"] is True


def test_return_group_provenance_records_exact_selected_rows_and_cutoffs() -> None:
    anchors = _anchors()
    group = btc_return_group("coinbase", anchors)

    assert group.source_cutoffs == {
        "coinbase_market_start_state": NOW - timedelta(seconds=240),
        "coinbase_trailing_120s_state": NOW - timedelta(seconds=120),
        "coinbase_trailing_60s_state": NOW - timedelta(seconds=60),
        "coinbase_trailing_30s_state": NOW - timedelta(seconds=30),
        "coinbase_current_state": NOW,
    }
    assert [item["row_id"] for item in group.observations] == [1, 2, 3, 4, 5]
    assert group.observations[-1] == {
        "kind": "btc_compact_state",
        "row_id": 5,
        "bucket_at": NOW,
        "last_event_at": NOW,
        "source": "coinbase",
        "stream": "spot",
        "instrument": "BTC-USD",
        "price": Decimal("101"),
        "fresh": True,
        "age_seconds": 0.0,
    }


def test_cross_venue_group_calculates_spread_agreement_and_derivatives() -> None:
    coinbase = _anchors(current="103", market_start="100")
    bybit_spot = _anchors(prefix="bybit_spot", current="204", market_start="200")
    bybit_linear = _anchors(
        prefix="bybit_linear",
        current="205",
        market_start="200",
        linear_extra={"funding_rate": "-0.001", "open_interest": "12345.5"},
    )

    group = btc_cross_venue_group(coinbase, bybit_spot, bybit_linear)

    assert group.values["coinbase_bybit_spot_return_spread"] == pytest.approx(0.03 - 0.02)
    assert group.values["coinbase_bybit_spot_direction_agree"] == 1.0
    assert group.values["spot_linear_direction_agree"] == 1.0
    assert group.values["bybit_linear_vs_spot_basis"] == pytest.approx(205 / 204 - 1)
    assert group.values["bybit_linear_funding_rate"] == pytest.approx(-0.001)
    assert group.values["bybit_linear_open_interest"] == pytest.approx(12345.5)
    assert all(value is False for value in group.missing_flags.values())
    assert group.source_cutoffs == {}
    assert group.observations == ()


def test_direction_agreement_is_zero_for_opposite_signs_and_missing_for_zero() -> None:
    coinbase = _anchors(current="101", market_start="100")
    bybit_spot = _anchors(prefix="bybit_spot", current="99", market_start="100")
    bybit_linear = _anchors(prefix="bybit_linear", current="100", market_start="100")

    group = btc_cross_venue_group(coinbase, bybit_spot, bybit_linear)

    assert group.values["coinbase_bybit_spot_direction_agree"] == 0.0
    assert group.values["spot_linear_direction_agree"] is None
    assert group.missing_flags["spot_linear_direction_agree_missing"] is True


def test_cross_venue_group_fails_closed_on_missing_or_stale_current_state() -> None:
    coinbase = _anchors()
    bybit_spot = _anchors(prefix="bybit_spot")
    bybit_linear = _anchors(prefix="bybit_linear")
    stale_linear = BTCStateObservation(
        **{
            **bybit_linear.current.__dict__,
            "fresh": False,
            "age_seconds": 20.0,
        }
    )
    group = btc_cross_venue_group(
        coinbase,
        bybit_spot,
        BTCAnchorSet(
            market_start=bybit_linear.market_start,
            trailing_120s=bybit_linear.trailing_120s,
            trailing_60s=bybit_linear.trailing_60s,
            trailing_30s=bybit_linear.trailing_30s,
            current=stale_linear,
        ),
    )

    assert group.values["bybit_linear_vs_spot_basis"] is None
    assert group.values["bybit_linear_funding_rate"] is None
    assert group.values["bybit_linear_open_interest"] is None


def test_v3_calculator_keys_never_include_polymarket_predictors() -> None:
    groups = (
        btc_return_group("coinbase", _anchors()),
        btc_return_group("bybit_spot", _anchors(prefix="bybit_spot")),
        btc_return_group("bybit_linear", _anchors(prefix="bybit_linear")),
        btc_cross_venue_group(
            _anchors(),
            _anchors(prefix="bybit_spot"),
            _anchors(prefix="bybit_linear"),
        ),
    )
    keys = {
        key
        for group in groups
        for key in (*group.values, *group.missing_flags, *group.source_cutoffs)
    }

    assert not any(key.startswith("pm_") for key in keys)
    assert not any("polymarket" in key for key in keys)
