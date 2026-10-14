"""Offline-only POSIX Stage B1 *synthetic* process watchdog rehearsal.

The ONLY executable child is this file, in one of five fixed fake modes.
NO recorder/settings/service module is imported by the child or activated.
NO arbitrary command, network, database, VM, systemd, or production switch.
The parent emits only fixed-status metadata; raw child output is discarded.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from bp_engine.recorder.stage_b1_observer import StageB1BulkObserver
from bp_engine.recorder.stage_b1_one_shot import (
    MAX_REPORT_BYTES,
    serialize_final_report,
)

SCENARIOS = (
    "cooperative", "hang", "ignore_term", "crash", "poisoned", "oversized",
    "leader_exits_descendant", "leader_exits_descendant_ignores_term",
)
_SCRIPT = Path(__file__).resolve()


def _bounded_seconds(value: float, *, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("synthetic timeout must be a finite positive number")
    if not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError("synthetic timeout out of range")
    return float(value)


def _synthetic_report() -> bytes:
    clock = [0.0]
    observer = StageB1BulkObserver(duration_seconds=1, monotonic=lambda: clock[0])
    clock[0] = 1.0
    return serialize_final_report(
        observer, stop_reason="window_elapsed", service_state="clean"
    ).encode("utf-8")


def _report_matches_synthetic_fixture(file: object) -> bool:
    file.seek(0)
    raw = file.read(MAX_REPORT_BYTES + 1)
    if not raw or len(raw) > MAX_REPORT_BYTES:
        return False
    try:
        decoded = json.loads(raw)
        return decoded == json.loads(_synthetic_report())
    except (UnicodeError, ValueError, TypeError):
        return False


def _child(scenario: str, output_fd: int, group_liveness_fd: int) -> None:
    # Only called by the self-exec fixed-scenario fork, not the recorder.
    if scenario in {
        "leader_exits_descendant",
        "leader_exits_descendant_ignores_term",
    }:
        # One fixed fake fork. The grandchild retains group_liveness_fd.
        # Handshake ensures SIGTERM-ignore is installed before leader exits.
        read_ready, write_ready = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read_ready)
            if scenario == "leader_exits_descendant_ignores_term":
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
            os.write(write_ready, b"1")
            os.close(write_ready)
            while True:
                time.sleep(0.05)
        os.close(write_ready)
        try:
            ready = os.read(read_ready, 1)
        finally:
            os.close(read_ready)
        if ready != b"1":
            raise RuntimeError("synthetic descendant did not start")
        os.write(output_fd, _synthetic_report())
        # Direct leader exits with an actual synthetic descendant alive.
        return

    if scenario == "crash":
        raise RuntimeError("SECRET_ERROR_DETAILS_MUST_NOT_ESCAPE")
    if scenario == "hang":
        while True:
            time.sleep(0.05)
    if scenario == "ignore_term":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        while True:
            time.sleep(0.05)

    raw = _synthetic_report()
    if scenario == "poisoned":
        raw = json.dumps({"secret_event": "SECRET_VALUE"}).encode()
    elif scenario == "oversized":
        raw = b"x" * (MAX_REPORT_BYTES + 256)
    elif scenario != "cooperative":
        raise ValueError("unknown synthetic scenario")
    os.write(output_fd, raw)


def _signal_group(pid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass


async def _wait_exit(process: subprocess.Popen[bytes], timeout: float) -> bool:
    """Bounded, non-threaded polling of a fixed synthetic process leader."""
    end = time.monotonic() + timeout
    while process.poll() is None:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(0.015, remaining))
    return True


async def _wait_pipe_eof(read_fd: int, timeout: float) -> bool:
    """Bounded EOF check for known synthetic inherited-FD descendants."""
    end = time.monotonic() + timeout
    while True:
        try:
            data = os.read(read_fd, 1)
        except BlockingIOError:
            pass
        else:
            return data == b""
        remaining = end - time.monotonic()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(0.015, remaining))


async def supervise_synthetic(
    scenario: str,
    *,
    wall_seconds: float = 0.8,
    term_grace_seconds: float = 0.15,
    kill_grace_seconds: float = 0.8,
) -> dict[str, object]:
    """Reap an isolated FAKE child, without executing any operator command.

    The inherited-pipe EOF is an extra assertion for fixed fake forks,
    NOT proof unrelated descendants, surviving DB threads or separately
    managed systemd services have stopped.
    """
    if os.name != "posix":
        raise RuntimeError("synthetic process boundary rehearsal requires POSIX")
    if scenario not in SCENARIOS:
        raise ValueError("only named synthetic child scenarios are permitted")
    wall = _bounded_seconds(wall_seconds, maximum=3)
    term = _bounded_seconds(term_grace_seconds, maximum=1)
    kill = _bounded_seconds(kill_grace_seconds, maximum=2)
    with tempfile.TemporaryFile(mode="w+b") as output:
        read_fd, write_fd = os.pipe()
        os.set_blocking(read_fd, False)
        try:
            try:
                # Acquire the handle synchronously before any cancellation point.
                # Async subprocess creation may spawn a child yet be cancelled
                # before returning the handle needed for group cleanup.
                # This remains a fixed fake-only invocation, never a recorder.
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-I",
                        "-B",
                        str(_SCRIPT),
                        "--synthetic-child",
                        scenario,
                        str(output.fileno()),
                        str(write_fd),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    pass_fds=(output.fileno(), write_fd),
                    start_new_session=True,
                )
            finally:
                # Parent write end must close; only live fake group
                # members can keep the liveness pipe open.
                os.close(write_fd)

            outcome = "unresolved"
            pipe_closed = False
            try:
                leader_exited = await _wait_exit(process, wall)
                pipe_closed = await _wait_pipe_eof(read_fd, 0.03)
                if leader_exited and pipe_closed:
                    outcome = "exited"
                else:
                    # A successful leader can have a live grandchild:
                    # still send a TERM signal to the isolated group.
                    _signal_group(process.pid, signal.SIGTERM)
                    leader_reaped_on_term = (
                        leader_exited or await _wait_exit(process, term)
                    )
                    pipe_closed = await _wait_pipe_eof(read_fd, term)
                    if leader_reaped_on_term and pipe_closed:
                        outcome = (
                            "descendant_sigterm" if leader_exited else "sigterm"
                        )
                    else:
                        _signal_group(process.pid, signal.SIGKILL)
                        leader_reaped_on_kill = (
                            leader_reaped_on_term
                            or await _wait_exit(process, kill)
                        )
                        pipe_closed = await _wait_pipe_eof(read_fd, kill)
                        if leader_reaped_on_kill and pipe_closed:
                            outcome = (
                                "descendant_sigkill" if leader_exited
                                else "sigkill"
                            )
            finally:
                if process.poll() is None or not pipe_closed:
                    # Includes cancellation after the synchronous spawn:
                    # try to kill/reap the known isolated fake group before
                    # propagating cancellation to the caller.
                    _signal_group(process.pid, signal.SIGKILL)
                    leader_stopped = await _wait_exit(process, kill)
                    pipe_closed = await _wait_pipe_eof(read_fd, kill)
                    if not leader_stopped or not pipe_closed:
                        outcome = "unresolved"

            reaped = process.returncode is not None
            accepted = (
                reaped
                and pipe_closed
                and outcome == "exited"
                and process.returncode == 0
                and _report_matches_synthetic_fixture(output)
            )
            return {
                "report": "phase14_b1_synthetic_process_watchdog_v2",
                "scope": "offline_fixed_fake_child_only",
                "scenario": scenario,
                "termination": outcome,
                "process_leader_reaped": reaped,
                "known_fixture_group_pipe_eof": pipe_closed,
                "fixed_synthetic_aggregate_valid": bool(accepted),
                "all_descendants_terminated_proven": False,
                "production_execution_authorized": False,
                "production_host_access": False,
                "operator_launcher_available": False,
                "limitations": [
                    "No production recorder or database subprocess is launched.",
                    "Inherited pipe EOF covers only known fake descendants.",
                    "One cancellation can trigger cleanup after synchronous spawn; "
                    "repeated cancellation/loop shutdown is not proven.",
                    "An unrelated or escaped process could close its pipe early.",
                    "No DB thread, actual process tree, or systemd restore is proven.",
                ],
            }
        finally:
            os.close(read_fd)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scenario", choices=SCENARIOS)
    group.add_argument("--synthetic-child", choices=SCENARIOS)
    parser.add_argument("--output-fd", type=int, default=None, help=argparse.SUPPRESS)
    args, extras = parser.parse_known_args()

    if args.synthetic_child is not None:
        # Private child entry: fixed arguments supplied by this same module.
        if len(extras) != 2 or args.output_fd is not None:
            parser.error("invalid fixed synthetic child invocation")
        try:
            fd = int(extras[0])
            group_fd = int(extras[1])
        except ValueError:
            parser.error("invalid synthetic output fd")
        if fd < 3 or group_fd < 3 or fd == group_fd:
            parser.error("invalid synthetic output fd")
        _child(args.synthetic_child, fd, group_fd)
        return
    if extras or args.output_fd is not None:
        parser.error("unexpected arguments")
    print(json.dumps(asyncio.run(supervise_synthetic(args.scenario)), sort_keys=True))


if __name__ == "__main__":
    main()
