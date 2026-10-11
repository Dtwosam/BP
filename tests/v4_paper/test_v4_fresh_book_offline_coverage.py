from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "report_v4_fresh_book_offline_coverage.py"
RUN = "v4-fresh-book-shadow-fixture"
MODEL = "f" * 64
T0 = datetime(2026, 10, 7, 20, 7, tzinfo=UTC)


def _module():
    existing = sys.modules.get("v4_offline_coverage")
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location("v4_offline_coverage", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _at(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


def _fixture_records() -> list[dict]:
    return [
        {
            "event": "v4_fresh_book_shadow_started",
            "started_at": _at(0),
            "model_sha256": MODEL,
            "source_received_cutoff": "received_at_lte_decision_at",
            "source_retry_policy": "retry_core_source_ineligible_within_max_decision_lag",
            "decision_offset_seconds": 240,
            "frozen_min_edge": "0.05",
            "max_decision_lag_seconds": 2.0,
        },
        {
            "event": "v4_fresh_book_shadow_decision_missed",
            "condition_id": "late-poll",
            "decision_at": _at(60),
            "observed_at": _at(62.6),
            "decision_lag_seconds": 2.6,
            "reason": "decision_lag_exceeded",
        },
        {
            "event": "v4_fresh_book_shadow_decision_missed",
            "condition_id": "late-inference",
            "decision_at": _at(600),
            "observed_at": _at(603.1),
            "decision_lag_seconds": 3.1,
            "reason": "feature_inference_lag_exceeded",
        },
        {
            "event": "v4_fresh_book_shadow_source_ineligible",
            "condition_id": "no-sources",
            "decision_at": _at(900),
            "reason": "core_source_ineligible_retry_window_exhausted",
            "source_ineligible_reasons": [
                "bybit_linear_market_start_missing",
                "bybit_linear_current_missing",
                "coinbase_current_stale",
            ],
            "source_retry_count": 2,
        },
        {
            "event": "v4_source_time_prediction",
            "condition_id": "evaluated",
            "prediction_id": "p1",
            "decision_at": _at(1800),
        },
        {
            "event": "v4_fresh_book_shadow_evaluated",
            "condition_id": "evaluated",
            "prediction_id": "p1",
        },
        {
            "event": "v4_source_time_prediction",
            "condition_id": "no-quote",
            "prediction_id": "p2",
            "decision_at": _at(3600),
        },
        {
            "event": "v4_fresh_book_shadow_quote_unavailable",
            "condition_id": "no-quote",
            "prediction_id": "p2",
        },
        {
            "event": "v4_fresh_book_shadow_completed",
            "completed_at": _at(86401),
            "seen_market_count": 5,
            "prediction_count": 2,
            "decision_missed_count": 2,
            "source_ineligible_count": 1,
            "evaluated_count": 1,
            "quote_unavailable_count": 1,
            "source_retry_deferral_count": 2,
            "source_retry_recovered_count": 0,
            "source_retry_exhausted_count": 1,
            "source_retry_pending_count": 0,
        },
    ]


def _write(tmp_path: Path, records: list[dict]) -> tuple[Path, str]:
    path = tmp_path / f"{RUN}.jsonl"
    payload = "systemd non-json diagnostic line\n" + "\n".join(
        json.dumps(record, sort_keys=True) for record in records
    ) + "\n"
    path.write_text(payload, encoding="utf-8")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _audit(tmp_path: Path, records: list[dict]):
    module = _module()
    path, sha = _write(tmp_path, records)
    return module.audit(
        path, expected_run_id=RUN, expected_sha256=sha, expected_model_sha256=MODEL
    )


def test_exact_hash_bound_offline_funnel_and_missing_reason_overlap(tmp_path: Path) -> None:
    report = _audit(tmp_path, _fixture_records())
    assert report["result"] == "PASS_EVIDENCE_RECONCILIATION_ONLY"
    assert report["counts"] == {
        "seen_markets": 5,
        "predictions": 2,
        "decision_missed": 2,
        "source_ineligible": 1,
        "evaluated": 1,
        "quote_unavailable": 1,
        "prediction_share_of_seen_percent": 40.0,
        "evaluated_share_of_seen_percent": 20.0,
    }
    assert report["missed"]["by_reason"] == {
        "decision_lag_exceeded": 1,
        "feature_inference_lag_exceeded": 1,
    }
    assert report["missed"]["lag_seconds_by_reason"]["decision_lag_exceeded"]["p95"] == 2.6
    assert report["source_ineligible"]["reason_occurrences"] == 3
    assert report["source_ineligible"]["affected_markets_by_venue"] == {
        "bybit_linear": 1,
        "coinbase": 1,
    }
    assert report["source_ineligible"]["affected_venue_count_per_market"] == {"2": 1}
    assert report["source_ineligible"]["retry_terminal_status_counts"] == {
        "retry_exhausted": 1,
    }
    assert report["source_ineligible"]["retry_exhausted_count_distribution"] == {
        "min": 2.0, "median": 2.0, "p95": 2.0, "max": 2.0,
    }
    assert report["source_ineligible"]["retry_terminal_by_venue"] == {
        "bybit_linear": {"retry_exhausted": 1},
        "coinbase": {"retry_exhausted": 1},
    }
    assert report["by_two_hour_decision_window"]["00-02h"] == {
        "decision_missed": 2,
        "prediction": 2,
        "source_ineligible": 1,
    }
    assert report["ignored_non_json_lines"] == 1
    assert report["safety"]["database_connected"] is False
    assert "profit" in report["limitations"][-1]


def test_sha_mismatch_fails_closed(tmp_path: Path) -> None:
    module = _module()
    path, sha = _write(tmp_path, _fixture_records())
    path.write_text(path.read_text() + "another line\n")
    with pytest.raises(module.CoverageEvidenceError, match="SHA-256 mismatch"):
        module.audit(path, expected_run_id=RUN, expected_sha256=sha,
                     expected_model_sha256=MODEL)


def test_parses_same_snapshot_that_was_hash_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    path, expected_sha = _write(tmp_path, _fixture_records())
    original_read_bytes = Path.read_bytes
    original_payload = original_read_bytes(path)
    reads = 0

    def replace_copy_after_read(self: Path) -> bytes:
        nonlocal reads
        result = original_read_bytes(self)
        if self == path:
            reads += 1
            # Simulate offline-file replacement between verification and
            # parsing: the reporter must use the verified byte snapshot.
            path.write_bytes(b'{"event":"corrupted_after_hash"}\n')
        return result

    monkeypatch.setattr(Path, "read_bytes", replace_copy_after_read)
    report = module.audit(
        path, expected_run_id=RUN, expected_sha256=expected_sha,
        expected_model_sha256=MODEL,
    )
    assert reads == 1
    assert original_read_bytes(path) != original_payload
    assert report["counts"]["seen_markets"] == 5
    assert report["evidence_sha256"] == expected_sha


def test_rejects_non_utf8_evidence_after_exact_hash_check(tmp_path: Path) -> None:
    module = _module()
    path, _ = _write(tmp_path, _fixture_records())
    payload = path.read_bytes() + b"\xff\n"
    path.write_bytes(payload)
    verified_sha = hashlib.sha256(payload).hexdigest()
    with pytest.raises(module.CoverageEvidenceError, match="not valid UTF-8"):
        module.audit(
            path, expected_run_id=RUN, expected_sha256=verified_sha,
            expected_model_sha256=MODEL,
        )


def test_missed_breakdown_rejects_unrecognized_reason(tmp_path: Path) -> None:
    records = _fixture_records()
    records[1]["reason"] = "model_was_wrong"
    with pytest.raises(_module().CoverageEvidenceError, match="unknown missed"):
        _audit(tmp_path, records)


def test_ineligible_reason_overlap_rejects_duplicate_flags(tmp_path: Path) -> None:
    records = _fixture_records()
    records[3]["source_ineligible_reasons"] *= 2
    with pytest.raises(_module().CoverageEvidenceError, match="duplicate source"):
        _audit(tmp_path, records)


def test_immediate_vs_exhausted_source_ineligibility_is_reconciled(tmp_path: Path) -> None:
    records = _fixture_records()
    records.insert(4, {
        "event": "v4_fresh_book_shadow_source_ineligible",
        "condition_id": "immediate-source",
        "decision_at": _at(1000),
        "reason": "core_source_ineligible",
        "source_ineligible_reasons": ["bybit_spot_current_stale"],
        "source_retry_count": 0,
    })
    records[-1]["seen_market_count"] += 1
    records[-1]["source_ineligible_count"] += 1
    report = _audit(tmp_path, records)
    assert report["source_ineligible"]["retry_terminal_status_counts"] == {
        "immediate_ineligible": 1,
        "retry_exhausted": 1,
    }
    assert report["source_ineligible"]["retry_terminal_by_venue"] == {
        "bybit_linear": {"retry_exhausted": 1},
        "bybit_spot": {"immediate_ineligible": 1},
        "coinbase": {"retry_exhausted": 1},
    }


def test_rejects_exhausted_terminal_without_prior_retry(tmp_path: Path) -> None:
    records = _fixture_records()
    records[3]["source_retry_count"] = 0
    with pytest.raises(_module().CoverageEvidenceError,
                       match="terminal reason/count mismatch"):
        _audit(tmp_path, records)


def test_rejects_immediate_terminal_with_prior_retries(tmp_path: Path) -> None:
    records = _fixture_records()
    records[3]["reason"] = "core_source_ineligible"
    with pytest.raises(_module().CoverageEvidenceError,
                       match="terminal reason/count mismatch"):
        _audit(tmp_path, records)


def test_rejects_unmatched_exhausted_retry_terminal_count(tmp_path: Path) -> None:
    records = _fixture_records()
    records[-1]["source_retry_exhausted_count"] = 0
    with pytest.raises(_module().CoverageEvidenceError,
                       match="exhausted terminal accounting mismatch"):
        _audit(tmp_path, records)


def test_rejects_unmatched_deferrals_when_recovered_is_zero(tmp_path: Path) -> None:
    records = _fixture_records()
    records[-1]["source_retry_deferral_count"] += 1
    with pytest.raises(_module().CoverageEvidenceError,
                       match="deferral/exhaustion accounting mismatch"):
        _audit(tmp_path, records)


def test_recovered_retry_total_not_inferable_from_terminal_only(tmp_path: Path) -> None:
    records = _fixture_records()
    records[-1]["source_retry_recovered_count"] = 1
    records[-1]["source_retry_deferral_count"] += 1
    report = _audit(tmp_path, records)
    assert report["retry"]["source_retry_recovered_count"] == 1


def test_terminal_accounting_fails_on_unmatched_market_count(tmp_path: Path) -> None:
    records = _fixture_records()
    records[-1]["seen_market_count"] += 1
    with pytest.raises(_module().CoverageEvidenceError, match="accounting mismatch"):
        _audit(tmp_path, records)


def test_missing_prediction_followup_fails(tmp_path: Path) -> None:
    records = [r for r in _fixture_records()
               if r["event"] != "v4_fresh_book_shadow_quote_unavailable"]
    with pytest.raises(_module().CoverageEvidenceError, match="lacks quote/evaluation"):
        _audit(tmp_path, records)


def test_no_double_accounting_missed_or_other_terminal_events(tmp_path: Path) -> None:
    records = _fixture_records()
    records.insert(3, records[1].copy())
    with pytest.raises(_module().CoverageEvidenceError, match="duplicate decision"):
        _audit(tmp_path, records)


def test_wrong_policy_or_wrong_run_fails(tmp_path: Path) -> None:
    records = _fixture_records()
    records[0]["frozen_min_edge"] = "0.075"
    with pytest.raises(_module().CoverageEvidenceError, match="frozen edge"):
        _audit(tmp_path, records)
    path, sha = _write(tmp_path, _fixture_records())
    with pytest.raises(_module().CoverageEvidenceError, match="filename"):
        _module().audit(path, expected_run_id="other-run", expected_sha256=sha,
                        expected_model_sha256=MODEL)


def test_quote_outcome_must_match_exact_prediction_id(tmp_path: Path) -> None:
    records = _fixture_records()
    records[7]["prediction_id"] = "other-prediction"
    with pytest.raises(_module().CoverageEvidenceError, match="prediction_id mismatch"):
        _audit(tmp_path, records)


def test_evaluated_outcome_must_match_exact_prediction_id(tmp_path: Path) -> None:
    records = _fixture_records()
    records[5]["prediction_id"] = "other-prediction"
    with pytest.raises(_module().CoverageEvidenceError, match="prediction_id mismatch"):
        _audit(tmp_path, records)


def test_missing_prediction_id_rejected_on_prediction(tmp_path: Path) -> None:
    records = _fixture_records()
    records[4].pop("prediction_id")
    with pytest.raises(_module().CoverageEvidenceError, match="prediction missing"):
        _audit(tmp_path, records)


def test_missing_prediction_id_rejected_on_quote_or_evaluation(tmp_path: Path) -> None:
    records = _fixture_records()
    records[7].pop("prediction_id")
    with pytest.raises(_module().CoverageEvidenceError, match="outcome missing"):
        _audit(tmp_path, records)


def test_duplicate_prediction_id_across_markets_rejected(tmp_path: Path) -> None:
    records = _fixture_records()
    records[6]["prediction_id"] = "p1"
    records[7]["prediction_id"] = "p1"
    with pytest.raises(_module().CoverageEvidenceError, match="duplicate prediction_id"):
        _audit(tmp_path, records)


def test_report_module_has_no_production_connection_or_order_path() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("sqlalchemy", "psycopg", "create_engine(", "requests.",
                      "httpx", "subprocess", "POLYMARKET_PRIVATE_KEY"):
        assert forbidden not in source
    assert "PHASE14_V4_OFFLINE_COVERAGE=PASS_EVIDENCE_ONLY" in source
