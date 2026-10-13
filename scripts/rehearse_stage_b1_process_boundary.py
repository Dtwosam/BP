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
import sys
import tempfile
import time
from pathlib import Path

from bp_engine.recorder.stage_b1_observer import StageB1BulkObserver
from bp_engine.recorder.stage_b1_one_shot import (
    MAX_REPORT_BYTES,
    serialize_final_report,
)

SCENARIOS = ("cooperative", "hang", "ignore_term", "crash", "poisoned", "oversized")
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


def _child(scenario: str, output_fd: int) -> None:
    # Only called by the self-exec fixed-scenario fork, not the recorder.
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


async def _wait_exit(process: asyncio.subprocess.Process, timeout: float) -> bool:
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
    except TimeoutError:
        return False
    return True


async def supervise_synthetic(
    scenario: str,
    *,
    wall_seconds: float = 0.8,
    term_grace_seconds: float = 0.15,
    kill_grace_seconds: float = 0.8,
) -> dict[str, object]:
    """Reap an isolated FAKE child, without executing any operator command.

    Only the direct process leader's exit/reaping is asserted. Killing a
    process group is best-effort; the result cannot prove every descendant,
    surviving DB thread, or separately managed systemd service stopped.
    """
    if os.name != "posix":
        raise RuntimeError("synthetic process boundary rehearsal requires POSIX")
    if scenario not in SCENARIOS:
        raise ValueError("only named synthetic child scenarios are permitted")
    wall = _bounded_seconds(wall_seconds, maximum=3)
    term = _bounded_seconds(term_grace_seconds, maximum=1)
    kill = _bounded_seconds(kill_grace_seconds, maximum=2)
    with tempfile.TemporaryFile(mode="w+b") as output:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-B",
            str(_SCRIPT),
            "--synthetic-child",
            scenario,
            str(output.fileno()),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            pass_fds=(output.fileno(),),
            start_new_session=True,
        )
        outcome = "unresolved"
        try:
            if await _wait_exit(process, wall):
                outcome = "exited"
            else:
                _signal_group(process.pid, signal.SIGTERM)
                if await _wait_exit(process, term):
                    outcome = "sigterm"
                else:
                    _signal_group(process.pid, signal.SIGKILL)
                    if await _wait_exit(process, kill):
                        outcome = "sigkill"
        finally:
            if process.returncode is None:
                _signal_group(process.pid, signal.SIGKILL)
                # Cancellation/interruption MUST NOT be treated as safe
                # completion if the direct child is not verifiably reaped.
                try:
                    await asyncio.shield(
                        asyncio.wait_for(process.wait(), timeout=kill)
                    )
                except TimeoutError:
                    outcome = "unresolved"

        reaped = process.returncode is not None
        accepted = (
            reaped
            and outcome == "exited"
            and process.returncode == 0
            and _report_matches_synthetic_fixture(output)
        )
        return {
            "report": "phase14_b1_synthetic_process_watchdog_v1",
            "scope": "offline_fixed_fake_child_only",
            "scenario": scenario,
            "termination": outcome,
            "process_leader_reaped": reaped,
            "fixed_synthetic_aggregate_valid": bool(accepted),
            "all_descendants_terminated_proven": False,
            "production_execution_authorized": False,
            "production_host_access": False,
            "operator_launcher_available": False,
            "limitations": [
                "No production recorder or database subprocess is launched.",
                "A reaped process leader does not prove all descendants stopped.",
                "No actual process-level observer collection or safe service restoration.",
            ],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scenario", choices=SCENARIOS)
    group.add_argument("--synthetic-child", choices=SCENARIOS)
    parser.add_argument("--output-fd", type=int, default=None, help=argparse.SUPPRESS)
    args, extras = parser.parse_known_args()

    if args.synthetic_child is not None:
        # Private child entry: fixed arguments supplied by this same module.
        if len(extras) != 1 or args.output_fd is not None:
            parser.error("invalid fixed synthetic child invocation")
        try:
            fd = int(extras[0])
        except ValueError:
            parser.error("invalid synthetic output fd")
        if fd < 3:
            parser.error("invalid synthetic output fd")
        _child(args.synthetic_child, fd)
        return
    if extras or args.output_fd is not None:
        parser.error("unexpected arguments")
    print(json.dumps(asyncio.run(supervise_synthetic(args.scenario)), sort_keys=True))


if __name__ == "__main__":
    main()
