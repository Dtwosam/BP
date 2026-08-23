import asyncio

import pytest

from bp_engine.recorder.__main__ import run_recorder
from bp_engine.recorder.clock import ClockSyncStatus


@pytest.mark.asyncio
async def test_run_recorder_uses_safe_settings_and_supplied_stop_event() -> None:
    observed: dict[str, object] = {}

    class FakeService:
        async def run(self, stop: asyncio.Event) -> None:
            observed["stop"] = stop
            observed["already_stopped"] = stop.is_set()

    def factory(settings: object) -> FakeService:
        observed["live_trading_enabled"] = settings.live_trading_enabled
        observed["mode"] = settings.mode.value
        return FakeService()

    stop = asyncio.Event()
    stop.set()
    await run_recorder(
        service_factory=factory,
        stop=stop,
        clock_checker=lambda: ClockSyncStatus(True, True, "test", "yes"),
    )

    assert observed == {
        "stop": stop,
        "already_stopped": True,
        "live_trading_enabled": False,
        "mode": "research",
    }


@pytest.mark.asyncio
async def test_run_recorder_refuses_unsynchronized_required_clock() -> None:
    factory_called = False

    def factory(settings: object) -> object:
        nonlocal factory_called
        factory_called = True
        raise AssertionError("service factory must not run")

    with pytest.raises(RuntimeError, match="clock is not synchronized"):
        await run_recorder(
            service_factory=factory,
            stop=asyncio.Event(),
            clock_checker=lambda: ClockSyncStatus(True, False, "test", "no"),
        )

    assert factory_called is False
