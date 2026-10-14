"""Fixed in-memory Stage B1 postcommit receipt delivery failure rehearsal.

This is NOT a production receipt, DB commit proof, writer, operator or
approved diagnostic. No files, environment, connections, SQL, subprocesses,
network, threads or side effects. Cases are synthetic and deliberately
include metadata that the present offline callback DOES NOT provide.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass

SCENARIOS = (
    "normal",
    "lost_callback",
    "duplicate_callback",
    "conflicting_retry",
    "before_commit",
    "late_after_readout",
    "cancelled_await_late_commit",
)
_CLASSES = ("trade", "orderbook", "liquidation", "other", "excluded_non_bulk")
_FAKE_BATCH_ID = "FIXED_FAKE_BATCH_01"


@dataclass(frozen=True)
class _Delivery:
    fake_batch_id: str
    inserted_trade: int
    observed_clock: int


@dataclass(frozen=True)
class _Case:
    fake_commit_clock: int
    fake_observation_readout_clock: int
    fake_await_cancelled_clock: int | None
    fake_deliveries: tuple[_Delivery, ...]


def _case(scenario: str) -> _Case:
    if scenario not in SCENARIOS:
        raise ValueError("only fixed nonproduction receipt scenarios allowed")
    delivered = _Delivery(_FAKE_BATCH_ID, 1, 2)
    cases = {
        "normal": _Case(1, 10, None, (delivered,)),
        "lost_callback": _Case(1, 10, None, ()),
        "duplicate_callback": _Case(1, 10, None, (delivered, delivered)),
        "conflicting_retry": _Case(
            1, 10, None, (delivered, _Delivery(_FAKE_BATCH_ID, 2, 3))
        ),
        "before_commit": _Case(3, 10, None, (delivered,)),
        "late_after_readout": _Case(
            1, 10, None, (_Delivery(_FAKE_BATCH_ID, 1, 12),)
        ),
        "cancelled_await_late_commit": _Case(
            3, 10, 1, (_Delivery(_FAKE_BATCH_ID, 1, 4),)
        ),
    }
    return cases[scenario]


def review_fake_receipts(scenario: str) -> dict[str, object]:
    """Classify only fixed toy notifications; never infer real transaction state."""
    case = _case(scenario)
    known: dict[str, int] = {}
    duplicate_deliveries = 0
    conflicting_deliveries = 0
    before_commit = 0
    late_readout = 0
    for delivery in case.fake_deliveries:
        if delivery.observed_clock <= case.fake_commit_clock:
            before_commit += 1
        if delivery.observed_clock > case.fake_observation_readout_clock:
            late_readout += 1
        existing = known.get(delivery.fake_batch_id)
        if existing is not None:
            if existing == delivery.inserted_trade:
                duplicate_deliveries += 1
            else:
                conflicting_deliveries += 1
        else:
            known[delivery.fake_batch_id] = delivery.inserted_trade

    observed_total = sum(x.inserted_trade for x in case.fake_deliveries)
    deduped_total = sum(known.values())
    fake_callback_incomplete = not case.fake_deliveries or late_readout > 0
    unsafe = bool(conflicting_deliveries or before_commit)
    return {
        "report": "phase14_b1_fake_postcommit_receipt_delivery_v1",
        "scenario": scenario,
        "status": (
            "FAKE_RECEIPT_INVALID_OR_CONFLICTING_NO_GO"
            if unsafe else (
                "FAKE_RECEIPT_DELIVERY_INCOMPLETE_NO_GO"
                if fake_callback_incomplete else "FAKE_RECEIPT_SHAPE_ONLY_NO_GO"
            )
        ),
        "fixed_fake_attempted_by_class": dict.fromkeys(_CLASSES, 0) | {"trade": 2},
        "fixed_fake_committed_inserted_by_class": dict.fromkeys(_CLASSES, 0) | {"trade": 1},
        "fake_delivery_count": len(case.fake_deliveries),
        "fake_duplicate_delivery_count": duplicate_deliveries,
        "fake_conflicting_delivery_count": conflicting_deliveries,
        "fake_before_commit_delivery_count": before_commit,
        "fake_late_after_readout_delivery_count": late_readout,
        "naive_double_count_prone_delivery_trade_total": observed_total,
        "idempotently_grouped_fake_trade_total": deduped_total,
        "fake_coroutine_cancelled_before_commit": (
            case.fake_await_cancelled_clock is not None
            and case.fake_await_cancelled_clock < case.fake_commit_clock
        ),
        "current_local_callback_has_batch_identifier": False,
        "current_local_callback_has_delivery_acknowledgement": False,
        "current_local_callback_provides_independent_db_attestation": False,
        "real_commit_or_insertion_proven": False,
        "real_exactly_once_receipt_delivery_proven": False,
        "actual_on_host_process_or_db_thread_quiescence_proven": False,
        "production_execution_authorized": False,
        "limitations": [
            "All attempted, inserted, clock and batch identities are synthetic.",
            "Current offline callback carries class counts only; fake IDs are illustrative.",
            "Callback failure/loss can occur AFTER commit, without undoing it.",
            "Duplicate callbacks require a trustworthy transaction identity to deduplicate.",
            "Cancellation of an awaiting coroutine does not stop its DB thread.",
            "No real database transaction, host, exchange or service was accessed.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True, choices=SCENARIOS)
    args = parser.parse_args()
    print(json.dumps(review_fake_receipts(args.scenario), sort_keys=True))


if __name__ == "__main__":
    main()
