"""Offline safety and correctness tests for the bounded Phase 14 V4 audit."""
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bp_engine.features.hashing import canonical_hash

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/audit_phase14_v4_forward_history_readonly.py"
spec = importlib.util.spec_from_file_location("v4_history_audit", SCRIPT)
assert spec is not None and spec.loader is not None
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def _feature() -> dict:
    start = datetime(2026, 10, 11, 10, 0, tzinfo=UTC)
    features = {
        "regime_bull": 1.0,
        "regime_bear": 0.0,
        "regime_sideways_mixed": 0.0,
        "coinbase_return_30s": 0.001,
    }
    missing = {"coinbase_current_missing": False}
    return {
        "horizon_seconds": 300,
        "market_start_at": start,
        "market_end_at": start + timedelta(seconds=300),
        "feature_at": start + timedelta(seconds=60),
        "feature_offset_seconds": 60,
        "features": features,
        "missing_flags": missing,
        "source_cutoffs": {"coinbase_current_state": "2026-10-11T10:00:59Z"},
        "input_fingerprint": "a" * 64,
        "feature_hash": canonical_hash({
            "features": features, "missing_flags": missing
        }),
    }


def test_valid_feature_passes() -> None:
    assert audit.inspect_feature(_feature()) == []


def test_hash_and_geometry_tampering_detected() -> None:
    row = _feature()
    row["features"]["coinbase_return_30s"] = 0.5
    row["feature_offset_seconds"] = 120
    issues = audit.inspect_feature(row)
    assert "feature_hash" in issues
    assert "time_geometry" in issues


def test_future_and_invalid_cutoffs_are_fail_closed() -> None:
    row = _feature()
    row["source_cutoffs"]["coinbase_current_state"] = "2026-10-11T10:01:01Z"
    assert "future_source_cutoff" in audit.inspect_feature(row)
    row["source_cutoffs"]["coinbase_current_state"] = "not-a-time"
    assert "invalid_source_cutoff" in audit.inspect_feature(row)


def test_leakage_regime_and_fingerprint_checks() -> None:
    row = _feature()
    row["features"]["polymarket_recent_price"] = 0.5
    row["features"]["regime_bear"] = 1.0
    row["input_fingerprint"] = "bad"
    row["feature_hash"] = canonical_hash({
        "features": row["features"], "missing_flags": row["missing_flags"],
    })
    issues = audit.inspect_feature(row)
    assert "polymarket_predictor" in issues
    assert "regime_invariant" in issues
    assert "input_fingerprint_format" in issues


def test_market_exactly_four_distinct_offsets() -> None:
    markets = [
        {"condition_id": "one"},
        {"condition_id": "two"},
        {"condition_id": "three"},
    ]
    offsets = [
        {"condition_id": "one", "feature_offset_seconds": v}
        for v in (60, 120, 180, 240)
    ] + [
        {"condition_id": "two", "feature_offset_seconds": v}
        for v in (60, 120, 180)
    ] + [
        {"condition_id": "three", "feature_offset_seconds": v}
        for v in (60, 120, 180, 180, 240)
    ]
    assert [
        row["condition_id"] for row in audit.inspect_market_offsets(markets, offsets)
    ] == ["two", "three"]


def test_read_only_and_work_bounds_are_explicit() -> None:
    source = SCRIPT.read_text()
    assert "default_transaction_read_only=on" in source
    assert "isolation_level=\"AUTOCOMMIT\"" in source
    assert "statement_timeout=3000" in source
    assert "lock_timeout=500" in source
    assert "connect_timeout" in source
    assert "PAGE_SIZE = 64" in source
    assert "MAX_RUNTIME_SECONDS = 900" in source
    assert "DELAY_SECONDS = 0.15" in source
    assert "id > :cursor AND id <= :high" in source
    assert "INPUT_FINGERPRINT_RECOMPUTED=false" in source
    assert "SINGLE_TRANSACTION_SNAPSHOT=false" in source
    for forbidden in (
        "INSERT INTO ", "UPDATE market_features ", "DELETE FROM ",
        "ALTER TABLE ", "DROP TABLE ", "CREATE INDEX ", "VACUUM ",
        "systemctl start", "systemctl stop", "systemctl restart",
    ):
        assert forbidden not in source, forbidden
