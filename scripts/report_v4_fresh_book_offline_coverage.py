#!/usr/bin/env python3
"""Offline, exact-hash-bound decision-funnel forensics for one frozen V4 shadow.

Reads only the supplied JSONL file; never opens a database or network connection.
A successful report means the evidence was reconciled, not that V4 is profitable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

START = "v4_fresh_book_shadow_started"
COMPLETED = "v4_fresh_book_shadow_completed"
PREDICTION = "v4_source_time_prediction"
MISSED = "v4_fresh_book_shadow_decision_missed"
INELIGIBLE = "v4_fresh_book_shadow_source_ineligible"
EVALUATED = "v4_fresh_book_shadow_evaluated"
QUOTE_MISSING = "v4_fresh_book_shadow_quote_unavailable"
OUTCOMES = {PREDICTION, MISSED, INELIGIBLE}
FOLLOWUPS = {EVALUATED, QUOTE_MISSING}
MISSED_REASONS = {"decision_lag_exceeded", "feature_inference_lag_exceeded"}
VENUES = ("coinbase", "bybit_spot", "bybit_linear")
VALID_FLAGS = {
    f"{venue}_{anchor}_{status}"
    for venue in VENUES
    for anchor in ("market_start", "current")
    for status in ("missing", "stale")
}


class CoverageEvidenceError(ValueError):
    """The supplied file is not the exact, consistent completed shadow."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CoverageEvidenceError(message)


def _utc(record: dict[str, Any], key: str) -> datetime:
    value = record.get(key)
    _require(isinstance(value, str), f"{key} must be an ISO datetime")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CoverageEvidenceError(f"{key} is not an ISO datetime") from exc
    _require(stamp.tzinfo is not None and stamp.utcoffset() is not None,
             f"{key} must have a timezone")
    return stamp


def _count(record: dict[str, Any], key: str) -> int:
    value = record.get(key)
    _require(type(value) is int and value >= 0, f"{key} must be a nonnegative integer")
    return value


def _lag(record: dict[str, Any]) -> float:
    raw = record.get("decision_lag_seconds")
    _require(type(raw) in (int, float) and math.isfinite(raw),
             "missed decision requires finite decision_lag_seconds")
    lag = float(raw)
    _require(lag >= 0, "missed decision lag must be nonnegative")
    observed = _utc(record, "observed_at")
    actual = (observed - _utc(record, "decision_at")).total_seconds()
    _require(abs(actual - lag) < 0.01, "missed decision lag/timestamps disagree")
    return lag


def _distribution(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "min": round(ordered[0], 6),
        "median": round((ordered[(len(ordered) - 1) // 2] +
                         ordered[len(ordered) // 2]) / 2, 6),
        "p95": round(ordered[math.ceil(0.95 * len(ordered)) - 1], 6),
        "max": round(ordered[-1], 6),
    }


def audit(
    path: Path,
    *,
    expected_run_id: str,
    expected_sha256: str,
    expected_model_sha256: str,
) -> dict[str, Any]:
    _require(path.is_file(), "evidence file absent")
    _require(path.name == f"{expected_run_id}.jsonl",
             "evidence filename must match the exact run ID")
    _require(len(expected_sha256) == 64 and len(expected_model_sha256) == 64,
             "expected hashes must be SHA-256 hex")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    _require(digest == expected_sha256.lower(), "evidence SHA-256 mismatch")

    starts: list[dict[str, Any]] = []
    completions: list[dict[str, Any]] = []
    decisions: dict[str, dict[str, Any]] = {}
    followups: dict[str, str] = {}
    missed_lags: dict[str, list[float]] = defaultdict(list)
    missing_reason_occurrences: Counter[str] = Counter()
    affected_markets_by_venue: Counter[str] = Counter()
    overlap_counts: Counter[str] = Counter()
    missed_reasons: Counter[str] = Counter()
    ignored_non_json = 0

    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                ignored_non_json += 1
                continue
            if not isinstance(record, dict):
                continue
            event = record.get("event")
            if event == START:
                starts.append(record)
                continue
            if event == COMPLETED:
                completions.append(record)
                continue
            if event not in OUTCOMES | FOLLOWUPS:
                continue
            condition = record.get("condition_id")
            _require(isinstance(condition, str) and bool(condition),
                     f"invalid condition_id at line {line_number}")
            if event in FOLLOWUPS:
                _require(condition not in followups,
                         f"duplicate quote/evaluation for {condition}")
                followups[condition] = event
                continue
            _require(condition not in decisions, f"duplicate decision for {condition}")
            _utc(record, "decision_at")
            decisions[condition] = record
            if event == MISSED:
                reason = record.get("reason")
                _require(reason in MISSED_REASONS, "unknown missed-decision reason")
                missed_reasons[reason] += 1
                missed_lags[reason].append(_lag(record))
            elif event == INELIGIBLE:
                reasons = record.get("source_ineligible_reasons")
                _require(isinstance(reasons, list) and bool(reasons),
                         "source-ineligible decision lacks reason flags")
                _require(all(isinstance(item, str) and item in VALID_FLAGS
                             for item in reasons), "unknown source reason")
                _require(len(set(reasons)) == len(reasons),
                         "duplicate source reason on one market")
                missing_reason_occurrences.update(reasons)
                affected = {
                    next(venue for venue in VENUES if reason.startswith(venue + "_"))
                    for reason in reasons
                }
                affected_markets_by_venue.update(affected)
                overlap_counts[str(len(affected))] += 1

    _require(len(starts) == 1 and len(completions) == 1,
             "exactly one start and terminal completion required")
    start, complete = starts[0], completions[0]
    _require(start.get("model_sha256") == expected_model_sha256.lower(),
             "frozen model hash mismatch")
    _require(start.get("source_received_cutoff") == "received_at_lte_decision_at",
             "source cutoff policy mismatch")
    _require(start.get("source_retry_policy") ==
             "retry_core_source_ineligible_within_max_decision_lag",
             "source retry policy mismatch")
    _require(start.get("decision_offset_seconds") == 240,
             "frozen decision offset mismatch")
    _require(str(start.get("frozen_min_edge")) == "0.05",
             "frozen edge policy mismatch")
    maximum_lag = start.get("max_decision_lag_seconds")
    _require(type(maximum_lag) in (int, float) and maximum_lag > 0,
             "missing decision deadline")
    for reason, lags in missed_lags.items():
        _require(all(lag > maximum_lag for lag in lags),
                 f"{reason} contains a decision inside allowed deadline")

    start_at, end_at = _utc(start, "started_at"), _utc(complete, "completed_at")
    _require(end_at > start_at, "completion precedes start")
    by_two_hour: dict[str, Counter[str]] = defaultdict(Counter)
    for condition, record in decisions.items():
        at = _utc(record, "decision_at")
        _require(start_at <= at <= end_at, "decision outside shadow time window")
        slot = max(0, int((at - start_at).total_seconds() // 7200))
        bucket = f"{slot * 2:02d}-{(slot + 1) * 2:02d}h"
        event = record["event"]
        label = {MISSED: "decision_missed", INELIGIBLE: "source_ineligible",
                 PREDICTION: "prediction"}[event]
        by_two_hour[bucket][label] += 1
        if event == PREDICTION:
            _require(condition in followups,
                     f"prediction lacks quote/evaluation outcome: {condition}")
        else:
            _require(condition not in followups,
                     "quote/evaluation outcome without prediction")
    _require(set(followups) == {
        condition for condition, record in decisions.items()
        if record["event"] == PREDICTION
    }, "orphaned quote/evaluation outcome")

    seen = len(decisions)
    predictions = sum(item["event"] == PREDICTION for item in decisions.values())
    missed = sum(item["event"] == MISSED for item in decisions.values())
    ineligible = sum(item["event"] == INELIGIBLE for item in decisions.values())
    evaluated = sum(item == EVALUATED for item in followups.values())
    quote_missing = sum(item == QUOTE_MISSING for item in followups.values())
    expected_counts = {
        "seen_market_count": seen,
        "prediction_count": predictions,
        "decision_missed_count": missed,
        "source_ineligible_count": ineligible,
        "evaluated_count": evaluated,
        "quote_unavailable_count": quote_missing,
    }
    for name, observed in expected_counts.items():
        _require(_count(complete, name) == observed,
                 f"completion accounting mismatch: {name}")
    retry = {key: _count(complete, key) for key in (
        "source_retry_deferral_count", "source_retry_recovered_count",
        "source_retry_exhausted_count", "source_retry_pending_count",
    )}
    _require(retry["source_retry_pending_count"] == 0,
             "completed run has pending source retries")
    total_reason_occurrences = sum(missing_reason_occurrences.values())
    return {
        "report": "v4_fresh_book_offline_coverage_forensics_v1",
        "result": "PASS_EVIDENCE_RECONCILIATION_ONLY",
        "run_id": expected_run_id,
        "evidence_sha256": digest,
        "counts": {
            "seen_markets": seen, "predictions": predictions,
            "decision_missed": missed, "source_ineligible": ineligible,
            "evaluated": evaluated, "quote_unavailable": quote_missing,
            "prediction_share_of_seen_percent": round(100 * predictions / seen, 2),
            "evaluated_share_of_seen_percent": round(100 * evaluated / seen, 2),
        },
        "missed": {
            "by_reason": dict(sorted(missed_reasons.items())),
            "lag_seconds_by_reason": {
                reason: _distribution(lags)
                for reason, lags in sorted(missed_lags.items())
            },
        },
        "source_ineligible": {
            "markets": ineligible,
            "reason_occurrences": total_reason_occurrences,
            "by_reason_occurrence": dict(sorted(missing_reason_occurrences.items())),
            "affected_markets_by_venue": dict(sorted(affected_markets_by_venue.items())),
            "affected_venue_count_per_market": dict(sorted(overlap_counts.items())),
            "counts_can_overlap_across_venues": True,
        },
        "by_two_hour_decision_window": {
            key: dict(sorted(counts.items()))
            for key, counts in sorted(by_two_hour.items())
        },
        "retry": retry,
        "ignored_non_json_lines": ignored_non_json,
        "limitations": [
            "Evidence metadata identifies outcome categories, not the root cause of late decisions.",
            "Missing-source reasons do not establish feed outage, arrival latency, or DB visibility.",
            "This does not replay live decisions, query raw source events, or access labels.",
            "Report PASS does not establish profitable paper economics or allow model promotion.",
        ],
        "safety": {
            "evidence_only": True, "database_connected": False,
            "production_queries": False, "orders_submitted": False,
            "holdout_accessed": False, "trading_policy_changed": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-file", type=Path, required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--expected-model-sha256", required=True)
    args = parser.parse_args()
    try:
        report = audit(
            args.evidence_file, expected_run_id=args.expected_run_id,
            expected_sha256=args.expected_sha256,
            expected_model_sha256=args.expected_model_sha256,
        )
    except (CoverageEvidenceError, OSError) as exc:
        parser.exit(1, f"FAIL_COVERAGE_EVIDENCE: {exc}\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    print("PHASE14_V4_OFFLINE_COVERAGE=PASS_EVIDENCE_ONLY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
