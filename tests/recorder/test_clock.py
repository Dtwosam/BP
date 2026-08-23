import subprocess

import pytest

from bp_engine.config import Settings
from bp_engine.recorder.clock import ClockSyncStatus, check_ntp_sync, ensure_clock_ready


def completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["timedatectl"], returncode, stdout=stdout, stderr="")


def test_check_ntp_sync_reads_timedatectl_yes() -> None:
    calls: list[list[str]] = []

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return completed("yes\n")

    status = check_ntp_sync(runner=runner)

    assert status == ClockSyncStatus(
        supported=True,
        synchronized=True,
        source="timedatectl",
        detail="yes",
    )
    assert calls == [["timedatectl", "show", "-p", "NTPSynchronized", "--value"]]


def test_check_ntp_sync_reports_timedatectl_no() -> None:
    status = check_ntp_sync(runner=lambda command: completed("no\n"))

    assert status.supported is True
    assert status.synchronized is False


def test_check_ntp_sync_reports_unsupported_when_no_clock_tool_exists() -> None:
    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(command[0])

    status = check_ntp_sync(runner=runner)

    assert status.supported is False
    assert status.synchronized is None
    assert status.source is None


def test_ensure_clock_ready_refuses_unsynchronized_required_host() -> None:
    settings = Settings(recorder_require_ntp_sync=True)
    status = ClockSyncStatus(
        supported=True,
        synchronized=False,
        source="timedatectl",
        detail="no",
    )

    with pytest.raises(RuntimeError, match="clock is not synchronized"):
        ensure_clock_ready(settings, status)


def test_ensure_clock_ready_can_be_disabled_for_local_research() -> None:
    settings = Settings(recorder_require_ntp_sync=False)
    status = ClockSyncStatus(
        supported=False,
        synchronized=None,
        source=None,
        detail="unavailable",
    )

    ensure_clock_ready(settings, status)
