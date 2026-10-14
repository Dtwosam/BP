"""Review-only, always-NO-GO Stage B1 lifecycle/restoration claim checker.

No host access, network, SQL, subprocess, service action, credential use,
command execution or production launcher. A self-reported 'claimed_reviewed'
field is NOT evidence that any on-host condition has been verified. Both the
incomplete and synthetically completed forms exit nonzero.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SCHEMA = "phase14_b1_lifecycle_restoration_review_v1"
MAX_INPUT_BYTES = 8192

# Ordered to preserve the distinct PRE / DURING / STOP / RESTORE review gates.
GATE_NAMES = (
    "fresh_exact_scope_authorization",
    "independent_operator_and_stop_owner",
    "current_host_service_venv_code_identity",
    "research_zero_money_no_wallet_auto_promotion",
    "exact_candidate_and_rollback_material",
    "independently_evidenced_restoration_rehearsal",
    "external_process_group_cgroup_hard_timeout_design",
    "db_worker_transaction_quiescence_design",
    "bounded_aggregate_only_output_and_zero_sql",
    "same_host_baseline_cpu_rss_queue_writer_p95",
    "baseline_service_feed_and_storage_health",
    "during_run_independent_deadline_and_abort_watch",
    "after_stop_process_tree_quiescent",
    "after_stop_db_workers_transactions_resolved",
    "source_unit_venv_and_expected_restart_restored",
    "after_restore_feed_writer_queue_storage_health",
    "immutable_sanitized_rollback_and_review_receipts",
)
_PROPOSED_SCOPE = {
    "target": "unresolved_bybit_linear_bulk_only",
    "observer_duration_upper_bound_seconds": 120,
    "database_queries_allowed": 0,
    "real_money_operations_allowed": False,
    "live_trading_enabled": False,
    "approved_operator_launcher_available": False,
    "host_operations_permitted_by_this_review": False,
}


def empty_template() -> dict[str, object]:
    """Unfilled fixed-schema review template, not an operations request."""
    return {
        "schema": SCHEMA,
        "proposed_scope": dict(_PROPOSED_SCOPE),
        "gate_claims": dict.fromkeys(GATE_NAMES),
    }


def evaluate_contract(manifest: object) -> dict[str, object]:
    """Accept only exact shape and fixed claim tokens; never trust claims."""
    shape = (
        type(manifest) is dict
        and set(manifest) == {"schema", "proposed_scope", "gate_claims"}
        and manifest["schema"] == SCHEMA
        and type(manifest["proposed_scope"]) is dict
        and set(manifest["proposed_scope"]) == set(_PROPOSED_SCOPE)
        and type(manifest["gate_claims"]) is dict
        and set(manifest["gate_claims"]) == set(GATE_NAMES)
    )
    scope_matches = bool(shape and manifest["proposed_scope"] == _PROPOSED_SCOPE)
    token_valid = bool(
        shape
        and all(
            value is None or value == "claimed_reviewed"
            for value in manifest["gate_claims"].values()
        )
    )
    claimed = (
        [
            key for key in GATE_NAMES
            if manifest["gate_claims"][key] == "claimed_reviewed"
        ]
        if shape and token_valid else []
    )
    complete = bool(shape and scope_matches and token_valid and len(claimed) == len(GATE_NAMES))
    missing = [key for key in GATE_NAMES if key not in claimed]
    return {
        "report": SCHEMA,
        "status": (
            "CLAIMS_FORMAT_COMPLETE_UNVERIFIED_NO_GO"
            if complete else "INCOMPLETE_OR_INVALID_NO_GO"
        ),
        "schema_valid": bool(shape),
        "fixed_scope_matches": scope_matches,
        "allowlisted_claim_tokens_only": token_valid,
        "claimed_gate_count": len(claimed),
        "total_gate_count": len(GATE_NAMES),
        "missing_or_invalid_gate_names": missing,
        "claims_independently_authenticated": False,
        "production_execution_authorized": False,
        "production_deploy_or_restart_permitted": False,
        "operational_candidate_present": False,
        "production_queries_performed": False,
        "actual_process_tree_termination_verified": False,
        "actual_db_worker_quiescence_verified": False,
        "actual_systemd_restoration_verified": False,
        "actual_production_overhead_accepted": False,
        "interpretation": [
            "Claimed review tokens are untrusted and cannot prove independent evidence.",
            "Post-stop and post-restore checks are future obligations, not preflight results.",
            "No one-shot operator launcher or live rollback control is provided.",
            "A separate fresh, explicit, bounded authorization is required for host action.",
        ],
    }


def _bounded_read(path: Path) -> object:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            return None
        return json.loads(raw)
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(evaluate_contract(_bounded_read(args.manifest)), sort_keys=True))
    # "complete" means only that untrusted claim tokens satisfy the syntax.
    # Never return zero or a GO-like exit status to automation.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
