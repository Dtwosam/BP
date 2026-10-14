"""Stage B1 fixed synthetic observability-equivalence review, ALWAYS NO GO.

No network, recorder run, DB, SQL, subprocess, environment access, files,
external inputs, operator launcher or production mutation. These are not
empirical observations or probability estimates. Synthetic worlds demonstrate
why a *separate* Bybit wire probe cannot localize the recorder's root cause.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass

VIEWS = (
    "separate_wire_only",
    "same_recorder_dormant_b1",
    "same_recorder_plus_verified_postcommit",
)


@dataclass(frozen=True)
class _World:
    name: str
    # Fixed probe is on a SEPARATE socket and has no recorder correlation.
    probe_wire: int
    probe_parsed: int
    probe_socket_ack: int
    # Hypothetical SAME-recorder counters; all are fixture-only.
    recorder_wire: int
    recorder_parsed: int
    recorder_enqueued: int
    recorder_attempts_in_returned_transactions: int
    # Not available in the dormant B1 observer. Hypothetical external receipt.
    verified_inserted_rows: int


# Every world deliberately has the same successful independent probe.
# Categories are illustrative bottlenecks, NOT a claim of unique causality.
_WORLDS = (
    _World("recorder_no_bulk_frames", 3, 3, 1, 0, 0, 0, 0, 0),
    _World("recorder_parser_no_events", 3, 3, 1, 3, 0, 0, 0, 0),
    _World("recorder_enqueue_no_returns", 3, 3, 1, 3, 3, 0, 0, 0),
    _World("recorder_writer_no_returns", 3, 3, 1, 3, 3, 3, 0, 0),
    _World("recorder_transaction_all_deduped", 3, 3, 1, 3, 3, 3, 3, 0),
    _World("recorder_new_rows_inserted", 3, 3, 1, 3, 3, 3, 3, 3),
)
WORLD_NAMES = tuple(w.name for w in _WORLDS)


def _signature(world: _World, view: str) -> tuple[int, ...]:
    probe = (world.probe_socket_ack, world.probe_wire, world.probe_parsed)
    same_recorder = (
        world.recorder_wire,
        world.recorder_parsed,
        world.recorder_enqueued,
        world.recorder_attempts_in_returned_transactions,
    )
    if view == "separate_wire_only":
        return probe
    if view == "same_recorder_dormant_b1":
        return same_recorder
    if view == "same_recorder_plus_verified_postcommit":
        return (*same_recorder, world.verified_inserted_rows)
    raise ValueError("only fixed observability views may be compared")


def review_equivalence(view: str) -> dict[str, object]:
    """Return only synthetic hypothesis group labels and fixed limitations."""
    if view not in VIEWS:
        raise ValueError("only fixed observability views may be compared")
    signatures: dict[tuple[int, ...], list[str]] = {}
    for world in _WORLDS:
        signatures.setdefault(_signature(world, view), []).append(world.name)
    equivalence_groups = list(signatures.values())
    ambiguous_groups = [group for group in equivalence_groups if len(group) > 1]
    ambiguous_pair_count = sum(
        len(group) * (len(group) - 1) // 2 for group in ambiguous_groups
    )
    return {
        "report": "phase14_b1_offline_observability_equivalence_v1",
        "status": "FIXED_SYNTHETIC_DECISION_REVIEW_NO_GO",
        "view": view,
        "fixture_world_count": len(_WORLDS),
        "equivalence_class_count": len(equivalence_groups),
        "ambiguous_hypothesis_pair_count": ambiguous_pair_count,
        "indistinguishable_fixture_world_groups": ambiguous_groups,
        "separate_probe_is_not_same_recorder_evidence": True,
        "same_recorder_counts_are_hypothetical": True,
        "same_recorder_batch_counter_is_attempts_not_insertions": True,
        "hypothetical_insert_receipt_is_unimplemented_in_production": True,
        "hypothetical_receipt_assumes_verified_commit_and_same_batch": True,
        "any_single_stage_pattern_proves_unique_cause": False,
        "production_root_cause_identified": False,
        "actual_host_source_identity_verified": False,
        "independent_stop_restoration_verified": False,
        "production_execution_authorized": False,
        "conclusion": (
            "Separate wire-only probe cannot discriminate any of the six "
            "fixed production-recorder hypotheses."
            if view == "separate_wire_only"
            else (
                "Dormant same-recorder counters distinguish some fixed stage "
                "patterns but cannot separate deduped from inserted rows."
                if view == "same_recorder_dormant_b1"
                else (
                    "Hypothetical independently verified same-batch insertion "
                    "receipts distinguish these six simplified fake worlds; "
                    "no real implementation or production proof exists."
                )
            )
        ),
        "limitations": [
            "Six worlds are synthetic illustrations, not exhaustive real causes.",
            "Stage counts across windows and batches are not necessarily one-to-one.",
            "Socket-level ACK does not prove each Bybit subscription topic.",
            "An isolated socket sample cannot attest to production socket receipt.",
            "No live recorder, host, database or exchange was accessed.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--view", required=True, choices=VIEWS)
    args = parser.parse_args()
    print(json.dumps(review_equivalence(args.view), sort_keys=True))


if __name__ == "__main__":
    main()
