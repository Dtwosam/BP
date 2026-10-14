"""Offline-only Stage B1 *fake* service-restoration/read-back boundary.

A fixed in-memory service state is mutated by a fake controller and observed
by a separate read-only fixture probe. No systemd, subprocess, threads, DB,
network, VM, host identity probe, credentials, or production operator entry.
All reports remain NON-AUTHORIZING even when the fake fixture looks healthy.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass

SCENARIOS = (
    "restored",
    "ack_only",
    "wrong_unit",
    "wrong_code",
    "extra_restart",
    "unhealthy",
    "db_worker_pending",
    "descendant_pending",
    "stale_readback",
    "no_ack",
    "restore_error",
    "observer_unavailable",
)
_BASE_UNIT = "a" * 64
_BASE_CODE = "b" * 64
_CHANGED = "c" * 64
_CHECK_NAMES = (
    "restore_ack_received",
    "fresh_separate_readback",
    "service_active",
    "service_healthy",
    "service_unit_matches_baseline",
    "recorder_source_matches_baseline",
    "expected_single_restart",
    "no_known_fake_descendants",
    "no_known_fake_db_workers",
)


@dataclass(frozen=True)
class _Snapshot:
    revision: int
    active: bool
    healthy: bool
    unit_sha256: str
    code_sha256: str
    restart_count: int
    descendant_count: int
    db_worker_pending: bool


@dataclass
class _FakeState:
    revision: int = 0
    active: bool = True
    healthy: bool = True
    unit_sha256: str = _BASE_UNIT
    code_sha256: str = _BASE_CODE
    restart_count: int = 0
    descendant_count: int = 0
    db_worker_pending: bool = False


class _IndependentFakeReadback:
    """Reads fixture state directly, never the controller's success claim."""

    def __init__(self, state: _FakeState) -> None:
        self._state = state

    def inspect(self) -> _Snapshot:
        state = self._state
        return _Snapshot(
            revision=state.revision,
            active=state.active,
            healthy=state.healthy,
            unit_sha256=state.unit_sha256,
            code_sha256=state.code_sha256,
            restart_count=state.restart_count,
            descendant_count=state.descendant_count,
            db_worker_pending=state.db_worker_pending,
        )


class _FakeController:
    def __init__(self, state: _FakeState) -> None:
        self._state = state

    def perturb(self) -> None:
        state = self._state
        state.revision += 1
        state.active = False
        state.healthy = False
        state.unit_sha256 = _CHANGED
        state.code_sha256 = _CHANGED

    def restore(self, scenario: str) -> bool:
        state = self._state
        if scenario in {"ack_only", "stale_readback"}:
            # Explicitly lie about success; no state restoration occurs.
            return True
        if scenario == "restore_error":
            # The fixture models a caught controller failure, no exception
            # string or command is allowed to escape.
            return False

        state.revision += 1
        state.active = True
        state.healthy = scenario != "unhealthy"
        state.unit_sha256 = _CHANGED if scenario == "wrong_unit" else _BASE_UNIT
        state.code_sha256 = _CHANGED if scenario == "wrong_code" else _BASE_CODE
        state.restart_count += 2 if scenario == "extra_restart" else 1
        state.descendant_count = 1 if scenario == "descendant_pending" else 0
        state.db_worker_pending = scenario == "db_worker_pending"
        return scenario != "no_ack"


def review_fake_restoration(scenario: str) -> dict[str, object]:
    """Reconcile one fixed fake restore report against separate fixture state."""
    if scenario not in SCENARIOS:
        raise ValueError("only fixed synthetic restoration scenarios are accepted")

    state = _FakeState()
    probe = _IndependentFakeReadback(state)
    controller = _FakeController(state)
    before = probe.inspect()
    controller.perturb()
    ack = controller.restore(scenario)

    # A stale cached read can look perfectly healthy, so it must not count as
    # evidence that the restore happened after the perturbation.
    after = (
        None if scenario == "observer_unavailable"
        else before if scenario == "stale_readback"
        else probe.inspect()
    )
    checks = {
        "restore_ack_received": ack is True,
        "fresh_separate_readback": (
            after is not None
            and after.revision > before.revision
            and after.revision >= state.revision
        ),
        "service_active": after is not None and after.active is True,
        "service_healthy": after is not None and after.healthy is True,
        "service_unit_matches_baseline": (
            after is not None and after.unit_sha256 == before.unit_sha256
        ),
        "recorder_source_matches_baseline": (
            after is not None and after.code_sha256 == before.code_sha256
        ),
        "expected_single_restart": (
            after is not None and after.restart_count == before.restart_count + 1
        ),
        "no_known_fake_descendants": (
            after is not None and after.descendant_count == 0
        ),
        "no_known_fake_db_workers": (
            after is not None and after.db_worker_pending is False
        ),
    }
    assert tuple(checks) == _CHECK_NAMES  # Constant fixture schema.
    matched = all(checks.values())

    return {
        "report": "phase14_b1_fake_independent_restore_readback_v1",
        "scope": "offline_in_memory_fake_service_and_separate_fixture_probe",
        "scenario": scenario,
        "status": (
            "FAKE_READBACK_MATCH_UNVERIFIED_NO_GO"
            if matched else "FAKE_RESTORE_MISMATCH_NO_GO"
        ),
        "checks": checks,
        "failed_checks": [key for key in _CHECK_NAMES if not checks[key]],
        "fake_readback_after_restore_attempt": after is not None,
        "actual_process_tree_termination_proven": False,
        "actual_db_thread_quiescence_proven": False,
        "actual_systemd_restoration_proven": False,
        "actual_host_identity_attested": False,
        "operator_launcher_available": False,
        "production_execution_authorized": False,
        "live_trading_enabled": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    args = parser.parse_args()
    print(json.dumps(review_fake_restoration(args.scenario), sort_keys=True))


if __name__ == "__main__":
    main()
