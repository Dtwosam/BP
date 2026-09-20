from bp_engine.features.v4_calculators import regime_consensus_group


def _venue(r5: float, r15: float, r60: float) -> dict[str, float]:
    return {
        "return_5m": r5,
        "return_15m": r15,
        "return_60m": r60,
    }


def test_regime_consensus_marks_bull_only_when_all_horizons_are_up() -> None:
    group = regime_consensus_group(
        coinbase=_venue(0.001, 0.002, 0.003),
        bybit_spot=_venue(0.0011, 0.0019, 0.0028),
        bybit_linear=_venue(0.0009, 0.0021, 0.0032),
    )

    assert group.values["regime_5m_direction"] == 1.0
    assert group.values["regime_15m_direction"] == 1.0
    assert group.values["regime_60m_direction"] == 1.0
    assert group.values["regime_bull"] == 1.0
    assert group.values["regime_bear"] == 0.0
    assert group.values["regime_sideways_mixed"] == 0.0
    assert group.values["regime_trend_score"] == 1.0


def test_regime_consensus_marks_bear_only_when_all_horizons_are_down() -> None:
    group = regime_consensus_group(
        coinbase=_venue(-0.001, -0.002, -0.003),
        bybit_spot=_venue(-0.0011, -0.0019, -0.0028),
        bybit_linear=_venue(-0.0009, -0.0021, -0.0032),
    )

    assert group.values["regime_bull"] == 0.0
    assert group.values["regime_bear"] == 1.0
    assert group.values["regime_sideways_mixed"] == 0.0
    assert group.values["regime_trend_score"] == -1.0


def test_regime_consensus_calls_mixed_horizons_sideways_without_tuned_cutoff() -> None:
    group = regime_consensus_group(
        coinbase=_venue(0.001, -0.002, 0.003),
        bybit_spot=_venue(0.0011, -0.0019, 0.0028),
        bybit_linear=_venue(0.0009, -0.0021, 0.0032),
    )

    assert group.values["regime_5m_direction"] == 1.0
    assert group.values["regime_15m_direction"] == -1.0
    assert group.values["regime_60m_direction"] == 1.0
    assert group.values["regime_bull"] == 0.0
    assert group.values["regime_bear"] == 0.0
    assert group.values["regime_sideways_mixed"] == 1.0
    assert group.values["regime_trend_score"] == 1 / 3


def test_regime_consensus_fails_missing_when_two_venue_votes_are_not_available() -> None:
    group = regime_consensus_group(
        coinbase={"return_5m": 0.001, "return_15m": 0.002, "return_60m": 0.003},
        bybit_spot={"return_5m": None, "return_15m": None, "return_60m": None},
        bybit_linear={"return_5m": None, "return_15m": None, "return_60m": None},
    )

    assert group.values["regime_bull"] is None
    assert group.values["regime_bear"] is None
    assert group.values["regime_sideways_mixed"] is None
    assert group.missing_flags["regime_bull_missing"] is True
