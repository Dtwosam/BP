from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable

from bp_engine.config import Settings, get_settings
from bp_engine.recorder.clock import ClockSyncStatus, check_ntp_sync, ensure_clock_ready
from bp_engine.recorder.service import RecorderService, build_default_recorder_service

ServiceFactory = Callable[[Settings], RecorderService]
ClockChecker = Callable[[], ClockSyncStatus]


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            continue


async def run_recorder(
    *,
    service_factory: ServiceFactory = build_default_recorder_service,
    stop: asyncio.Event | None = None,
    clock_checker: ClockChecker = check_ntp_sync,
) -> None:
    settings = get_settings()
    ensure_clock_ready(settings, clock_checker())
    service = service_factory(settings)
    stop_event = stop or asyncio.Event()
    if stop is None:
        _install_signal_handlers(stop_event)
    await service.run(stop_event)


def main() -> None:
    asyncio.run(run_recorder())


if __name__ == "__main__":
    main()
