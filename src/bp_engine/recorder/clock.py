from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from bp_engine.config import Settings


@dataclass(frozen=True)
class ClockSyncStatus:
    supported: bool
    synchronized: bool | None
    source: str | None
    detail: str


CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _default_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def check_ntp_sync(*, runner: CommandRunner = _default_runner) -> ClockSyncStatus:
    timedatectl = ["timedatectl", "show", "-p", "NTPSynchronized", "--value"]
    try:
        result = runner(timedatectl)
    except FileNotFoundError:
        result = None

    if result is not None and result.returncode == 0:
        detail = result.stdout.strip().lower()
        if detail in {"yes", "no"}:
            return ClockSyncStatus(
                supported=True,
                synchronized=detail == "yes",
                source="timedatectl",
                detail=detail,
            )

    try:
        chrony = runner(["chronyc", "tracking"])
    except FileNotFoundError:
        chrony = None

    if chrony is not None and chrony.returncode == 0:
        detail = chrony.stdout.strip()
        normalized = detail.lower()
        synchronized = "leap status" in normalized and "normal" in normalized
        return ClockSyncStatus(True, synchronized, "chronyc", detail)

    return ClockSyncStatus(
        supported=False,
        synchronized=None,
        source=None,
        detail="clock synchronization status unavailable",
    )


def ensure_clock_ready(settings: Settings, status: ClockSyncStatus) -> None:
    if not settings.recorder_require_ntp_sync:
        return
    if not status.supported:
        raise RuntimeError("clock synchronization status is unavailable")
    if status.synchronized is not True:
        raise RuntimeError("clock is not synchronized")
