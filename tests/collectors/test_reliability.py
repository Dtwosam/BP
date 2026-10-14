import math
from datetime import UTC, datetime, timedelta

import pytest

from bp_engine.collectors.reliability import ClockSkewGuard, FeedWatchdog, ReconnectPolicy


def test_reconnect_policy_exponential_backoff_caps_at_maximum() -> None:
    policy = ReconnectPolicy(initial_seconds=1, multiplier=2, maximum_seconds=5)

    assert [policy.delay_for_attempt(i) for i in range(6)] == [1, 2, 4, 5, 5, 5]



def test_reconnect_policy_does_not_overflow_on_long_failure_streaks() -> None:
    policy = ReconnectPolicy()
    assert [policy.delay_for_attempt(i) for i in range(7)] == [
        1, 2, 4, 8, 16, 30, 30,
    ]
    for attempt in (1023, 1024, 10_000, 10**400):
        assert policy.delay_for_attempt(attempt) == 30


def test_reconnect_policy_zero_delay_stays_zero_after_many_failures() -> None:
    immediate = ReconnectPolicy(initial_seconds=0, maximum_seconds=0)
    assert immediate.delay_for_attempt(10_000) == 0
    assert ReconnectPolicy(
        initial_seconds=0, multiplier=2, maximum_seconds=30
    ).delay_for_attempt(10_000) == 0


def test_reconnect_policy_tiny_initial_delay_with_large_finite_power() -> None:
    policy = ReconnectPolicy(
        initial_seconds=1e-300, multiplier=2, maximum_seconds=1e300
    )
    delay = policy.delay_for_attempt(1500)
    assert 0 < delay < 1e300
    assert math.isclose(
        math.log(delay), math.log(1e-300) + 1500 * math.log(2),
        abs_tol=1e-10,
    )
    assert policy.delay_for_attempt(10_000) == 1e300


def test_reconnect_policy_still_rejects_negative_attempts() -> None:

    with pytest.raises(ValueError, match="attempt must be non-negative"):
        ReconnectPolicy().delay_for_attempt(-1)



def test_watchdog_emits_stale_once_then_recovered_on_next_event() -> None:
    watchdog = FeedWatchdog(stale_after_seconds=10)
    observed_at = datetime(2026, 8, 20, 21, 30, tzinfo=UTC)

    assert watchdog.observe("bybit", "spot", monotonic_time=100, observed_at=observed_at) is None
    stale = watchdog.check("bybit", "spot", monotonic_time=111, observed_at=observed_at)
    duplicate = watchdog.check("bybit", "spot", monotonic_time=115, observed_at=observed_at)
    recovered = watchdog.observe(
        "bybit",
        "spot",
        monotonic_time=116,
        observed_at=observed_at + timedelta(seconds=16),
    )

    assert stale is not None and stale.incident_type == "stale"
    assert duplicate is None
    assert recovered is not None and recovered.incident_type == "recovered"


def test_watchdog_arm_does_not_make_last_real_observation_newer() -> None:
    watchdog = FeedWatchdog(stale_after_seconds=10)
    observed_at = datetime(2026, 8, 20, 21, 30, tzinfo=UTC)

    assert watchdog.observe("bybit", "spot", monotonic_time=100, observed_at=observed_at) is None
    watchdog.arm("bybit", "spot", monotonic_time=109)
    stale = watchdog.check(
        "bybit",
        "spot",
        monotonic_time=111,
        observed_at=observed_at + timedelta(seconds=11),
    )

    assert stale is not None
    assert stale.incident_type == "stale"
    assert stale.details["age_seconds"] == 11


def test_clock_skew_guard_allows_delayed_events_but_rejects_future_source_time() -> None:
    guard = ClockSkewGuard(max_abs_skew_seconds=2)
    received_at = datetime(2026, 8, 20, 21, 30, 10, tzinfo=UTC)

    assert (
        guard.check(
            source="bybit",
            stream="linear",
            source_timestamp=received_at - timedelta(seconds=30),
            received_at=received_at,
        )
        is None
    )

    assert (
        guard.check(
            source="bybit",
            stream="linear",
            source_timestamp=received_at + timedelta(seconds=1),
            received_at=received_at,
        )
        is None
    )

    incident = guard.check(
        source="bybit",
        stream="linear",
        source_timestamp=received_at + timedelta(seconds=3),
        received_at=received_at,
    )

    assert incident is not None
    assert incident.incident_type == "clock_skew"
    assert incident.details["skew_seconds"] == -3.0
